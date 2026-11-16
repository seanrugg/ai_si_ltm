// Members and addresses (issue #196), in Geography's table twin (#405).
// The only place on the tab that shows addresses: admin-only, never narrated,
// never drawn on the globe and never sent to an LRS.
import { HoloTable } from "../../components/VizFrame.jsx";
import { relativeTime } from "../../lib/format.js";
import { KINDS, ROLES, kindOf } from "../../lib/visual.js";

/** One row per member of `/admin/network`, with its best location. */
export function addressRows(netRows, locRows, members) {
  const loc = Object.fromEntries((locRows || []).map((r) => [r.name, r]));
  return (netRows || []).map((r) => {
    const m = (members || []).find((x) => x.name === r.name) || { name: r.name, kind: r.kind };
    const role = ROLES[r.role] ? r.role : "free";
    const kind = kindOf({ ...m, kind: m.kind ?? r.kind, role });
    const best = loc[r.name]?.best;
    const where = best ? [best.label, best.room].filter(Boolean).join(", ") || "unnamed" : null;
    return {
      id: r.name,
      name: r.name,
      role,
      kind,
      path: r.path ?? "—",
      seen: r.seen?.ip ?? "—",
      reported: r.reported?.length ? r.reported.map((a) => `${a.ip} (${a.source})`).join(", ") : "—",
      location: best ? `${where} (${best.source === "network" ? "network, estimated" : best.source})` : "—",
      lastSeen: m.last_seen_at ?? r.seen?.last_seen ?? null,
    };
  });
}

export default function NetworkTable({ netRows, locRows, members }) {
  return (
    <div>
      <HoloTable caption="Members and addresses" rows={addressRows(netRows, locRows, members)}
                 columns={[
                   { key: "name", label: "Member", render: (r) => <strong>{r.name}</strong> },
                   { key: "role", label: "Role · Kind", render: (r) => (
                     <><span style={{ color: ROLES[r.role].color }}>{ROLES[r.role].label}</span>{` · ${KINDS[r.kind].label}`}</>
                   ) },
                   { key: "path", label: "Path" },
                   { key: "seen", label: "Seen by FLTMS" },
                   { key: "reported", label: "Reported LAN IP" },
                   { key: "location", label: "Location" },
                   { key: "lastSeen", label: "Last seen", render: (r) => (r.lastSeen ? relativeTime(r.lastSeen) : "—") },
                 ]} />
      <ul className="mt-2 list-disc pl-5 text-xs text-paper-muted">
        <li>&ldquo;Seen by FLTMS&rdquo; is what the server saw, and is reliable. Members behind a hub show the hub&apos;s address.</li>
        <li>Reported addresses are unverified.</li>
        <li>Cloud agents show their provider&apos;s address.</li>
        <li>Addresses are for admins only: they are never drawn on the globe, narrated, or sent to an LRS.</li>
      </ul>
    </div>
  );
}
