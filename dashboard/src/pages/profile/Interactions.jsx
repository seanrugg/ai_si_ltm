// Profile Topology tab (was Interactions): the star system (#404). The hub is
// the star, members are planets, their senses moons, and every record travels
// to the LTMS pyramid. The table twin holds Paths, By source, Members and the
// earlier neuron graph (#194) as "Exchanges".
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../../api/client.js";
import VizFrame, { HoloTable } from "../../components/VizFrame.jsx";
import { ErrorNote, Loading } from "../../components/ui.jsx";
import { CategoryLine, KIND_PATHS, KindIcon } from "../../components/visual.jsx";
import { dendrites, layout, strands } from "../../lib/synapse.js";
import { CATEGORIES, KINDS, RELAY, ROLES, category, kindOf } from "../../lib/visual.js";
import { heartbeatBpm } from "../../lib/light.js";
import { createReplayClock } from "../../lib/replay.js";
import { relativeTime } from "../../lib/format.js";
import { buildScene, memberRows, pathRows, sourceRows, touchList } from "../../lib/topology.js";
import SystemView from "./SystemView.jsx";

const W = 640;
const H = 480;

const STYLE = `
@keyframes ltms-impulse { to { stroke-dashoffset: -40; } }
@keyframes ltms-impulse-back { to { stroke-dashoffset: 40; } }
.ltms-impulse { animation: ltms-impulse 1.6s linear infinite; }
.ltms-impulse.back { animation-name: ltms-impulse-back; }
@media (prefers-reduced-motion: reduce) { .ltms-impulse { animation: none; } }
`;

function nodeMember(n) {
  return { name: n.name, kind: n.kind, role: n.role, cognition: n.cognition };
}

function Neuron({ node, pos, selected, onSelect }) {
  const role = ROLES[node.role] ? node.role : "free";
  const color = ROLES[role].color;
  const r = pos.centre ? 20 : 13;
  const kind = kindOf(nodeMember(node));
  return (
    <a href={`#neuron-${node.name}`} aria-label={`${node.name}, ${ROLES[role].label} · ${KINDS[kind].label}`}
       onClick={(e) => { e.preventDefault(); onSelect(node.name); }}>
      <circle cx={pos.x} cy={pos.y} r={r * 2.4} fill={`url(#glow-${role})`} />
      {dendrites(pos.x, pos.y, r).map((d, i) => (
        <path key={i} d={d} fill="none" stroke={color} strokeOpacity="0.55" strokeWidth="1.2" strokeLinecap="round" />
      ))}
      {selected && <circle cx={pos.x} cy={pos.y} r={r + 7} fill="none" stroke={color} strokeWidth="2" strokeOpacity="0.8" />}
      <circle cx={pos.x} cy={pos.y} r={r} fill="#0F1828" stroke={color} strokeWidth={pos.centre ? 3 : 2.2} />
      <g transform={`translate(${pos.x - r * 0.6} ${pos.y - r * 0.6}) scale(${(r * 1.2) / 24})`} aria-hidden="true">
        <path d={KIND_PATHS[kind]} fill="none" stroke={color} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </g>
      <text x={pos.x} y={pos.y + r + 16} fontSize="11" textAnchor="middle" fill="#E7EAF0">{node.name}</text>
    </a>
  );
}

