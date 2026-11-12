// Swarm membership controls shared by the Swarms page card and the swarm
// profile's Members tab (#282): change a role, remove a member, add a member.
import { useState } from "react";
import HelpTip from "./HelpTip.jsx";
import { api } from "../api/client.js";
import { SWARM_ROLE } from "../lib/help.js";
import { membershipChange } from "../lib/membership.js";

export const ADD_ROLES = ["member", "observer"];

// A member or observer's role, changed in one step with Apply (issue #160).
export function RoleControl({ swarm, member, busy, act }) {
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

export function RemoveMemberButton({ name, busy, act }) {
  return (
    <button className="btn btn-danger text-xs" disabled={busy}
            onClick={() => act(() => api(`/admin/swarms/members/${encodeURIComponent(name)}`, { method: "DELETE" }))}
            aria-label={`Remove ${name}`}>
      Remove
    </button>
  );
}

/** Add an active member (or move one from another swarm, unless it's that swarm's hub). */
export function AddMemberForm({ swarm, swarms, allMembers, busy, act }) {
  const members = swarm.members || [];
  const placement = new Map();
  for (const s of swarms || []) {
    if (s.name === swarm.name) continue;
    for (const m of s.members || []) placement.set(m.name, { swarm: s.name, role: m.role });
  }
  const eligible = (allMembers || []).filter((m) => m.status === "active" && !members.some((s) => s.name === m.name)
    && placement.get(m.name)?.role !== "hub");
  const [addName, setAddName] = useState("");
  const [addRole, setAddRole] = useState("member");

  return (
    <form onSubmit={(e) => {
            e.preventDefault();
            act(() => api(`/admin/swarms/${encodeURIComponent(swarm.name)}/members`,
              { method: "POST", body: { member: addName, role: addRole } }));
          }}
          className="mb-4 flex flex-wrap items-end gap-2" aria-label="Add member">
      <select className="field" value={addName} onChange={(e) => setAddName(e.target.value)} aria-label="Member">
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
      <select className="field w-24" value={addRole} onChange={(e) => setAddRole(e.target.value)} aria-label="Role">
        {ADD_ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
      </select>
      <HelpTip id={`add-role-help-${swarm.name}`} label="About roles">{SWARM_ROLE}</HelpTip>
      <button className="btn btn-primary" disabled={busy || !addName}>Add</button>
    </form>
  );
}
