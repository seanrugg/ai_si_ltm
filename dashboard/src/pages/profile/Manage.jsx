// Swarm profile Manage tab (#331): everything that used to sit on the Swarms list card,
// except membership, which is on the Members tab (#282).
import { useState } from "react";
import { Link } from "react-router-dom";
import HelpTip from "../../components/HelpTip.jsx";
import LocationForm from "../../components/LocationForm.jsx";
import RemoveSwarmDialog from "../../components/RemoveSwarmDialog.jsx";
import RenameSwarm from "../../components/RenameSwarm.jsx";
import { ErrorNote, StatusBadge } from "../../components/ui.jsx";
import { api } from "../../api/client.js";
import { useApi } from "../../api/useApi.js";
import { plural } from "../../lib/format.js";
import { LEADER_ABOUT, MEMORY_WIPE, ROOT_KEY_PASTE, WIPE_VS_DELETE } from "../../lib/help.js";
import { appointLeader, signAppointment, SwarmKeyError } from "../../lib/swarmKey.js";

export function AppointHub({ swarm, allMembers, busy, act }) {
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
          <HelpTip id={`root-key-paste-${swarm.name}`} label="If the key is pasted wrong">{ROOT_KEY_PASTE}</HelpTip>
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

/** A city estimate from a member's network address (#285), to pre-fill a swarm's home location. */
/** Appoint or remove the swarm leader (#309): any active member except the hub. */
export function AppointLeader({ swarm, allMembers, busy, act }) {
  const hubName = (swarm.members || []).find((m) => m.role === "hub")?.name;
  const candidates = (allMembers || []).filter((m) => m.status === "active" && m.name !== hubName);
  const [leader, setLeader] = useState("");
  const [keyText, setKeyText] = useState("");
  const [localError, setLocalError] = useState(null);
  const [confirmRemove, setConfirmRemove] = useState(false);

  async function sign(e) {
    e.preventDefault();
    setLocalError(null);
    const member = candidates.find((m) => m.name === leader);
    let body;
    try {
      body = appointLeader(keyText, { swarm, leader, leaderKey: member?.public_key });
    } catch (err) {
      setKeyText("");
      setLocalError(err instanceof SwarmKeyError ? err : new Error("Couldn't sign the appointment."));
      return;
    }
    setKeyText("");
    await act(() => api(`/admin/swarms/${encodeURIComponent(swarm.name)}/leader`, { method: "POST", body }));
    setLeader("");
  }

  return (
    <div className="mb-4">
      <h3 className="mb-2 flex items-center gap-2 text-sm font-bold text-paper-muted">
        Appoint leader <HelpTip id={`leader-about-${swarm.name}`} label="About the swarm leader">{LEADER_ABOUT}</HelpTip>
      </h3>
      <p className="mb-2 text-xs text-paper-muted">{swarm.leader ? `Leader: ${swarm.leader}.` : "No leader."}</p>
      <form onSubmit={sign} className="mb-2 flex flex-wrap items-end gap-2" autoComplete="off"
            aria-label={`Appoint leader for ${swarm.name}`}>
        <label className="flex flex-col gap-1 text-xs text-paper-muted">
          Leader
          <select className="field" value={leader} onChange={(e) => setLeader(e.target.value)} required>
            <option value="">Choose a member</option>
            {candidates.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-paper-muted">
          Root key (signed here; never sent)
          <input className="field w-72 font-mono" type="password" name="swarm-root-key-leader"
                 autoComplete="new-password" spellCheck={false} data-1p-ignore data-lpignore="true"
                 value={keyText} onChange={(e) => setKeyText(e.target.value)} placeholder="ltms-swarm-key-v1:…" required />
        </label>
        <button className="btn btn-primary" disabled={busy || !leader || !keyText}>Sign and appoint</button>
      </form>
      {localError && <p className="mb-2 text-sm text-bad" role="alert">{localError.message}</p>}
      {swarm.leader && (confirmRemove ? (
        <span className="flex flex-wrap items-center gap-2 text-sm">
          Remove {swarm.leader} as leader?
          <button type="button" className="btn btn-danger" disabled={busy}
                  onClick={() => act(() => api(`/admin/swarms/${encodeURIComponent(swarm.name)}/leader`, { method: "DELETE" }))
                    .then(() => setConfirmRemove(false))}>Remove leader</button>
          <button type="button" className="btn" onClick={() => setConfirmRemove(false)}>Keep</button>
        </span>
      ) : <button type="button" className="btn" onClick={() => setConfirmRemove(true)}>Remove leader…</button>)}
    </div>
  );
}

export function networkSuggestion(swarm, locations) {
  const names = new Set((swarm.members || []).map((m) => m.name));
  for (const l of locations?.members || []) {
    if (!names.has(l.name)) continue;
    const net = (l.all || []).find((e) => e.source === "network" && e.label);
    if (net) return { label: net.label, lat: net.lat ?? "", lon: net.lon ?? "" };
  }
  return null;
}

export function SwarmManage({ swarm, allMembers, location, suggestion, onReload }) {
  const members = swarm.members || [];
  const revoked = swarm.status === "revoked";
  // allMembers is already filtered to active, so this counts who the wipe would reach.
  const activeNames = new Set(allMembers.map((m) => m.name));
  const wipeCount = members.filter((m) => activeNames.has(m.name)).length;
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
    <section className="mb-8 rounded-md border border-ink-line bg-ink p-4" aria-labelledby={`swarm-${swarm.name}-h`}>
      <h2 id={`swarm-${swarm.name}-h`} className="mb-2 text-lg font-bold">Manage {swarm.name}</h2>
      <RenameSwarm name={swarm.name} onDone={onReload} />
      <LocationForm key={JSON.stringify(location)} subjectType="swarm" name={swarm.name} current={location}
                    title="Home location" suggestion={suggestion} onDone={onReload}
                    prompt="Set a home location: every member without its own location will use it." />
      <p className="mb-3 text-xs text-paper-muted">
        <StatusBadge status={swarm.status} /> {swarm.home && <span className="ml-2">home swarm</span>}
        {swarm.description && <span className="ml-2">— {swarm.description}</span>}
      </p>

      <p className="mb-4 text-sm text-paper-muted">
        Membership (add, remove, change roles) is on the <Link to={`/swarms/${encodeURIComponent(swarm.name)}/profile?tab=members`}
              className="underline">Members</Link> tab.
      </p>

      {!revoked && (
        <>
          <AppointHub swarm={swarm} allMembers={allMembers} busy={busy} act={act} />
          <AppointLeader swarm={swarm} allMembers={allMembers} busy={busy} act={act} />

          <h3 className="mb-2 mt-6 text-base font-bold">Danger zone</h3>
          <div className="rounded-md border border-bad/40 bg-bad/5 p-3">
            <h4 className="mb-2 text-sm font-bold text-paper-muted">Revoke swarm</h4>
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

export default function Manage({ name, swarms, members, onReload }) {
  const locations = useApi("/admin/locations");
  const swarm = (swarms || []).find((s) => s.name === name);
  if (!swarm) return <p className="py-8 text-sm text-paper-muted">This swarm isn&apos;t in the list.</p>;
  const allMembers = (members || []).filter((m) => m.status === "active");
  return (
    <SwarmManage swarm={swarm} allMembers={allMembers}
                 location={locations.data?.swarms?.[swarm.name] ?? null}
                 suggestion={networkSuggestion(swarm, locations.data)}
                 onReload={() => { onReload?.(); locations.reload?.(); }} />
  );
}