function SidePanel({ node, subjectName, edges }) {
  if (!node) {
    return <p className="text-sm text-paper-muted">Select a neuron to see what it exchanges.</p>;
  }
  const role = ROLES[node.role] ? node.role : "free";
  const kind = kindOf(nodeMember(node));
  const mine = edges.filter((e) => (e.a === node.name || e.b === node.name)
    && (subjectName == null || e.a === subjectName || e.b === subjectName || node.name === subjectName));
  const byCat = Object.fromEntries(CATEGORIES.map((c) => [c.key, 0]));
  for (const e of mine) byCat[e.category] += e.count;
  const max = Math.max(1, ...Object.values(byCat));
  const partner = subjectName && node.name !== subjectName ? subjectName : null;
  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <span className="inline-flex h-7 w-7 items-center justify-center rounded-full"
              style={{ boxShadow: `inset 0 0 0 2px ${ROLES[role].color}` }}>
          <KindIcon kind={kind} color={ROLES[role].color} size={16} />
        </span>
        <span className="font-bold">{node.name}</span>
      </div>
      <p className="mb-3 text-sm text-paper-muted">{`${ROLES[role].label} · ${KINDS[kind].label}`}{node.swarm ? ` · ${node.swarm}` : ""}</p>
      <ul className="mb-3 space-y-1 text-sm" aria-label={`What ${node.name} exchanges`}>
        {CATEGORIES.map((c) => (
          <li key={c.key} className="flex items-center gap-2">
            <CategoryLine category={c} width={20} />
            <span className="w-32">{c.label}</span>
            <span className="h-1.5 rounded" aria-hidden="true"
                  style={{ width: `${Math.max(2, (byCat[c.key] / max) * 90)}px`, background: c.color, opacity: byCat[c.key] ? 1 : 0.2 }} />
            <span className="w-10 text-right">{byCat[c.key]}</span>
          </li>
        ))}
      </ul>
      {partner && (
        <Link className="btn" to={`/conversation?members=${encodeURIComponent(`${partner},${node.name}`)}`}>
          Open conversation
        </Link>
      )}
    </div>
  );
}

