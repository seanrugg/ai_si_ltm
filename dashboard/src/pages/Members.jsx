import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import { useApi } from "../api/useApi.js";
import { EmptyState, ErrorNote, HashStrip, Loading, PageHeader, StatusBadge } from "../components/ui.jsx";
import { localTime, relativeTime } from "../lib/format.js";

const PERMISSIONS = [
  { id: "contribute", help: "Write records under its own name" },
  { id: "recall", help: "Read every record" },
  { id: "recall:scoped", help: "Read only the types, members and places you list" },
  { id: "policy", help: "Write policy records (rules other members follow)" },
];
const TYPES = ["communication", "decision", "action", "event", "perception", "lesson", "policy"];

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

function PendingCard({ m, onDone }) {
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
        <p>
          <span className="text-lg font-bold">{m.name}</span>
          <span className="ml-2 text-sm text-paper-muted">{m.kind}{m.operator ? `, run by ${m.operator}` : ""}</span>
        </p>
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

function MemberRow({ m, onDone }) {
  const [mode, setMode] = useState(null); // null | "edit" | "revoke"
  const [reason, setReason] = useState("");
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

  return (
    <li className="border-b border-ink-line px-4 py-3 last:border-b-0">
      <div className="grid grid-cols-1 items-baseline gap-2 md:grid-cols-[12rem_6rem_1fr_7rem_auto]">
        <Link to={`/?agent=${encodeURIComponent(m.name)}`} className="font-bold hover:underline">{m.name}</Link>
        <span><StatusBadge status={m.status} /></span>
        <span className="text-sm text-paper-muted">
          {m.kind}, {m.permissions.join(", ") || "no permissions"}
          {m.scope && <span className="ml-1 text-paper-faint">(scoped)</span>}
        </span>
        <span className="text-sm text-paper-muted" title={localTime(m.last_seen_at)}>
          {m.last_ack_seq} records{m.last_seen_at ? `, seen ${relativeTime(m.last_seen_at)}` : ""}
        </span>
        {m.status === "active" && mode === null && (
          <span className="flex gap-2">
            <button className="btn" onClick={() => setMode("edit")}>Permissions</button>
            <button className="btn btn-danger" onClick={() => setMode("revoke")}>Revoke</button>
          </span>
        )}
      </div>
      <ErrorNote error={error} />
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
    </li>
  );
}

export default function Members() {
  const { data, error, loading, reload } = useApi("/admin/members");
  const [showPast, setShowPast] = useState(false);
  const members = data?.members || [];
  const pending = members.filter((m) => m.status === "pending");
  const active = members.filter((m) => m.status === "active");
  const past = members.filter((m) => m.status === "revoked" || m.status === "rejected");

  return (
    <>
      <PageHeader title="Members" />
      {loading && !data && <Loading />}
      <ErrorNote error={error} onRetry={reload} />
      {data && (
        <>
          {pending.length > 0 && (
            <section className="mb-8" aria-labelledby="pending-h">
              <h2 id="pending-h" className="mb-3 text-lg font-bold">Waiting for approval ({pending.length})</h2>
              <ul className="grid gap-3 xl:grid-cols-2">
                {pending.map((m) => <PendingCard key={m.id} m={m} onDone={reload} />)}
              </ul>
            </section>
          )}
          <section aria-labelledby="active-h">
            <h2 id="active-h" className="mb-3 text-lg font-bold">Active ({active.length})</h2>
            {active.length ? (
              <ul className="panel">{active.map((m) => <MemberRow key={m.id} m={m} onDone={reload} />)}</ul>
            ) : (
              <EmptyState title="No active members">
                A robot or agent asks to join with <code className="hash">python -m cli_agent enroll</code> (or its own
                client). Its request shows up here for you to approve.
              </EmptyState>
            )}
          </section>
          {past.length > 0 && (
            <section className="mt-8">
              <button className="text-sm text-paper-muted underline" onClick={() => setShowPast((s) => !s)} aria-expanded={showPast}>
                {showPast ? "Hide" : "Show"} revoked and rejected ({past.length})
              </button>
              {showPast && <ul className="panel mt-3">{past.map((m) => <MemberRow key={m.id} m={m} onDone={reload} />)}</ul>}
            </section>
          )}
        </>
      )}
    </>
  );
}
