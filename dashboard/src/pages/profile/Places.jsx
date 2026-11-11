// Profile Places & movement tab (issue #193).
import { useEffect, useState } from "react";
import { api } from "../../api/client.js";
import { MemberMark } from "../../components/visual.jsx";
import { actionPoses, makeScaler, percent, poseSamples } from "../../lib/profile.js";
import { CATEGORIES, NEUTRAL, category } from "../../lib/visual.js";
import { memberWithRole } from "../Profile.jsx";

const ICONS = {
  gps: "M12 2v3 M12 19v3 M2 12h3 M19 12h3 M12 7a5 5 0 1 0 0 10a5 5 0 1 0 0-10z M11 12a1 1 0 1 0 2 0a1 1 0 1 0-2 0",
  declared: "M12 21s-6-5.6-6-11a6 6 0 1 1 12 0c0 5.4-6 11-6 11z M10 10a2 2 0 1 0 4 0a2 2 0 1 0-4 0",
  network: "M3 12a9 9 0 1 0 18 0a9 9 0 1 0-18 0 M3 12h18 M12 3c2.5 2.5 3.8 5.5 3.8 9s-1.3 6.5-3.8 9 M12 3c-2.5 2.5-3.8 5.5-3.8 9s1.3 6.5 3.8 9",
};
const SOURCES = [
  { key: "gps", label: "GPS", explain: "From the newest sensed record with modality gps. High precision." },
  { key: "declared", label: "Declared", explain: "Set by an admin on the member, or inherited from its swarm." },
  { key: "network", label: "Network estimate", explain: "City at best, from an optional offline database. Never for private addresses." },
];
const PATH_LIMIT = 200;

function Icon({ d }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={NEUTRAL} strokeWidth="1.8"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d={d} /></svg>
  );
}