/** Exchanges: the earlier neuron graph (#194), now a section of the table twin. */
export function Exchanges({ subject, name, graph }) {
  const [params, setParams] = useSearchParams();
  const [asTable, setAsTable] = useState(false);
  const selected = params.get("peer");
  function select(peer) {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("peer", peer);
      return next;
    }, { replace: true });
  }

  if (graph.loading && !graph.data) return <Loading what="Loading exchanges" />;
  if (graph.error) return <ErrorNote error={graph.error} onRetry={graph.reload} />;
  const { nodes = [], edges = [], relays = [] } = graph.data || {};
  const { positions } = layout(nodes, { type: subject, name }, { width: W, height: H });
  const threads = strands(edges, positions);
  const selectedNode = nodes.find((n) => n.name === selected) || null;

  return (
    <section aria-labelledby="exchanges-h">
      <style>{STYLE}</style>
      <div className="mb-2 flex items-center justify-between">
        <h3 id="exchanges-h" className="text-lg font-bold">Exchanges</h3>
        <button type="button" className="btn" aria-pressed={asTable} onClick={() => setAsTable((v) => !v)}>
          {asTable ? "View as map" : "View as table"}
        </button>
      </div>
      {edges.length === 0 && relays.length === 0 ? (
        <p className="text-sm text-paper-muted">No exchanges with other members in this range.</p>
      ) : asTable ? (
        <table className="w-full text-sm">
          <caption className="sr-only">Exchanges by pair and data category</caption>
          <thead><tr><th className="text-left">Members</th><th className="text-left">Data</th><th className="text-right">Records</th></tr></thead>
          <tbody>
            {edges.map((e) => (
              <tr key={`${e.a}-${e.b}-${e.category}`}><td>{`${e.a} ↔ ${e.b}`}</td><td>{category(e.category)?.label}</td>
                <td className="text-right">{e.count}</td></tr>
            ))}
            {relays.map((r) => (
              <tr key={`relay-${r.hub}-${r.member}`}><td>{`${r.hub} relays for ${r.member}`}</td><td>Relay path</td>
                <td className="text-right">{r.count}</td></tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_16rem]">
          <svg viewBox={`0 0 ${W} ${H}`} className="w-full rounded-md border border-ink-line bg-ink" role="group"
               aria-label="Members as neurons; each thread is one data type">
            <defs>
              {Object.entries(ROLES).map(([key, r]) => (
                <radialGradient key={key} id={`glow-${key}`}>
                  <stop offset="0%" stopColor={r.color} stopOpacity="0.35" />
                  <stop offset="100%" stopColor={r.color} stopOpacity="0" />
                </radialGradient>
              ))}
            </defs>
            {relays.map((r) => {
              const a = positions[r.hub];
              const b = positions[r.member];
              if (!a || !b) return null;
              return <line key={`relay-${r.hub}-${r.member}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                           stroke={RELAY} strokeOpacity="0.5" strokeWidth="1" />;
            })}
            {threads.map((t, i) => {
              const c = category(t.category);
              const dim = selected && t.a !== selected && t.b !== selected;
              return (
                <g key={i} opacity={dim ? 0.25 : 1}>
                  <path d={t.d} fill="none" stroke={c.color} strokeLinecap="round"
                        strokeWidth={t.primary ? 1.6 : 1} strokeOpacity={t.primary ? 1 : 0.4}
                        strokeDasharray={t.primary ? c.dash : undefined} />
                  {t.primary && (
                    <path d={t.d} fill="none" stroke={c.color} strokeWidth="2.4" strokeLinecap="round"
                          strokeDasharray="3 37" className={`ltms-impulse${t.category === "learn" ? " back" : ""}`} />
                  )}
                </g>
              );
            })}
            {nodes.map((n) => positions[n.name] && (
              <Neuron key={n.name} node={n} pos={positions[n.name]} selected={n.name === selected} onSelect={select} />
            ))}
          </svg>
          <aside aria-label="Selected member" className="panel p-3" aria-live="polite">
            <SidePanel node={selectedNode} subjectName={subject === "member" ? name : null} edges={edges} />
          </aside>
        </div>
      )}
      <p className="mt-2 text-xs text-paper-muted">
        Each thread is one data type. More threads = more traffic. Gray = relay path.
      </p>
    </section>
  );
}

// ---------------------------------------------------------------- Topology

const DAY_MS = 24 * 3600_000;
const ALL_DAYS = 365; // "All": the episodes API reads at most 366 days
const MAX_SHOWN = 24; // members whose records are fetched on a swarm profile
const LIVE_RELOAD_MS = 60_000;

/** [since, until] in epoch ms for a profile range ({hours}, 0 = all). */
export function rangeSpan(range, now = Date.now()) {
  const hours = range?.hours > 0 ? range.hours : ALL_DAYS * 24;
  return [now - Math.min(hours * 3600_000, ALL_DAYS * DAY_MS), now];
}

/** The swarm a profile shows: the swarm itself, or the member's swarm. */
export function swarmFor(subject, name, swarms) {
  const list = swarms || [];
  if (subject === "swarm") return list.find((s) => s.name === name) ?? null;
  return list.find((s) => (s.members || []).some((m) => m.name === name)) ?? null;
}

/** The roster: the swarm's members with their admin rows merged in (or the member alone). */
export function rosterFor(subject, name, swarm, members) {
  const rows = Object.fromEntries((members || []).map((m) => [m.name, m]));
  if (swarm) return (swarm.members || []).map((sm) => ({ ...rows[sm.name], name: sm.name, role: sm.role }));
  return subject === "member" ? [{ ...rows[name], name, role: null }] : [];
}

function useTopologyData({ subject, name, range, swarm, roster, reloadKey }) {
  const [state, setState] = useState({ loading: true, error: null, episodes: {}, network: [], models: {}, graph: null });
  const names = roster.map((m) => m.name);
  const shown = subject === "member" ? [name] : names.slice(0, MAX_SHOWN);
  const ids = roster.filter((m) => m.id).map((m) => [m.name, m.id]);
  const key = JSON.stringify([subject, name, range.hours, swarm?.name ?? null, shown, ids, reloadKey]);
  useEffect(() => {
    let live = true;
    const [since, until] = rangeSpan(range);
    const iso = (t) => new Date(t).toISOString();
    const optional = (p) => p.catch(() => null);
    setState((s) => ({ ...s, loading: true, error: null }));
    Promise.all([
      Promise.all(shown.map((m) => api("/admin/profile/episodes",
        { params: { member: m, since: iso(since), until: iso(until) } }).then((d) => [m, d]))),
      optional(api("/admin/network", { params: swarm ? { swarm: swarm.name } : { member: name } })),
      Promise.all(ids.map(([m, id]) => optional(api(`/admin/members/${encodeURIComponent(id)}/specs`))
        .then((d) => [m, d?.specs?.models?.value ?? []]))),
      optional(api("/admin/profile/graph", { params: { [subject]: name, hours: range.hours } })),
    ]).then(([eps, network, models, graph]) => {
      if (!live) return;
      setState({
        loading: false, error: null, since, until,
        episodes: Object.fromEntries(eps), network: network?.members ?? [],
        models: Object.fromEntries(models), graph,
      });
    }).catch((error) => {
      if (live) setState((s) => ({ ...s, loading: false, error }));
    });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return state;
}

function lastSeen(ts) {
  return ts ? relativeTime(ts) : "never";
}

export function TopologyTables({ scene, roster, t }) {
  const paths = pathRows(scene, t);
  const sources = sourceRows(scene);
  const members = memberRows(scene, roster);
  return (
    <div className="space-y-4">
      <HoloTable caption="Paths" rows={paths}
                 columns={[{ key: "from", label: "From" }, { key: "through", label: "Through" },
                   { key: "to", label: "To" }, { key: "records", label: "Records up to the hand", numeric: true }]} />
      {paths.length === 0 && <p className="text-sm text-paper-muted">Nothing has arrived up to the hand.</p>}
      <HoloTable caption="By source" rows={sources}
                 columns={[{ key: "member", label: "Member" },
                   { key: "label", label: "Source", render: (r) => r.label },
                   { key: "records", label: "Records in range", numeric: true }]} />
      <HoloTable caption="Members" rows={members}
                 columns={[{ key: "name", label: "Name" },
                   { key: "role", label: "Role", render: (r) => ROLES[r.role]?.label ?? r.role },
                   { key: "kind", label: "Kind", render: (r) => KINDS[kindOf(r)]?.label ?? "" },
                   { key: "hub", label: "Home hub" },
                   { key: "lastSeen", label: "Last seen", render: (r) => lastSeen(r.lastSeen) }]} />
    </div>
  );
}

export default function Topology({ subject, name, range, members, swarms }) {
  const [params] = useSearchParams();
  const [reloadKey, setReloadKey] = useState(0);
  const [selected, setSelected] = useState(null);
  const swarm = swarmFor(subject, name, swarms);
  const roster = useMemo(() => rosterFor(subject, name, swarm, members), [subject, name, swarm, members]);
  const data = useTopologyData({ subject, name, range, swarm, roster, reloadKey });

  const [clock] = useState(() => {
    const [since, until] = rangeSpan(range);
    return createReplayClock({ start: since, end: until });
  });
  useEffect(() => {
    if (data.since != null) clock.setRange(data.since, data.until);
  }, [clock, data.since, data.until]);
  // Live: fetch again now and then so new records join the system.
  useEffect(() => {
    const id = setInterval(() => {
      if (clock.getState().mode === "live") setReloadKey((k) => k + 1);
    }, LIVE_RELOAD_MS);
    return () => clearInterval(id);
  }, [clock]);

  const scene = useMemo(() => buildScene({
    roster, network: data.network, episodes: data.episodes, models: data.models,
    focus: subject === "member" ? name : null,
  }), [roster, data.network, data.episodes, data.models, subject, name]);
  const edges = data.graph?.edges;
  const touchables = touchList(scene, {
    bpm: (p) => (p.heartbeat?.interval_s ? heartbeatBpm(p.heartbeat.interval_s) : null),
  }).map((item) => ({
    ...item,
    // VizFrame plays the shared touch note, sound.note(item.note ?? item.id) (#428).
    onTouch: () => setSelected(item.id),
  }));
  const graph = { data: data.graph, loading: data.loading && !data.graph, error: null, reload: () => setReloadKey((k) => k + 1) };

  if (data.loading && !Object.keys(data.episodes).length && !data.error) return <Loading what="Loading the topology" />;
  return (
    <>
      <ErrorNote error={data.error} onRetry={() => setReloadKey((k) => k + 1)} />
      {data.since != null && (
        <VizFrame title="Topology" clock={clock} touchables={touchables} defaultCalm={params.get("peer") ? true : undefined}
                  renderTable={(frame) => (
                    <>
                      <TopologyTables scene={scene} roster={roster} t={frame.t} />
                      <div className="mt-4"><Exchanges subject={subject} name={name} graph={graph} /></div>
                    </>
                  )}>
          {(frame) => <SystemView scene={scene} frame={frame} edges={edges} selected={selected} />}
        </VizFrame>
      )}
      {scene.events.length === 0 && !data.loading && (
        <p className="mt-2 text-sm text-paper-muted">Nothing has arrived in this range.</p>
      )}
    </>
  );
}
