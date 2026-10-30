import { useState } from "react";
import { api } from "../api/client.js";
import { useApi } from "../api/useApi.js";
import { EmptyState, ErrorNote, Loading, PageHeader, StatusBadge } from "../components/ui.jsx";

const ROLES = ["member", "observer"];

function CreateSwarm({ onCreated }) {
  const [name, setName] = useState("");
  const [publicKey, setPublicKey] = useState("");
  const [description, setDescription] = useState("");
  const [home, setHome] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function create(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/admin/swarms", {
        method: "POST",
        body: { name: name.trim(), public_key: publicKey.trim(), description: description.trim() || null, home },
      });
      setName("");
      setPublicKey("");
      setDescription("");
      setHome(false);
      onCreated();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mb-8" aria-labelledby="create-h">
      <h2 id="create-h" className="mb-3 text-lg font-bold">Register a swarm</h2>
      <p className="mb-3 text-sm text-paper-muted">
        The swarm's root key stays offline with you. Here you only register its name and public
        key (the fingerprint shown by `python -m scripts.swarm_key show`).
      </p>
      <form onSubmit={create} className="mb-4 flex flex-wrap items-end gap-2" aria-label="Register swarm">
        <input className="field w-40" value={name} onChange={(e) => setName(e.target.value)}
               placeholder="Swarm name (echo-base)" aria-label="Swarm name" required />
        <input className="field w-64" value={publicKey} onChange={(e) => setPublicKey(e.target.value)}
               placeholder="Root public key, 64 hex" aria-label="Public key" required />
        <input className="field w-64" value={description} onChange={(e) => setDescription(e.target.value)}
               placeholder="Description" aria-label="Description" />
        <label className="flex items-center gap-2 text-sm text-paper-muted cursor-pointer">
          <input type="checkbox" checked={home} onChange={(e) => setHome(e.target.checked)} className="accent-led" />
          Make this the home swarm (only one active home at a time)
        </label>
        <button className="btn btn-primary" disabled={busy}>Register</button>
      </form>
      <ErrorNote error={error} />
    </section>
  );
}

function SwarmCard({ swarm, allMembers, onReload }) {
  const members = swarm.members || [];
  const revoked = swarm.status === "revoked";
  const eligible = allMembers.filter((m) => m.status === "active" && !members.some((s) => s.name === m.name));
  const [addName, setAddName] = useState("");
  const [addRole, setAddRole] = useState("member");
  const [appt, setAppt] = useState("");
  const [confirmRevoke, setConfirmRevoke] = useState("");
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

  async function appointHub() {
    return api(`/admin/swarms/${encodeURIComponent(swarm.name)}/hub`, {
      method: "POST", body: JSON.parse(appt),
    });
  }

  async function revokeSwarm() {
    return api(`/admin/swarms/${encodeURIComponent(swarm.name)}/revoke`, { method: "POST" });
  }

  return (
    <section className="mb-8 border rounded-md border-ink-line bg-ink p-4" aria-labelledby={`swarm-${swarm.name}-h`}>
      <h2 id={`swarm-${swarm.name}-h`} className="mb-2 text-lg font-bold">{swarm.name}</h2>
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
            <h4 className="mb-2 text-xs text-paper-muted">{roleMembers.length} {role}{roleMembers.length > 1 ? "s" : ""}</h4>
            <ul className="panel" aria-label={`${role} members of ${swarm.name}`}>
              {roleMembers.map((m) => (
                <li key={m.name} className="flex items-center justify-between px-3 py-2">
                  <span className="text-sm">{m.name}</span>
                  {!revoked && (
                    <button className="btn btn-danger text-xs" onClick={() => act(() => removeMember(m.name))}>
                      Remove
                    </button>
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
              {eligible.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
            </select>
            <select className="field w-24" value={addRole} onChange={(e) => setAddRole(e.target.value)}
                    aria-label="Role">
              {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <button className="btn btn-primary" disabled={busy || !addName}>Add</button>
          </form>

          <div className="mb-4">
            <h3 className="mb-2 text-sm font-bold text-paper-muted">Appoint hub</h3>
            <p className="mb-2 text-xs text-paper-muted">
              Paste the appointment signed by the swarm's root key (from `python -m scripts.swarm_key appoint-hub`).
            </p>
            <textarea className="field w-full mb-2" value={appt} onChange={(e) => setAppt(e.target.value)}
                      placeholder="Paste the appointment JSON here" rows={4} aria-label="Hub appointment" />
            <button className="btn" onClick={() => act(appointHub)} disabled={busy || !appt.trim()}>
              Appoint hub
            </button>
          </div>

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
        </>
      )}

      <ErrorNote error={error} />
    </section>
  );
}

export default function Swarms() {
  const { data, error, loading, reload } = useApi("/admin/swarms");
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
          Register a swarm to start. Its root key is created offline with
          <code className="hash">python -m scripts.swarm_key create</code> and a hub is appointed with a signed
          <code className="hash">appoint-hub</code> statement.
        </EmptyState>
      )}
      <CreateSwarm onCreated={reload} />
      <ul className="panel" aria-label="Swarms">
        {swarms.map((s) => <SwarmCard key={s.id} swarm={s} allMembers={allMembers} onReload={reload} />)}
      </ul>
    </>
  );
}