function PlaceList({ places }) {
  const max = Math.max(1, ...places.map((p) => p.total));
  return (
    <section aria-labelledby="places-h" className="mb-6">
      <h2 id="places-h" className="mb-2 text-lg font-bold">Places</h2>
      {places.length === 0 ? (
        <p className="text-sm text-paper-muted">No records with a place in this range.</p>
      ) : (
        <ul className="panel">
          {places.map((p) => (
            <li key={p.place} className="flex flex-wrap items-center gap-3 border-b border-ink-line px-3 py-2 last:border-b-0">
              <span className="w-40 font-bold">{p.place}</span>
              <span className="w-56 text-sm text-paper-muted">
                {p.total} records{p.action_success != null ? ` · ${percent(p.action_success)} action success` : ""}
              </span>
              <span className="flex h-2 overflow-hidden rounded bg-ink-line" aria-hidden="true"
                    style={{ width: `${Math.max(4, (p.total / max) * 240)}px` }}>
                {CATEGORIES.map((c) => (p[c.key] ? (
                  <span key={c.key} style={{ width: `${(p[c.key] / p.total) * 100}%`, background: c.color }} />
                ) : null))}
              </span>
              <span className="sr-only">
                {CATEGORIES.filter((c) => p[c.key]).map((c) => `${p[c.key]} ${c.label.toLowerCase()}`).join(", ")}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function describeEntry(e) {
  const where = [e.label, e.room].filter(Boolean).join(", ");
  const coords = e.lat != null ? ` (${e.lat.toFixed(4)}, ${e.lon.toFixed(4)})` : "";
  const unc = e.accuracy_km != null ? ` · ±${e.accuracy_km < 1 ? `${Math.round(e.accuracy_km * 1000)} m` : `${e.accuracy_km} km`}` : "";
  const from = e.inherited_from ? ` · from ${e.inherited_from}` : "";
  return `${where || "unnamed"}${coords}${unc}${from}`;
}

function Sources({ entries }) {
  return (
    <section aria-labelledby="sources-h" className="mb-6">
      <h2 id="sources-h" className="mb-2 text-lg font-bold">Location sources</h2>
      <ul className="panel">
        {SOURCES.map((s) => {
          const e = (entries || []).find((x) => x.source === s.key);
          return (
            <li key={s.key} className="flex items-start gap-3 border-b border-ink-line px-3 py-2 last:border-b-0">
              <Icon d={ICONS[s.key]} />
              <span className="w-36 font-bold">{s.label}</span>
              <span className="text-sm">
                {e ? describeEntry(e) : <span className="text-paper-muted">{s.explain}</span>}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

const W = 640;
const H = 400;

function diamond(x, y, r = 5) {
  return `M${x} ${y - r}L${x + r} ${y}L${x} ${y + r}L${x - r} ${y}Z`;
}

export function Movement({ poses, actions, members, swarms }) {
  const [asTable, setAsTable] = useState(false);
  if (!poses.length) {
    return (
      <section aria-labelledby="move-h">
        <h2 id="move-h" className="mb-2 text-lg font-bold">Movement</h2>
        <p className="text-sm text-paper-muted">
          No pose data yet. Members send movement as sensed records with <code>{`"kind": "pose"`}</code>, a{" "}
          <code>frame</code> and <code>x</code>, <code>y</code> samples; see <code>docs/sensing.md</code>.
        </p>
      </section>
    );
  }
  const sense = category("sense");
  const act = category("act");
  const frame = poses[0].frame;
  const scale = makeScaler([...poses, ...actions], W, H);
  const byAgent = {};
  for (const p of poses) (byAgent[p.agent] ||= []).push(p);
  return (
    <section aria-labelledby="move-h">
      <div className="mb-2 flex items-center justify-between">
        <h2 id="move-h" className="text-lg font-bold">Movement</h2>
        <button type="button" className="btn" aria-pressed={asTable} onClick={() => setAsTable((v) => !v)}>
          {asTable ? "View as chart" : "View as table"}
        </button>
      </div>
      {asTable ? (
        <table className="w-full text-sm">
          <caption className="sr-only">Plotted pose samples</caption>
          <thead><tr><th className="text-left">Time</th><th className="text-left">Member</th>
            <th className="text-right">x</th><th className="text-right">y</th><th className="text-left">Frame</th></tr></thead>
          <tbody>
            {poses.map((p, i) => (
              <tr key={`${p.agent}-${i}`}><td>{p.ts}</td><td>{p.agent}</td>
                <td className="text-right">{p.x}</td><td className="text-right">{p.y}</td><td>{p.frame}</td></tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="relative">
          <svg viewBox={`0 0 ${W} ${H}`} className="w-full rounded-md border border-ink-line" role="img"
               aria-label={`Movement in frame ${frame}: ${poses.length} pose samples. Use View as table for the values.`}>
            <text x={W / 2} y={H - 6} fontSize="11" textAnchor="middle" fill="#93A0B8">{`${frame} x`}</text>
            <text x={12} y={H / 2} fontSize="11" textAnchor="middle" fill="#93A0B8"
                  transform={`rotate(-90 12 ${H / 2})`}>{`${frame} y`}</text>
            {Object.entries(byAgent).map(([agent, pts]) => (
              <polyline key={agent} fill="none" stroke={sense.color} strokeWidth="2" strokeDasharray={sense.dash}
                        strokeLinecap="round" points={pts.map((p) => { const s = scale.map(p); return `${s.x},${s.y}`; }).join(" ")} />
            ))}
            {actions.map((a, i) => {
              const s = scale.map(a);
              return (
                <path key={i} d={diamond(s.x, s.y)} fill={act.color}>
                  <title>{`${a.agent}: ${a.summary || "action"}${a.outcome ? ` (${a.outcome})` : ""}`}</title>
                </path>
              );
            })}
          </svg>
          {Object.entries(byAgent).map(([agent, pts]) => {
            const s = scale.map(pts[pts.length - 1]);
            const m = memberWithRole((members || []).find((x) => x.name === agent) || { name: agent }, swarms);
            return (
              <span key={agent} className="absolute -translate-x-1/2 -translate-y-1/2"
                    style={{ left: `${(s.x / W) * 100}%`, top: `${(s.y / H) * 100}%` }}
                    title={`${agent}: newest position`}>
                <MemberMark member={m} size={22} />
              </span>
            );
          })}
        </div>
      )}
    </section>
  );
}

export default function Places({ subject, name, summary, members, swarms }) {
  const [entries, setEntries] = useState(null);
  const [poses, setPoses] = useState([]);
  const [actions, setActions] = useState([]);
  const agents = subject === "member"
    ? [name]
    : ((swarms || []).find((s) => s.name === name)?.members || []).map((m) => m.name);
  const agentKey = agents.join(",");

  useEffect(() => {
    let live = true;
    setEntries(null);
    if (subject === "member") {
      api("/admin/locations", { params: { member: name } })
        .then((d) => { if (live) setEntries(d?.members?.[0]?.all ?? []); })
        .catch(() => {}); // an older server without #191: show the explanations only
    }
    return () => { live = false; };
  }, [subject, name]);

  useEffect(() => {
    let live = true;
    Promise.all(agentKey.split(",").filter(Boolean).map(async (agent) => {
      const [sensed, acts] = await Promise.all([
        api("/admin/timeline", { params: { agent, type: "sensed", limit: PATH_LIMIT } }).catch(() => ({ records: [] })),
        api("/admin/timeline", { params: { agent, type: "action", limit: PATH_LIMIT } }).catch(() => ({ records: [] })),
      ]);
      return { sensed: sensed.records || [], acts: acts.records || [] };
    })).then((all) => {
      if (!live) return;
      setPoses(poseSamples(all.flatMap((a) => a.sensed)));
      setActions(actionPoses(all.flatMap((a) => a.acts)));
    });
    return () => { live = false; };
  }, [agentKey]);

  return (
    <>
      <PlaceList places={summary.places} />
      {subject === "member" && <Sources entries={entries} />}
      <Movement poses={poses} actions={actions} members={members} swarms={swarms} />
    </>
  );
}
