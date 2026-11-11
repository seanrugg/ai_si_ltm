import { useEffect, useRef, useState } from "react";
import HelpTip from "../components/HelpTip.jsx";
import LocationForm from "../components/LocationForm.jsx";
import RemoveSwarmDialog from "../components/RemoveSwarmDialog.jsx";
import { ADD_SWARM, MEMORY_WIPE, ROOT_PRIVATE_KEY, ROOT_PUBLIC_KEY, SWARM_ROLE, WIPE_VS_DELETE } from "../lib/help.js";
import RenameSwarm from "../components/RenameSwarm.jsx";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import { useApi } from "../api/useApi.js";
import { plural } from "../lib/format.js";
import { membershipChange } from "../lib/membership.js";
import { generateRootKey, signAppointment, SwarmKeyError } from "../lib/swarmKey.js";
import { EmptyState, ErrorNote, Loading, PageHeader, StatusBadge } from "../components/ui.jsx";

const ROLES = ["member", "observer"];

// The one-time panel for a new root key. It traps focus and can't be dismissed
// until the admin confirms the key is saved; then the key string is dropped.
export function KeyOnceDialog({ swarmName, swarmId, keyString, onDone }) {
  const ref = useRef(null);
  const [saved, setSaved] = useState(false);
  const [copyStatus, setCopyStatus] = useState("");

  useEffect(() => {
    const node = ref.current;
    node?.querySelector("[data-autofocus]")?.focus();
    function onKey(e) {
      if (e.key === "Escape") {
        e.preventDefault(); // closing needs the confirmation
        return;
      }
      if (e.key !== "Tab" || !node) return;
      const items = [...node.querySelectorAll("button:not([disabled]), input:not([disabled]), [tabindex='0']")];
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
    function onUnload(e) {
      e.preventDefault();
      e.returnValue = ""; // the key can't be shown again
    }
    document.addEventListener("keydown", onKey);
    window.addEventListener("beforeunload", onUnload);
    return () => {
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("beforeunload", onUnload);
    };
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(keyString);
      setCopyStatus("Copied to the clipboard.");
    } catch {
      setCopyStatus("Copy didn't work in this browser. Select the key and copy it by hand.");
    }
  }

  function download() {
    const text = `${keyString}\n\nswarm: ${swarmName}\nswarm id: ${swarmId}\n` +
      "This is the swarm's root key. Keep it offline or in a password manager.\n";
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${swarmName}.swarm-key.txt`;
    a.click();
    URL.revokeObjectURL(url);
    setCopyStatus(`Downloaded ${swarmName}.swarm-key.txt.`);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/80 p-4">
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby="key-once-h" aria-describedby="key-once-d"
           className="panel w-full max-w-xl p-5">
        <h2 id="key-once-h" className="mb-2 text-lg font-bold" tabIndex={-1} data-autofocus>
          Root key for {swarmName}
        </h2>
        <p id="key-once-d" className="mb-3 text-sm text-paper-muted">
          This is the swarm&apos;s root key. LTMS does not keep it and cannot show it again. Store it in your
          password manager. Anyone with it can appoint this swarm&apos;s hub.
        </p>
        <div className="mb-1 flex items-center gap-2 text-sm font-bold">
          Private root key
          <HelpTip id="root-private-key" label="About the private root key">{ROOT_PRIVATE_KEY}</HelpTip>
        </div>
        <p className="mb-3 break-all rounded-md border border-led/50 bg-ink p-3 font-mono text-sm select-all"
           aria-label="Root key">
          {keyString}
        </p>
        <div className="mb-3 flex flex-wrap items-center gap-2 text-sm text-paper-muted">
          <span>Swarm ID <span className="break-all font-mono text-paper">{swarmId}</span></span>
          <HelpTip id="root-public-key" label="About the public root key">{ROOT_PUBLIC_KEY}</HelpTip>
        </div>
        <div className="mb-2 flex flex-wrap gap-2">
          <button type="button" className="btn btn-primary" onClick={copy}>Copy</button>
          <button type="button" className="btn" onClick={download}>Download (.txt)</button>
        </div>
        <p className="mb-3 min-h-5 text-sm text-ok" role="status" aria-live="polite">{copyStatus}</p>
        <p className="mb-2 text-sm text-warn">
          Copy this key now and store it offline. It is not shown again. Without it you can&apos;t appoint or replace
          this swarm&apos;s hub.
        </p>
        <label className="mb-3 flex items-center gap-2 text-sm cursor-pointer">
          <input type="checkbox" className="accent-led" checked={saved} onChange={(e) => setSaved(e.target.checked)} />
          I&apos;ve saved the key
        </label>
        <button type="button" className="btn" disabled={!saved} onClick={onDone}>Done</button>
      </div>
    </div>
  );
}

function CreateSwarm({ onCreated }) {
  const [mode, setMode] = useState("generate"); // "generate" | "existing"
  const [name, setName] = useState("");
  const [publicKey, setPublicKey] = useState("");
  const [description, setDescription] = useState("");
  const [home, setHome] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [shown, setShown] = useState(null); // {swarmName, swarmId, keyString}, while the one-time panel is open

  function reset() {
    setName("");
    setPublicKey("");
    setDescription("");
    setHome(false);
  }

  async function register(pub) {
    return api("/admin/swarms", {
      method: "POST",
      body: { name: name.trim(), public_key: pub, description: description.trim() || null, home },
    });
  }

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "generate") {
        const key = generateRootKey();
        const keyString = key.keyString;
        key.seed.fill(0);
        const created = await register(key.publicKey);
        setShown({ swarmName: created.name, swarmId: created.id, keyString });
      } else {
        await register(publicKey.trim());
        onCreated();
      }
      reset();
    } catch (err) {
      setError(err); // a key generated for a failed registration is simply dropped
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mb-8" aria-labelledby="create-h">
      <h2 id="create-h" className="mb-3 text-lg font-bold">Create a swarm</h2>
      <p className="mb-3 text-sm text-paper-muted">
        {mode === "generate"
          ? "Your browser creates the swarm's root key and shows it to you once. Only its public key is sent to LTMS."
          : "Register a swarm whose root key you made with the command line. Only its public key is sent."}
      </p>
      <form onSubmit={submit} className="mb-2 flex flex-wrap items-end gap-2" aria-label="Create swarm"
            autoComplete="off">
        <input className="field w-40" value={name} onChange={(e) => setName(e.target.value)}
               placeholder="Swarm name (echo-base)" aria-label="Swarm name" required />
        {mode === "existing" && (
          <input className="field w-64" value={publicKey} onChange={(e) => setPublicKey(e.target.value)}
                 placeholder="Root public key, 64 hex" aria-label="Public key" required />
        )}
        <input className="field w-64" value={description} onChange={(e) => setDescription(e.target.value)}
               placeholder="Description" aria-label="Description" />
        <label className="flex items-center gap-2 text-sm text-paper-muted cursor-pointer">
          <input type="checkbox" checked={home} onChange={(e) => setHome(e.target.checked)} className="accent-led" />
          Make this the home swarm (only one active home at a time)
        </label>
        <button className="btn btn-primary" disabled={busy}>
          {mode === "generate" ? "Add swarm" : "Register"}
        </button>
        {mode === "generate" && <HelpTip id="add-swarm" label="About adding a swarm">{ADD_SWARM}</HelpTip>}
      </form>
      <button type="button" className="mb-2 text-sm text-paper-muted underline"
              onClick={() => setMode(mode === "generate" ? "existing" : "generate")}>
        {mode === "generate" ? "Already have a key? Register its public key" : "Generate a new key in the browser instead"}
      </button>
      <ErrorNote error={error} />
      {shown && (
        <KeyOnceDialog {...shown} onDone={() => { setShown(null); onCreated(); }} />
      )}
    </section>
  );
}

// Sign a hub appointment in the browser with a pasted root key. Only
// {statement, sig} is sent; the key input is cleared after submit or cancel.
function AppointHub({ swarm, allMembers, busy, act }) {
  const candidates = (swarm.members || [])
    .filter((m) => m.role === "member")
    .map((m) => allMembers.find((a) => a.name === m.name))
    .filter(Boolean);
  const currentHub = (swarm.members || []).find((m) => m.role === "hub");
  const [hub, setHub] = useState("");
  const [keyText, setKeyText] = useState("");
  const [localError, setLocalError] = useState(null);
  const [signedJson, setSignedJson] = useState("");

  function clear() {
    setKeyText("");
    setLocalError(null);
  }

  async function sign(e) {
    e.preventDefault();
    setLocalError(null);
    const member = candidates.find((m) => m.name === hub);
    let body;
    try {
      body = signAppointment(keyText, { swarm, hub, hubKey: member?.public_key });
    } catch (err) {
      setKeyText("");
      setLocalError(err instanceof SwarmKeyError ? err : new Error("Couldn't sign the appointment."));
      return;
    }
    setKeyText("");
    await act(() => api(`/admin/swarms/${encodeURIComponent(swarm.name)}/hub`, { method: "POST", body }));
    setHub("");
  }

  return (
    <div className="mb-4">
      <h3 className="mb-2 text-sm font-bold text-paper-muted">Appoint hub</h3>
      {currentHub && <p className="mb-2 text-xs text-paper-muted">Current hub: {currentHub.name}.</p>}
      {candidates.length === 0 ? (
        <p className="mb-2 text-xs text-paper-muted">
          {currentHub
            ? "To change the hub, add the new hub to this swarm as a member first."
            : "Add a member to this swarm first; the hub is chosen from its members."}
        </p>
      ) : (
        <form onSubmit={sign} className="mb-2 flex flex-wrap items-end gap-2" autoComplete="off"
              aria-label={`Appoint hub for ${swarm.name}`}>
          <label className="flex flex-col gap-1 text-xs text-paper-muted">
            Hub
            <select className="field" value={hub} onChange={(e) => setHub(e.target.value)} required>
              <option value="">Choose a member</option>
              {candidates.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-paper-muted">
            Root key (signed here; never sent)
            <input className="field w-72 font-mono" type="password" name="swarm-root-key"
                   autoComplete="new-password" spellCheck={false} data-1p-ignore data-lpignore="true"
                   value={keyText} onChange={(e) => setKeyText(e.target.value)}
                   placeholder="ltms-swarm-key-v1:…" required />
          </label>
          <button className="btn btn-primary" disabled={busy || !hub || !keyText}>Sign and appoint</button>
          <button type="button" className="btn" onClick={clear} disabled={!keyText && !localError}>Cancel</button>
        </form>
      )}
      {localError && <p className="mb-2 text-sm text-bad" role="alert">{localError.message}</p>}
      <details className="text-xs text-paper-muted">
        <summary className="cursor-pointer">Have an appointment signed with the command line?</summary>
        <textarea className="field mt-2 w-full mb-2" value={signedJson} onChange={(e) => setSignedJson(e.target.value)}
                  placeholder="Paste the JSON from python -m scripts.swarm_key appoint-hub" rows={4}
                  aria-label="Signed hub appointment" />
        <button className="btn" disabled={busy || !signedJson.trim()}
                onClick={() => act(async () => {
                  let body;
                  try {
                    body = JSON.parse(signedJson);
                  } catch {
                    throw new Error("That isn't valid JSON.");
                  }
                  await api(`/admin/swarms/${encodeURIComponent(swarm.name)}/hub`, { method: "POST", body });
                  setSignedJson("");
                })}>
          Appoint hub
        </button>
      </details>
    </div>
  );
}

// A member or observer's role, changed in one step with Apply (issue #160).
function RoleControl({ swarm, member, busy, act }) {
  const [role, setRole] = useState(member.role);
  const { kind } = membershipChange({ swarm: swarm.name, role: member.role }, { swarm: swarm.name, role });
  return (
    <form className="flex items-center gap-2" aria-label={`Change role for ${member.name}`}
          onSubmit={(e) => {
            e.preventDefault();
            act(() => api(`/admin/swarms/${encodeURIComponent(swarm.name)}/members`,
              { method: "POST", body: { member: member.name, role } }));
          }}>
      <select className="field text-xs" value={role} onChange={(e) => setRole(e.target.value)}
              aria-label={`Role for ${member.name}`}>
        <option value="member">member</option>
        <option value="observer">observer</option>
      </select>
      <button className="btn text-xs" disabled={busy || kind === "none"}>Apply</button>
    </form>
  );
}

function SwarmCard({ swarm, swarms, allMembers, location, onReload }) {
  const members = swarm.members || [];
  const revoked = swarm.status === "revoked";
  // allMembers is already filtered to active, so this counts who the wipe would reach.
  const activeNames = new Set(allMembers.map((m) => m.name));
  const wipeCount = members.filter((m) => activeNames.has(m.name)).length;
  // Where each member is now, so "Add a member" can say it's a move and leave
  // other swarms' hubs out (a hub moves from its member row, with a warning).
  const placement = new Map();
  for (const s of swarms || []) {
    if (s.name === swarm.name) continue;
    for (const m of s.members || []) placement.set(m.name, { swarm: s.name, role: m.role });
  }
  const eligible = allMembers.filter((m) => m.status === "active" && !members.some((s) => s.name === m.name)
    && placement.get(m.name)?.role !== "hub");
  const [addName, setAddName] = useState("");
  const [addRole, setAddRole] = useState("member");
  const [confirmRevoke, setConfirmRevoke] = useState("");
  const [wipeOpen, setWipeOpen] = useState(false);
  const [confirmWipe, setConfirmWipe] = useState("");
  const [wipeNotice, setWipeNotice] = useState(null);
  const [removeOpen, setRemoveOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function act(fn) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onReload();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function addMember() {
    return api(`/admin/swarms/${encodeURIComponent(swarm.name)}/members`, {
      method: "POST", body: { member: addName, role: addRole },
    });
  }

  async function removeMember(memberName) {
    return api(`/admin/swarms/members/${encodeURIComponent(memberName)}`, { method: "DELETE" });
  }

  async function revokeSwarm() {
    return api(`/admin/swarms/${encodeURIComponent(swarm.name)}/revoke`, { method: "POST" });
  }

  // The typed name is cleared whenever the panel opens or closes, so a wipe
  // always needs the name typed again.
  function showWipe(open) {
    setConfirmWipe("");
    setWipeNotice(null);
    setWipeOpen(open);
  }

  async function wipeSwarmMemory() {
    const res = await api(`/admin/swarms/${encodeURIComponent(swarm.name)}/wipe`, {
      method: "POST", body: { confirm: swarm.name },
    });
    showWipe(false);
    setWipeNotice(`Memory wiped for ${plural(res?.wiped?.length ?? wipeCount, "member")} of ${swarm.name}.`);
  }

  return (
    <section className="mb-8 border rounded-md border-ink-line bg-ink p-4" aria-labelledby={`swarm-${swarm.name}-h`}>
      <h2 id={`swarm-${swarm.name}-h`} className="mb-2 text-lg font-bold">
        <Link to={`/members?show=${encodeURIComponent(`swarm:${swarm.name}`)}`}>{swarm.name}</Link>
        <Link to={`/swarms/${encodeURIComponent(swarm.name)}/profile`}
              className="ml-3 text-sm font-normal text-paper-muted underline hover:text-paper"
              aria-label={`Profile of ${swarm.name}`}>Profile</Link>
      </h2>
      <RenameSwarm name={swarm.name} onDone={onReload} />
      <LocationForm key={JSON.stringify(location)} subjectType="swarm" name={swarm.name} current={location}
                    title="Home location" onDone={onReload} />
      <p className="mb-3 text-xs text-paper-muted">
        <StatusBadge status={swarm.status} /> {swarm.home && <span className="ml-2">home swarm</span>}
        {swarm.description && <span className="ml-2">— {swarm.description}</span>}
      </p>

      <h3 className="mb-2 text-sm font-bold text-paper-muted">Members</h3>
      {members.length === 0 && <p className="mb-4 text-xs text-paper-muted">No members yet.</p>}
      {["hub", "member", "observer"].map((role) => {
        const roleMembers = members.filter((m) => m.role === role);
        if (roleMembers.length === 0) return null;
        return (
          <section key={role} className="mb-3">
            <h4 className="mb-2 text-xs text-paper-muted">{plural(roleMembers.length, role)}</h4>
            <ul className="panel" aria-label={`${role} members of ${swarm.name}`}>
              {roleMembers.map((m) => (
                <li key={m.name} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                  <span className="text-sm">{m.name}</span>
                  {!revoked && (
                    <span className="flex flex-wrap items-center gap-2">
                      {m.role !== "hub" && (
                        <RoleControl key={m.role} swarm={swarm} member={m} busy={busy} act={act} />
                      )}
                      <button className="btn btn-danger text-xs" onClick={() => act(() => removeMember(m.name))}
                              aria-label={`Remove ${m.name}`}>
                        Remove
                      </button>
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </section>
        );
      })}

      {!revoked && (
        <>
          <form onSubmit={(e) => { e.preventDefault(); act(addMember); }}
                className="mb-4 flex flex-wrap items-end gap-2" aria-label="Add member">
            <select className="field" value={addName} onChange={(e) => setAddName(e.target.value)}
                    aria-label="Member">
              <option value="">Add a member</option>
              {eligible.map((m) => {
                const from = placement.get(m.name);
                return (
                  <option key={m.name} value={m.name}>
                    {from ? `${m.name} (moves from ${from.swarm})` : m.name}
                  </option>
                );
              })}
            </select>
            <select className="field w-24" value={addRole} onChange={(e) => setAddRole(e.target.value)}
                    aria-label="Role">
              {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <HelpTip id={`add-role-help-${swarm.name}`} label="About roles">{SWARM_ROLE}</HelpTip>
            <button className="btn btn-primary" disabled={busy || !addName}>Add</button>
          </form>

          <AppointHub swarm={swarm} allMembers={allMembers} busy={busy} act={act} />

          <div className="rounded-md border border-bad/40 bg-bad/5 p-3">
            <h3 className="mb-2 text-sm font-bold text-paper-muted">Revoke swarm</h3>
            <p className="mb-2 text-xs text-paper-muted">
              Makes every member a free agent and stops the hub from relaying. Type the swarm's name to confirm.
            </p>
            <input className="field w-40 mb-2" value={confirmRevoke}
                   onChange={(e) => setConfirmRevoke(e.target.value)} placeholder={swarm.name} aria-label="Confirm revoke" />
            <button className="btn btn-danger" onClick={() => act(revokeSwarm)}
                    disabled={busy || confirmRevoke !== swarm.name}>
              Revoke {swarm.name}
            </button>
          </div>

          <div className="mt-2">
            <button className="btn btn-danger" onClick={() => showWipe(true)}>Wipe swarm memory</button>
            <HelpTip id={`wipe-swarm-help-${swarm.name}`} label="About wiping swarm memory">{MEMORY_WIPE}</HelpTip>
          </div>
          <div className="mt-2">
            <button type="button" className="btn btn-danger" onClick={() => setRemoveOpen(true)}>Remove swarm</button>
          </div>
          {removeOpen && (
            <RemoveSwarmDialog name={swarm.name} onClose={() => setRemoveOpen(false)} onRemoved={onReload} />
          )}
          <p className={wipeNotice ? "mt-2 text-sm" : "sr-only"} role="status">{wipeNotice}</p>
          {wipeOpen && (
            <div className="mt-3 rounded-md border border-bad/40 bg-bad/5 p-3 text-sm">
              <p>
                <strong>Clean slate. This can't be undone.</strong> These droids stop recalling everything from before
                now, including what other droids recorded. They keep their names and keep writing to their chains.
                Policies still apply. The record log and anything already sent to an LRS are kept.
              </p>
              <p className="mt-2 text-xs text-paper-muted">
                {wipeCount > 0
                  ? `Wipes memory for ${plural(wipeCount, "active member")}.`
                  : "This swarm has no active members to wipe."}
              </p>
              <label htmlFor={`wipe-swarm-confirm-${swarm.name}`} className="mt-2 block text-xs text-paper-muted">
                Confirm by typing {swarm.name}
              </label>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <input id={`wipe-swarm-confirm-${swarm.name}`} className="field w-40" value={confirmWipe}
                       autoComplete="off" onChange={(e) => setConfirmWipe(e.target.value)} />
                <HelpTip id={`wipe-swarm-vs-delete-${swarm.name}`} label="Wipe vs delete">{WIPE_VS_DELETE}</HelpTip>
                <button className="btn btn-danger" onClick={() => act(wipeSwarmMemory)}
                        disabled={busy || wipeCount === 0 || confirmWipe !== swarm.name}>
                  Wipe {plural(wipeCount, "active member")}
                </button>
                <button className="btn" onClick={() => showWipe(false)}>Cancel</button>
              </div>
              <p className="mt-2 text-xs text-paper-muted">
                To keep a copy first, use <Link to="/export" className="underline">Export</Link>.
              </p>
            </div>
          )}
        </>
      )}

      <ErrorNote error={error} />
    </section>
  );
}

export default function Swarms() {
  const { data, error, loading, reload } = useApi("/admin/swarms");
  const locations = useApi("/admin/locations");
  const { data: membersData } = useApi("/admin/members");
  const swarms = data || [];
  const allMembers = (membersData?.members || []).filter((m) => m.status === "active");

  if (loading && !data) return <Loading what="Loading swarms" />;
  if (error) return <ErrorNote error={error} onRetry={reload} />;

  return (
    <>
      <PageHeader title="Swarms" />
      {swarms.length === 0 && (
        <EmptyState title="No swarms yet">
          Create a swarm to start. Your browser generates its root key and shows it once; keep it offline. A
          hub is then appointed by signing with that key, here or with{" "}
          <code className="hash">python -m scripts.swarm_key appoint-hub</code>.
        </EmptyState>
      )}
      <CreateSwarm onCreated={reload} />
      <ul className="panel" aria-label="Swarms">
        {swarms.map((s) => <SwarmCard key={s.id} swarm={s} swarms={swarms} allMembers={allMembers}
                                     location={locations.data?.swarms?.[s.name] ?? null}
                                     onReload={() => { reload(); locations.reload(); }} />)}
      </ul>
    </>
  );
}
