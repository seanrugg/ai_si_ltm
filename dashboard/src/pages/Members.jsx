import { useState } from "react";
import HelpTip from "../components/HelpTip.jsx";
import LocationForm from "../components/LocationForm.jsx";
import { SWARM_MOVE, SWARM_ROLE, MEMORY_WIPE, WIPE_VS_DELETE } from "../lib/help.js";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client.js";
import { useApi } from "../api/useApi.js";
import { EmptyState, ErrorNote, HashStrip, Loading, PageHeader, StatusBadge } from "../components/ui.jsx";
import { localTime, plural, relativeTime } from "../lib/format.js";
import { describeChange, HUB_WARNING, membershipChange } from "../lib/membership.js";

const PERMISSIONS = [
  { id: "contribute", help: "Write records under its own name" },
  { id: "recall", help: "Read every record" },
  { id: "recall:scoped", help: "Read only the types, members and places you list" },
  { id: "policy", help: "Write policy records (rules other members follow)" },
];
const TYPES = ["communication", "thought", "decision", "action", "event", "perception", "lesson", "policy"];

const splitList = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);

function PermissionEditor({ initial, initialScope, onSave, saveLabel, onCancel, busy }) {
  const [perms, setPerms] = useState(new Set(initial));
  const [types, setTypes] = useState(new Set(initialScope?.types || []));
  const [agents, setAgents] = useState((initialScope?.agents || []).join(", "));
  const [places, setPlaces] = useState((initialScope?.places || []).join(", "));
  const scoped = perms.has("recall:scoped") && !perms.has("recall");

  function toggle(set, setter, value) {
    const next = new Set(set);
    next.has(value) ? next.delete(value) : next.add(value);
    setter(next);
  }

  function save() {
    let scope = null;
    if (scoped) {
      scope = { types: [...types] };
      if (agents.trim()) scope.agents = splitList(agents);
      if (places.trim()) scope.places = splitList(places);
    }
    onSave([...perms], scope);
  }

  return (
    <div className="mt-3 rounded-md border border-ink-line bg-ink p-3">
      <fieldset>
        <legend className="mb-2 text-sm font-bold">Permissions to grant</legend>
        {PERMISSIONS.map((p) => (
          <label key={p.id} className="flex items-start gap-2 py-0.5 text-sm">
            <input type="checkbox" className="mt-1 accent-led" checked={perms.has(p.id)}
                   onChange={() => toggle(perms, setPerms, p.id)} />
            <span>
              {p.id} <span className="text-paper-muted">{p.help}</span>
            </span>
          </label>
        ))}
      </fieldset>
      {scoped && (
        <fieldset className="mt-3 border-t border-ink-line pt-3">
          <legend className="mb-2 text-sm font-bold">Scope for recall:scoped</legend>
          <p className="mb-2 text-xs text-paper-muted">Record types it may read. With none ticked it can read nothing.</p>
          <div className="flex flex-wrap gap-x-4">
            {TYPES.map((t) => (
              <label key={t} className="flex items-center gap-1.5 text-sm">
                <input type="checkbox" className="accent-led" checked={types.has(t)} onChange={() => toggle(types, setTypes, t)} />
                {t}
              </label>
            ))}
          </div>
          <label className="mt-2 block text-sm">
            Only these members (comma-separated, blank for all)
            <input className="field mt-1 w-full" value={agents} onChange={(e) => setAgents(e.target.value)} />
          </label>
          <label className="mt-2 block text-sm">
            Only these places (comma-separated, blank for all)
            <input className="field mt-1 w-full" value={places} onChange={(e) => setPlaces(e.target.value)} />
          </label>
        </fieldset>
      )}
      <div className="mt-3 flex gap-2">
        <button className="btn btn-primary" onClick={save} disabled={busy}>{saveLabel}</button>
        {onCancel && <button className="btn" onClick={onCancel}>Cancel</button>}
      </div>
    </div>
  );
}

