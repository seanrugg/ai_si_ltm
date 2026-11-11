// Profile Interactions tab: members as neurons, data categories as threads (issue #194).
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useApi } from "../../api/useApi.js";
import { ErrorNote, Loading } from "../../components/ui.jsx";
import { CategoryLine, KIND_PATHS, KindIcon } from "../../components/visual.jsx";
import { dendrites, layout, strands } from "../../lib/synapse.js";
import { CATEGORIES, KINDS, RELAY, ROLES, category, kindOf } from "../../lib/visual.js";

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

export default function Interactions({ subject, name, range }) {
  const [params, setParams] = useSearchParams();
  const [asTable, setAsTable] = useState(false);
  const graph = useApi("/admin/profile/graph", { [subject]: name, hours: range.hours });
  const selected = params.get("peer");
  function select(peer) {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("peer", peer);
      return next;
    }, { replace: true });
  }

  if (graph.loading && !graph.data) return <Loading what="Loading interactions" />;
  if (graph.error) return <ErrorNote error={graph.error} onRetry={graph.reload} />;
  const { nodes = [], edges = [], relays = [] } = graph.data || {};
  const { positions } = layout(nodes, { type: subject, name }, { width: W, height: H });
  const threads = strands(edges, positions);
  const selectedNode = nodes.find((n) => n.name === selected) || null;

  return (
    <section aria-labelledby="interactions-h">
      <style>{STYLE}</style>
      <div className="mb-2 flex items-center justify-between">
        <h2 id="interactions-h" className="text-lg font-bold">Interactions</h2>
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
