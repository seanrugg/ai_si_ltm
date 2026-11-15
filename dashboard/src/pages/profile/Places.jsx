// Profile Places & movement tab (issue #193); Choreography, for members with a body (#407).
import { useEffect, useState } from "react";
import { api } from "../../api/client.js";
import HelpTip from "../../components/HelpTip.jsx";
import { MemberMark } from "../../components/visual.jsx";
import { localTime } from "../../lib/format.js";
import { CHOREOGRAPHY } from "../../lib/help.js";
import { actionPoses, hasBody, makeScaler, niceLength, percent, poseFrames } from "../../lib/profile.js";
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

function Track({ track, color, gid }) {
  const n = track.pts.length;
  if (n === 1) return null;
  return (
    <g filter={`url(#${gid})`}>
      {track.pts.slice(1).map((p, i) => {
        const a = track.pts[i];
        // Fades from older (dim) to newer (bright).
        const opacity = 0.2 + 0.8 * ((i + 1) / (n - 1));
        return <line key={i} x1={a.x} y1={a.y} x2={p.x} y2={p.y} stroke={color} strokeWidth="2.5"
                     strokeLinecap="round" strokeOpacity={opacity} />;
      })}
    </g>
  );
}

function FramePanel({ track, actions, index, members, swarms }) {
  const sense = category("sense");
  const act = category("act");
  const scale = makeScaler([...track.samples, ...actions], W, H);
  const { minX, maxX, minY, maxY } = scale.bounds;
  const k = scale.map({ x: minX + 1, y: minY }).x - scale.map({ x: minX, y: minY }).x; // px per unit
  const unit = track.unit || "units";
  const spanX = maxX - minX;
  const spanY = maxY - minY;
  const bar = niceLength(Math.max(spanX, spanY, 1) / 4);
  const barPx = Math.min(W / 2, bar * k);
  const pts = track.samples.map(scale.map);
  const first = pts[0];
  const last = pts[pts.length - 1];
  const gid = `glow-${index}`;
  const m = memberWithRole((members || []).find((x) => x.name === track.agent) || { name: track.agent }, swarms);
  const fmt = (v) => (Math.abs(v) >= 100 ? Math.round(v) : Number(v.toFixed(1)));
  return (
    <li className="mb-4">
      <h3 className="mb-1 text-sm font-bold">
        {track.agent} · frame <code>{track.frame}</code>
        <span className="ml-2 font-normal text-paper-muted">
          {localTime(track.start)} to {localTime(track.end)} · {track.samples.length} samples ·
          {` spans ${fmt(spanX)} × ${fmt(spanY)} ${unit}`}
        </span>
      </h3>
      {track.newFrame && (
        <p className="mb-1 text-sm text-paper-muted">
          New frame: {track.agent}&apos;s pose origin changed (for example, it was picked up), so this track starts
          fresh and isn&apos;t joined to the one before.
        </p>
      )}
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full rounded-md border border-ink-line" role="img"
             aria-label={`${track.agent} in frame ${track.frame}: ${track.samples.length} pose samples, spanning ${fmt(spanX)} by ${fmt(spanY)} ${unit}. Use View as table for the values.`}>
          <defs>
            <filter id={gid} x="-10%" y="-10%" width="120%" height="120%">
              <feGaussianBlur stdDeviation="2.5" result="blur" />
              <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
            </filter>
          </defs>
          <text x={W / 2} y={H - 6} fontSize="11" textAnchor="middle" fill="#93A0B8">{`x (${unit})`}</text>
          <text x={12} y={H / 2} fontSize="11" textAnchor="middle" fill="#93A0B8"
                transform={`rotate(-90 12 ${H / 2})`}>{`y (${unit})`}</text>
          <Track track={{ pts }} color={sense.color} gid={gid} />
          <circle cx={first.x} cy={first.y} r="6" fill="none" stroke={sense.color} strokeWidth="2">
            <title>{`Start: ${localTime(track.start)}`}</title>
          </circle>
          <text x={first.x + 9} y={first.y - 8} fontSize="11" fill="#93A0B8">start</text>
          <circle cx={last.x} cy={last.y} r="6" fill={sense.color}>
            <title>{`End: ${localTime(track.end)}`}</title>
          </circle>
          <text x={last.x + 9} y={last.y - 8} fontSize="11" fill="#93A0B8">end</text>
          {actions.map((a, i) => {
            const p = scale.map(a);
            return (
              <path key={i} d={diamond(p.x, p.y)} fill={act.color}>
                <title>{`${a.agent}: ${a.summary || "action"}${a.outcome ? ` (${a.outcome})` : ""}`}</title>
              </path>
            );
          })}
          <g aria-hidden="true">
            <line x1={W - 24 - barPx} y1={24} x2={W - 24} y2={24} stroke="#93A0B8" strokeWidth="2" />
            <line x1={W - 24 - barPx} y1={19} x2={W - 24 - barPx} y2={29} stroke="#93A0B8" strokeWidth="2" />
            <line x1={W - 24} y1={19} x2={W - 24} y2={29} stroke="#93A0B8" strokeWidth="2" />
            <text x={W - 24 - barPx / 2} y={42} fontSize="11" textAnchor="middle" fill="#93A0B8">{`${bar} ${unit}`}</text>
          </g>
        </svg>
        <span className="absolute -translate-x-1/2 -translate-y-1/2"
              style={{ left: `${(last.x / W) * 100}%`, top: `${(last.y / H) * 100}%` }}
              title={`${track.agent}: newest position in this frame`}>
          <MemberMark member={m} size={22} />
        </span>
      </div>
    </li>
  );
}