function PendingCard({ m, selected, onToggle, onDone }) {
  const [mode, setMode] = useState(null); // null | "approve" | "reject"
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function act(path, body) {
    setBusy(true);
    setError(null);
    try {
      await api(`/admin/members/${m.id}/${path}`, { method: "POST", body });
      onDone();
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  return (
    <li className="panel p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex items-start gap-2">
          <label className="flex items-center gap-1.5 cursor-pointer self-start">
            <input type="checkbox" checked={selected} onChange={() => onToggle(m.id)}
                   className="accent-led mt-0.5 h-5 w-5" aria-label={`Select ${m.name}`} />
          </label>
          <div>
            <p>
              <span className="text-lg font-bold">{m.name}</span>
              <span className="ml-2 text-sm text-paper-muted">{m.kind}{m.operator ? `, run by ${m.operator}` : ""}</span>
            </p>
          </div>
        </div>
        <span className="text-xs text-paper-muted" title={localTime(m.created_at)}>
          asked {relativeTime(m.created_at)}
        </span>
      </div>
      <dl className="mt-2 grid grid-cols-[8rem_1fr] gap-y-1 text-sm">
        <dt className="text-paper-muted">Asks for</dt>
        <dd>{m.requested_permissions.join(", ") || "nothing"}</dd>
        <dt className="text-paper-muted">Key fingerprint</dt>
        <dd><HashStrip hash={m.public_key} label={m.public_key.slice(0, 16)} /></dd>
        {m.note && (<><dt className="text-paper-muted">Note</dt><dd>{m.note}</dd></>)}
      </dl>
      <p className="mt-2 text-xs text-paper-faint">
        Check the fingerprint against the key file on the device before approving.
      </p>
      <ErrorNote error={error} />
      {mode === null && (
        <div className="mt-3 flex gap-2">
          <button className="btn btn-primary" onClick={() => setMode("approve")}>Review and approve</button>
          <button className="btn btn-danger" onClick={() => setMode("reject")}>Reject</button>
        </div>
      )}
      {mode === "approve" && (
        <PermissionEditor initial={m.requested_permissions} saveLabel={`Approve ${m.name}`} busy={busy}
                          onCancel={() => setMode(null)}
                          onSave={(permissions, scope) => act("approve", { permissions, scope })} />
      )}
      {mode === "reject" && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input className="field w-72" placeholder="Reason (optional)" value={reason} onChange={(e) => setReason(e.target.value)} />
          <button className="btn btn-danger" disabled={busy} onClick={() => act("reject", { reason: reason || null })}>
            Reject {m.name}
          </button>
          <button className="btn" onClick={() => setMode(null)}>Cancel</button>
        </div>
      )}
    </li>
  );
}

// Join, move, change role or leave in one step (issue #160). An Apply button,
// not change-on-select, so keyboard and screen-reader users can browse the
// options without triggering anything (WCAG 3.2.2).
export function MembershipForm({ name, current, swarms, onDone }) {
  const isHub = current?.role === "hub";
  const [swarm, setSwarm] = useState(current?.swarm ?? "");
  const [role, setRole] = useState(current?.role ?? "member");
  const [hubOk, setHubOk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const target = { swarm: swarm || null, role };
  const { kind, hubWarning } = membershipChange(current ?? null, target);
  const hubSelectable = isHub && swarm === current.swarm;
  const options = (swarms || []).filter((s) => s.status === "active" || s.name === current?.swarm);
  const id = `membership-${name}`;

  function pickSwarm(value) {
    setSwarm(value);
    if (role === "hub" && value !== current?.swarm) setRole("member"); // hub is only "no change"
  }

  async function apply(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (kind === "leave") {
        await api(`/admin/swarms/members/${encodeURIComponent(name)}`, { method: "DELETE" });
      } else {
        await api(`/admin/swarms/${encodeURIComponent(swarm)}/members`, { method: "POST", body: { member: name, role } });
      }
      setHubOk(false);
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={apply} className="mt-2 text-sm" aria-label={`Swarm and role for ${name}`}>
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-xs text-paper-muted" htmlFor={`${id}-swarm`}>
          Swarm
          <select id={`${id}-swarm`} className="field" value={swarm} onChange={(e) => pickSwarm(e.target.value)}
                  aria-label={`Swarm for ${name}`}>
            <option value="">Free agent</option>
            {options.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
          </select>
        </label>
        <HelpTip id={`${id}-swarm-help`} label={`About moving ${name}`}>{SWARM_MOVE}</HelpTip>
        <label className="flex flex-col gap-1 text-xs text-paper-muted" htmlFor={`${id}-role`}>
          Role
          <select id={`${id}-role`} className="field" value={swarm ? role : ""} disabled={!swarm}
                  onChange={(e) => setRole(e.target.value)} aria-label={`Role for ${name}`}>
            {!swarm && <option value="">—</option>}
            {isHub && <option value="hub" disabled={!hubSelectable}>hub</option>}
            <option value="member">member</option>
            <option value="observer">observer</option>
          </select>
        </label>
        <HelpTip id={`${id}-role-help`} label="About roles">{SWARM_ROLE}</HelpTip>
        <button className="btn" disabled={busy || kind === "none" || (hubWarning && !hubOk)}>Apply</button>
      </div>
      <p className="mt-1 min-h-4 text-xs text-paper-muted" aria-live="polite">{describeChange(current ?? null, target)}</p>
      {hubWarning && (
        <div className="mt-1 rounded-md border border-warn/50 p-2 text-xs" role="note">
          <p className="mb-1"><span className="font-bold">Hub:</span> {HUB_WARNING}</p>
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" className="accent-led" checked={hubOk} onChange={(e) => setHubOk(e.target.checked)} />
            I understand {name} will stop relaying for {current.swarm}
          </label>
        </div>
      )}
      <ErrorNote error={error} />
    </form>
  );
}

// Where a robot's AI runs (issue #185), with the source that decided it.
export function cognitionText(c) {
  if (!c || !c.value) return "Unknown";
  const where = c.value === "offboard" ? "Off board" : "On board";
  if (c.source === "admin") return `${where}, set by an admin`;
  if (c.source === "declared") return `${where}, declared by device`;
  if (c.source === "observed") {
    return c.offboard_share == null
      ? `${where}, observed`
      : `${where}, observed: ${Math.round(c.offboard_share * 100)}% of decisions`;
  }
  return where;
}

export function CognitionControl({ m, onDone }) {
  const current = m.cognition?.source === "admin" ? m.cognition.value : "";
  const [value, setValue] = useState(current);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const id = `cognition-${m.id}`;

  async function apply(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api(`/admin/members/${m.id}/cognition`, { method: "PATCH", body: { cognition: value || null } });
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={apply} className="mt-2 flex flex-wrap items-center gap-2 text-sm"
          aria-label={`AI location for ${m.name}`}>
      <span className="text-paper-muted">AI location: <span className="text-paper">{cognitionText(m.cognition)}</span></span>
      <label htmlFor={id} className="sr-only">AI location override for {m.name}</label>
      <select id={id} className="field" value={value} onChange={(e) => setValue(e.target.value)}>
        <option value="">No override</option>
        <option value="onboard">On board</option>
        <option value="offboard">Off board</option>
      </select>
      <button className="btn" disabled={busy || value === current}>Apply</button>
      <ErrorNote error={error} />
    </form>
  );
}

function MemberRow({ m, swarm, swarms, location, onDone }) {
  const [mode, setMode] = useState(null); // null | "edit" | "revoke" | "wipe"
  const [reason, setReason] = useState("");
  const [confirmWipe, setConfirmWipe] = useState("");
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function act(method, path, body) {
    setBusy(true);
    setError(null);
    try {
      await api(`/admin/members/${m.id}/${path}`, { method, body });
      setMode(null);
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  // The typed name is cleared whenever the panel opens or closes, so a wipe
  // always needs the name typed again.
  function showWipe(open) {
    setConfirmWipe("");
    setNotice(null);
    setMode(open ? "wipe" : null);
  }

  async function wipe() {
    setBusy(true);
    setError(null);
    try {
      await api(`/admin/members/${m.id}/wipe`, { method: "POST", body: { confirm: m.name } });
      showWipe(false);
      setNotice(`${m.name}'s memory was wiped.`);
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="border-b border-ink-line px-4 py-3 last:border-b-0">
      <div className="grid grid-cols-1 items-baseline gap-2 md:grid-cols-[12rem_6rem_1fr_7rem_auto]">
        <span className="flex items-baseline gap-2">
          <Link to={`/?agent=${encodeURIComponent(m.name)}`} className="font-bold hover:underline">{m.name}</Link>
          <Link to={`/members/${encodeURIComponent(m.name)}/profile`} className="text-xs text-paper-muted underline hover:text-paper"
                aria-label={`Profile of ${m.name}`}>Profile</Link>
        </span>
        <span><StatusBadge status={m.status} /></span>
        <span className="text-sm text-paper-muted">
          {m.kind}, {m.permissions.join(", ") || "no permissions"}
          {m.scope && <span className="ml-1 text-paper-faint">(scoped)</span>}
          {swarm ? ` · in ${swarm.swarm} as ${swarm.role}` : " · free agent"}
        </span>
        <span className="text-sm text-paper-muted" title={localTime(m.last_seen_at)}>
          {plural(m.last_ack_seq, "record")}{m.last_seen_at ? `, seen ${relativeTime(m.last_seen_at)}` : ""}
        </span>
        {m.status === "active" && mode === null && (
          <span className="flex flex-wrap gap-2 items-center">
            <button className="btn" onClick={() => setMode("edit")}>Permissions</button>
            <button className="btn btn-danger" onClick={() => setMode("revoke")}>Revoke</button>
            <button className="btn btn-danger" onClick={() => showWipe(true)}>Wipe memory</button>
            <HelpTip id={`wipe-memory-help-${m.name}`} label="About wiping memory">{MEMORY_WIPE}</HelpTip>
          </span>
        )}
      </div>
      <ErrorNote error={error} />
      <p className={notice ? "mt-2 text-sm" : "sr-only"} role="status">{notice}</p>
      {m.status === "active" && mode === null && (
        <MembershipForm key={`${swarm?.swarm ?? ""}:${swarm?.role ?? ""}`} name={m.name} current={swarm ?? null}
                        swarms={swarms} onDone={onDone} />
      )}
      {m.status === "active" && mode === null && m.kind === "robot" && (
        <CognitionControl key={`${m.cognition?.source ?? ""}:${m.cognition?.value ?? ""}`} m={m} onDone={onDone} />
      )}
      {m.status === "active" && mode === null && (
        <LocationForm key={JSON.stringify(location)} subjectType="member" name={m.name} current={location} onDone={onDone} />
      )}
      {mode === "edit" && (
        <PermissionEditor initial={m.permissions} initialScope={m.scope} saveLabel="Save permissions" busy={busy}
                          onCancel={() => setMode(null)}
                          onSave={(permissions, scope) => act("PATCH", "permissions", { permissions, scope })} />
      )}
      {mode === "edit" && (
        <p className="mt-2 text-xs text-paper-faint">Saving signs the member out; it logs in again with the new permissions.</p>
      )}
      {mode === "revoke" && (
        <div className="mt-3 rounded-md border border-bad/40 bg-bad/5 p-3 text-sm">
          <p>
            Revoking stops {m.name} from logging in or writing, immediately. Its {m.last_ack_seq} records stay in the
            memory. This can't be undone; the member would have to enroll again with a new key.
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <input className="field w-72" placeholder="Reason (optional)" value={reason} onChange={(e) => setReason(e.target.value)} />
            <button className="btn btn-danger" disabled={busy} onClick={() => act("POST", "revoke", { reason: reason || null })}>
              Revoke {m.name}
            </button>
            <button className="btn" onClick={() => setMode(null)}>Cancel</button>
          </div>
        </div>
      )}
      {m.status === "active" && mode === "wipe" && (
        <div className="mt-3 rounded-md border border-bad/40 bg-bad/5 p-3 text-sm">
          <p>
            <strong>Clean slate. This can't be undone.</strong> This droid stops recalling everything from before now,
            including what other droids recorded. It keeps its name and keeps writing to its chain. Policies still apply.
            The record log and anything already sent to an LRS are kept.
          </p>
          {m.memory_generation > 0 && (
            <p className="mt-2 text-xs text-paper-muted">Wiped {plural(m.memory_generation, "time")}</p>
          )}
          <label htmlFor={`wipe-confirm-${m.id}`} className="mt-2 block text-xs text-paper-muted">
            Confirm by typing {m.name}
          </label>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <input id={`wipe-confirm-${m.id}`} className="field w-72" value={confirmWipe} autoComplete="off"
                   onChange={(e) => setConfirmWipe(e.target.value)} />
            <HelpTip id={`wipe-vs-delete-${m.name}`} label="Wipe vs delete">{WIPE_VS_DELETE}</HelpTip>
            <button className="btn btn-danger" disabled={busy || confirmWipe !== m.name} onClick={wipe}>
              Wipe {m.name}'s memory
            </button>
            <button className="btn" onClick={() => showWipe(false)}>Cancel</button>
          </div>
          <p className="mt-2 text-xs text-paper-muted">
            To keep a copy first, use <Link to="/export" className="underline">Export</Link>.
          </p>
        </div>
      )}
    </li>
  );
}

// Pure helper: filter the active members by the Show selection.
// `show` is "all", "free", or "swarm:<name>". An unknown swarm returns none.
export function filterMembers(active, swarmOf, show) {
  if (!show || show === "all") return active;
  if (show === "free") return active.filter((m) => !swarmOf.has(m.name));
  if (show.startsWith("swarm:")) {
    const name = show.slice("swarm:".length);
    return active.filter((m) => {
      const s = swarmOf.get(m.name);
      return s && s.swarm === name;
    });
  }
  return [];
}

export default function Members() {
  const { data, error, loading, reload } = useApi("/admin/members");
  const { data: swarmsData } = useApi("/admin/swarms");
  const locations = useApi("/admin/locations");
  const declaredOf = (name) => (locations.data?.members || []).find((l) => l.name === name)?.all
    ?.find((e) => e.source === "declared" && !e.inherited_from) ?? null;
  const [showPast, setShowPast] = useState(false);
  const [params, setParams] = useSearchParams();
  const [selected, setSelected] = useState(new Set());
  const [bulkMode, setBulkMode] = useState(null); // null | "approve" | "reject"
  const [rejectReason, setRejectReason] = useState("");
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkError, setBulkError] = useState(null);
  const [bulkResults, setBulkResults] = useState(null);

  const members = data?.members || [];
  const swarmOf = new Map();
  for (const s of (swarmsData || [])) {
    for (const m of (s.members || [])) swarmOf.set(m.name, { swarm: s.name, role: m.role });
  }
  const pending = members.filter((m) => m.status === "pending");
  const active = members.filter((m) => m.status === "active");
  const past = members.filter((m) => m.status === "revoked" || m.status === "rejected");
  const freeAgents = active.filter((m) => !swarmOf.has(m.name));
  const pendingById = new Map(pending.map((m) => [m.id, m]));
  // Only ids still pending count: a card approved on its own drops out.
  const selectedIds = [...selected].filter((id) => pendingById.has(id));

  const show = params.get("show") || "all";
  const list = filterMembers(active, swarmOf, show);

  function swarmCount(name) {
    return active.filter((m) => {
      const s = swarmOf.get(m.name);
      return s && s.swarm === name;
    }).length;
  }

  let heading, emptyTitle, emptyBody;
  if (show === "all") {
    heading = `Active (${active.length})`;
    emptyTitle = "No active members";
    emptyBody = (
      <>
        A robot or agent asks to join with <code className="hash">python -m cli_agent enroll</code> (or its own
        client). Its request shows up here for you to approve.
      </>
    );
  } else if (show === "free") {
    heading = `Free agents (${freeAgents.length})`;
    emptyTitle = "No free agents";
    emptyBody = "Every active member is in a swarm.";
  } else {
    const name = show.slice("swarm:".length);
    heading = `${name} (${list.length})`;
    emptyTitle = `No active members in ${name}`;
    emptyBody = null;
  }

  function onShow(value) {
    setParams({ show: value });
  }

  function toggleSelection(id) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    const all = pending.length > 0 && pending.every((m) => selected.has(m.id));
    setSelected(all ? new Set() : new Set(pending.map((m) => m.id)));
  }

  function cancelBulk() {
    setBulkMode(null);
    setBulkError(null);
    setRejectReason("");
  }

  async function doBulk() {
    setBulkError(null);
    const ids = selectedIds;
    // Names captured now: after the reload these members are no longer pending.
    const names = new Map(ids.map((id) => [id, pendingById.get(id)?.name ?? id]));
    setBulkBusy(true);
    try {
      const { results } = await api("/admin/members/bulk", {
        method: "POST",
        body: { action: bulkMode, ids, reason: bulkMode === "reject" ? (rejectReason.trim() || null) : null },
      });
      setBulkResults({ action: bulkMode, rows: results.map((r) => ({ ...r, name: names.get(r.id) })) });
      setSelected(new Set());
      setBulkMode(null);
      setRejectReason("");
      reload();
    } catch (err) {
      setBulkError(err);
    } finally {
      setBulkBusy(false);
    }
  }

  return (
    <>
      <PageHeader title="Members" />
      {loading && !data && <Loading />}
      <ErrorNote error={error} onRetry={reload} />
      {data && (
        <>
          {bulkResults && (
            <div className="mb-6 text-sm" role="status">
              <p className="mb-1 font-bold text-paper-muted">Results</p>
              <ul>
                {bulkResults.rows.map((r) => (
                  <li key={r.id}>
                    {r.name}: {r.ok
                      ? (bulkResults.action === "approve" ? "approved" : "rejected")
                      : <>not changed (<span className="font-mono">{r.error}</span>)</>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {pending.length > 0 && (
            <section className="mb-8" aria-labelledby="pending-h">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <h2 id="pending-h" className="mb-0 text-lg font-bold">Waiting for approval ({pending.length})</h2>
                <div className="flex flex-wrap items-center gap-2">
                  <label className="flex items-center gap-1.5 text-sm cursor-pointer">
                    <input type="checkbox"
                           checked={selectedIds.length === pending.length && pending.length > 0}
                           onChange={toggleAll} className="accent-led" />
                    Select all
                  </label>
                  {bulkMode === null && selectedIds.length > 0 && (
                    <>
                      <button className="btn btn-primary" onClick={() => setBulkMode("approve")}>
                        Approve selected ({selectedIds.length})
                      </button>
                      <button className="btn btn-danger" onClick={() => setBulkMode("reject")}>
                        Reject selected ({selectedIds.length})
                      </button>
                    </>
                  )}
                </div>
              </div>
              <ul className="grid gap-3 xl:grid-cols-2">
                {pending.map((m) => <PendingCard key={m.id} m={m} selected={selected.has(m.id)}
                  onToggle={toggleSelection} onDone={reload} />)}
              </ul>
              {bulkMode && (
                <div className="mt-4 rounded-md border border-ink-line bg-ink p-4" role="group"
                     aria-labelledby="bulk-confirm-h">
                  <p id="bulk-confirm-h" className="mb-2 text-sm font-bold">
                    {bulkMode === "approve"
                      ? "Approve these members? Each gets exactly the permissions it asked for."
                      : "Reject these members?"}
                  </p>
                  <p className="mb-2 text-xs text-paper-muted">Check each fingerprint against the device&apos;s key file.</p>
                  <ul className="mb-3 text-sm space-y-1">
                    {selectedIds.map((id) => {
                      const m = pendingById.get(id);
                      return (
                        <li key={id} className="flex flex-wrap items-center gap-2">
                          <span className="font-bold">{m.name}</span>
                          <HashStrip hash={m.public_key} label={m.public_key.slice(0, 16)} />
                          <span className="text-xs text-paper-muted">asks for {m.requested_permissions?.join(", ") || "nothing"}</span>
                        </li>
                      );
                    })}
                  </ul>
                  {bulkMode === "reject" && (
                    <div className="mb-3">
                      <label htmlFor="bulk-reason" className="block text-sm text-paper-muted mb-1">Reason (optional)</label>
                      <input id="bulk-reason" className="field h-11 w-full" value={rejectReason} maxLength={500}
                             onChange={(e) => setRejectReason(e.target.value)}
                             placeholder="Why are you rejecting these requests?" />
                    </div>
                  )}
                  <div className="flex flex-wrap gap-2">
                    <button className={`btn ${bulkMode === "approve" ? "btn-primary" : "btn-danger"} h-11`}
                            disabled={bulkBusy} onClick={doBulk}>
                      {bulkMode === "approve" ? "Approve" : "Reject"} ({selectedIds.length})
                    </button>
                    <button className="btn h-11" type="button" onClick={cancelBulk}>Cancel</button>
                  </div>
                  {bulkError && <ErrorNote error={bulkError} />}
                </div>
              )}
            </section>
          )}
          <section aria-labelledby="active-h">
            <h2 id="active-h" className="mb-3 text-lg font-bold">{heading}</h2>
            <label htmlFor="member-show" className="mb-2 block text-sm font-bold text-paper-muted">
              Show
            </label>
            <select
              id="member-show"
              value={show}
              onChange={(e) => onShow(e.target.value)}
              className="field"
            >
              <option value="all">All active ({active.length})</option>
              <option value="free">Free agents ({freeAgents.length})</option>
              {(swarmsData || []).map((s) => (
                <option key={s.name} value={`swarm:${s.name}`}>
                  {s.name} ({swarmCount(s.name)})
                </option>
              ))}
            </select>
            {list.length ? (
              <ul className="panel mt-3">{list.map((m) => <MemberRow key={m.id} m={m} swarm={swarmOf.get(m.name)} swarms={swarmsData}
                                                      location={declaredOf(m.name)} onDone={() => { reload(); locations.reload(); }} />)}</ul>
            ) : (
              <EmptyState title={emptyTitle}>{emptyBody}</EmptyState>
            )}
          </section>
          {past.length > 0 && (
            <section className="mt-8">
              <button className="text-sm text-paper-muted underline" onClick={() => setShowPast((s) => !s)} aria-expanded={showPast}>
                {showPast ? "Hide" : "Show"} revoked and rejected ({past.length})
              </button>
              {showPast && <ul className="panel mt-3">{past.map((m) => <MemberRow key={m.id} m={m} swarm={swarmOf.get(m.name)} onDone={reload} />)}</ul>}
            </section>
          )}
        </>
      )}
    </>
  );
}
