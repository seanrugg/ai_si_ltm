// What one swarm/role change means, for the Members and Swarms pages (issue #160).
//
// current: {swarm, role} or null (free agent). target: {swarm, role}, with
// swarm null for a free agent. The server does a join, move or role change
// in one POST /admin/swarms/{name}/members call, and "leave" is one DELETE.

export function membershipChange(current, target) {
  const from = current?.swarm ?? null;
  const to = target?.swarm ?? null;
  let kind;
  if (from === to) kind = to === null || current?.role === target?.role ? "none" : "role";
  else if (from === null) kind = "join";
  else if (to === null) kind = "leave";
  else kind = "move";
  return { kind, hubWarning: current?.role === "hub" && kind !== "none" };
}

/** One line saying what Apply will do, or "" for no change. */
export function describeChange(current, target) {
  const { kind } = membershipChange(current, target);
  switch (kind) {
    case "join": return `Joins ${target.swarm} as ${target.role}`;
    case "move": return `Moves from ${current.swarm} to ${target.swarm} as ${target.role}`;
    case "role": return `Changes role to ${target.role}`;
    case "leave": return `Leaves ${current.swarm} and becomes a free agent`;
    default: return "";
  }
}

export const HUB_WARNING =
  "This member is its swarm's hub. It will lose its relay permission, and that swarm's members can't reach " +
  "the server through the hub until a new hub is appointed.";
