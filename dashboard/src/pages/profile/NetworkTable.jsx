// Members and addresses (issue #196): the text alternative under the map.
import { relativeTime } from "../../lib/format.js";
import { CATEGORIES, KINDS, ROLES, kindOf } from "../../lib/visual.js";

export default function NetworkTable({ netRows, locRows, members, rates, sites, onSelectMember }) {
  const loc = Object.fromEntries((locRows || []).map((r) => [r.name, r]));
  const siteOf = (n) => (sites || []).find((s) => s.members.includes(n));
  return (
    <div className="mt-4 overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="mb-2 text-left text-lg font-bold">Members and addresses</caption>
        <thead>
          <tr className="text-left text-paper-muted">
            <th>Member</th><th>Role · Kind</th><th>Path</th><th>Seen by LTMS</th><th>Reported LAN IP</th>
            <th>Location</th><th className="text-right">Rate</th><th>Last seen</th>
          </tr>
        </thead>
        <tbody>
          {(netRows || []).map((r) => {
            const m = (members || []).find((x) => x.name === r.name) || { name: r.name, kind: r.kind };
            const role = ROLES[r.role] ? r.role : "free";
            const kind = kindOf({ ...m, kind: m.kind ?? r.kind, role });
            const best = loc[r.name]?.best;
            const site = siteOf(r.name);
            const rate = site && site.members.length === 1
              ? CATEGORIES.reduce((s, c) => s + (rates?.[site.key]?.[c.key] || 0), 0) : null;
            return (
              <tr key={r.name} className="border-t border-ink-line">
                <td className="py-1.5">
                  <button type="button" className="underline" onClick={() => onSelectMember(r.name)}>{r.name}</button>
                </td>
                <td><span style={{ color: ROLES[role].color }}>{ROLES[role].label}</span>{` · ${KINDS[kind].label}`}</td>
                <td>{r.path}</td>
                <td>{r.seen?.ip ?? "—"}</td>
                <td>{r.reported?.length ? r.reported.map((a) => `${a.ip} (${a.source})`).join(", ") : "—"}</td>
                <td>{best ? `${[best.label, best.room].filter(Boolean).join(", ") || "unnamed"} (${best.source})` : "—"}</td>
                <td className="text-right">{rate != null ? `${rate.toFixed(1)}/min` : "—"}</td>
                <td>{m.last_seen_at ? relativeTime(m.last_seen_at) : r.seen?.last_seen ? relativeTime(r.seen.last_seen) : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <ul className="mt-2 list-disc pl-5 text-xs text-paper-muted">
        <li>&ldquo;Seen by LTMS&rdquo; is what the server saw, and is reliable. Members behind a hub show the hub&apos;s address.</li>
        <li>Reported addresses are unverified.</li>
        <li>Cloud agents show their provider&apos;s address.</li>
      </ul>
    </div>
  );
}