/** Actions drawn in a frame's panel: same frame, or (without one) the same member during the track. */
function actionsIn(track, actions) {
  return actions.filter((a) => (a.frame ? a.frame === track.frame && a.agent === track.agent
    : a.agent === track.agent && a.ts >= track.start && a.ts <= track.end));
}

export function Choreography({ tracks, actions, members, swarms }) {
  const [asTable, setAsTable] = useState(false);
  const heading = (
    <h2 id="choreo-h" className="text-lg font-bold">
      Choreography <HelpTip id="choreo-about" label="About Choreography">{CHOREOGRAPHY}</HelpTip>
    </h2>
  );
  if (!tracks.length) {
    return (
      <section aria-labelledby="choreo-h">
        <div className="mb-2">{heading}</div>
        <p className="text-sm text-paper-muted">
          No pose data yet. Members with a body send movement as sensed records with <code>{`"kind": "pose"`}</code>,
          a <code>frame</code> and <code>x</code>, <code>y</code> samples; see <code>docs/sensing.md</code>.
        </p>
      </section>
    );
  }
  return (
    <section aria-labelledby="choreo-h">
      <div className="mb-2 flex items-center justify-between">
        {heading}
        <button type="button" className="btn" aria-pressed={asTable} onClick={() => setAsTable((v) => !v)}>
          {asTable ? "View as chart" : "View as table"}
        </button>
      </div>
      {asTable ? (
        <table className="w-full text-sm">
          <caption className="sr-only">Pose samples, newest frame first</caption>
          <thead><tr><th className="text-left">Time</th><th className="text-left">Member</th>
            <th className="text-right">x</th><th className="text-right">y</th><th className="text-left">Unit</th>
            <th className="text-left">Frame</th></tr></thead>
          <tbody>
            {tracks.flatMap((t) => t.samples.map((p, i) => (
              <tr key={`${t.key}-${i}`}><td>{p.ts}</td><td>{t.agent}</td>
                <td className="text-right">{p.x}</td><td className="text-right">{p.y}</td><td>{t.unit || ""}</td>
                <td>{t.frame}</td></tr>
            )))}
          </tbody>
        </table>
      ) : (
        <ol aria-label="Frames, newest first">
          {tracks.map((t, i) => (
            <FramePanel key={t.key} track={t} index={i} actions={actionsIn(t, actions)} members={members}
                        swarms={swarms} />
          ))}
        </ol>
      )}
    </section>
  );
}

export default function Places({ subject, name, summary, members, swarms }) {
  const [entries, setEntries] = useState(null);
  const [tracks, setTracks] = useState([]);
  const [actions, setActions] = useState([]);
  const agents = subject === "member"
    ? [name]
    : ((swarms || []).find((s) => s.name === name)?.members || []).map((m) => m.name);
  // Choreography is only for bodies (#407): AI agents and hubs never show it.
  const bodies = agents.filter((a) => hasBody(memberWithRole((members || []).find((m) => m.name === a), swarms)));
  const agentKey = bodies.join(",");

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
      setTracks(poseFrames(all.flatMap((a) => a.sensed)));
      setActions(actionPoses(all.flatMap((a) => a.acts)));
    });
    return () => { live = false; };
  }, [agentKey]);

  return (
    <>
      <PlaceList places={summary.places} />
      {subject === "member" && <Sources entries={entries} />}
      {bodies.length > 0 && <Choreography tracks={tracks} actions={actions} members={members} swarms={swarms} />}
    </>
  );
}
