// Swarm profile Members tab (#282): who is in the swarm, and managing membership.
import { useState } from "react";
import { Link } from "react-router-dom";
import { ErrorNote, StatusBadge } from "../../components/ui.jsx";
import { AddMemberForm, RemoveMemberButton, RoleControl } from "../../components/SwarmMemberControls.jsx";
import { localTime, relativeTime } from "../../lib/format.js";
import { SpecsRollup } from "./Specs.jsx";
import { KINDS, ROLES, kindOf } from "../../lib/visual.js";

const ORDER = { hub: 0, member: 1, observer: 2 };

export default function Members({ name, summary, members, swarms, onReload }) {
  const swarm = (swarms || []).find((s) => s.name === name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  if (!swarm) return <p className="py-8 text-sm text-paper-muted">This swarm isn&apos;t in the list.</p>;

  const byName = new Map((members || []).map((m) => [m.name, m]));
  const counts = new Map((summary?.members || []).map((r) => [
    r.name, ["sense", "act", "comm", "learn"].reduce((n, k) => n + (r[k] || 0), 0)]));
  const rows = [...(swarm.members || [])].sort((a, b) => (ORDER[a.role] - ORDER[b.role]) || a.name.localeCompare(b.name));
  const revoked = swarm.status === "revoked";
  const active = (members || []).filter((m) => m.status === "active");

  async function act(fn) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onReload?.();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="members-h">
      <h2 id="members-h" className="mb-2 text-lg font-bold">Members</h2>
      <p className="mb-2 text-sm text-paper-muted">{swarm.leader ? `Leader: ${swarm.leader}` : "No leader"}</p>
      <ErrorNote error={error} />
      {rows.length === 0 ? (
        <p className="mb-4 text-sm text-paper-muted">No members yet.</p>
      ) : (
        <div className="panel mb-4 overflow-x-auto">
          <table className="w-full text-sm">
            <caption className="sr-only">Members of {name}, with role, kind, status and activity</caption>
            <thead>
              <tr className="border-b border-ink-line text-left text-xs text-paper-muted">
                <th scope="col" className="px-3 py-2">Name</th>
                <th scope="col" className="px-3 py-2">Role</th>
                <th scope="col" className="px-3 py-2">Kind</th>
                <th scope="col" className="px-3 py-2">Status</th>
                <th scope="col" className="px-3 py-2">Last seen</th>
                <th scope="col" className="px-3 py-2 text-right">Records in range</th>
                {!revoked && <th scope="col" className="px-3 py-2"><span className="sr-only">Manage</span></th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const m = byName.get(row.name) || { name: row.name };
                const kind = KINDS[kindOf({ ...m, role: row.role })]?.label ?? m.kind ?? "";
                return (
                  <tr key={row.name} className="border-b border-ink-line last:border-b-0">
                    <td className="px-3 py-2">
                      <Link to={`/members/${encodeURIComponent(row.name)}/profile`} className="font-bold hover:underline">
                        {row.name}
                      </Link>
                    </td>
                    <td className="px-3 py-2">
                      <span className="inline-flex items-center gap-2">
                        <span aria-hidden="true" className="inline-block h-3 w-3 rounded-full"
                              style={{ boxShadow: `inset 0 0 0 2px ${ROLES[row.role]?.color}` }} />
                        {row.role}
                        {swarm.leader === row.name && (
                          <span className="ml-2 inline-flex items-center gap-1 text-xs">
                            <span aria-hidden="true" className="inline-block h-3 w-3 rounded-full"
                                  style={{ boxShadow: `inset 0 0 0 2px ${ROLES.leader.color}` }} />
                            leader
                          </span>
                        )}
                      </span>
                    </td>
                    <td className="px-3 py-2">{kind}</td>
                    <td className="px-3 py-2">{m.status ? <StatusBadge status={m.status} /> : "—"}</td>
                    <td className="px-3 py-2" title={localTime(m.last_seen_at)}>
                      {m.last_seen_at ? relativeTime(m.last_seen_at) : "never"}
                    </td>
                    <td className="px-3 py-2 text-right">{counts.get(row.name) ?? 0}</td>
                    {!revoked && (
                      <td className="px-3 py-2">
                        {row.role === "hub" ? (
                          <span className="text-xs text-paper-muted">Change the hub with Appoint hub on the Swarms page</span>
                        ) : (
                          <span className="flex flex-wrap items-center gap-2">
                            <RoleControl key={row.role} swarm={swarm} member={row} busy={busy} act={act} />
                            <RemoveMemberButton name={row.name} busy={busy} act={act} />
                          </span>
                        )}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {!revoked && <AddMemberForm swarm={swarm} swarms={swarms} allMembers={active} busy={busy} act={act} />}
      <SpecsRollup swarm={swarm} members={members} />
    </section>
  );
}
