// The Home view (issue #196): a home network's rooms, members and live flows.
import { useEffect, useState } from "react";
import { api } from "../../api/client.js";
import { flowWidth, ratesFrom, roomsOf } from "../../lib/sites.js";
import { CATEGORIES, KINDS, NEUTRAL, ROLES, kindOf } from "../../lib/visual.js";
import { KIND_PATHS } from "../../components/visual.jsx";
import { FLOW_STYLE } from "./Globe.jsx";

const W = 640;
const H = 440;
const POLL_MS = 10000;

/** Grid of room rectangles and member positions inside them. */
export function homeLayout(rooms, { width = W, height = H - 60 } = {}) {
  const cols = Math.max(1, Math.ceil(Math.sqrt(rooms.length)));
  const rows = Math.max(1, Math.ceil(rooms.length / cols));
  const rw = width / cols;
  const rh = height / rows;
  const positions = {};
  const boxes = rooms.map((r, i) => {
    const x = (i % cols) * rw + 8;
    const y = Math.floor(i / cols) * rh + 8;
    const box = { room: r.room, x, y, w: rw - 16, h: rh - 16 };
    r.members.forEach((name, j) => {
      const step = box.w / (r.members.length + 1);
      positions[name] = { x: box.x + step * (j + 1), y: box.y + box.h / 2 };
    });
    return box;
  });
  return { boxes, positions };
}

export default function HomeView({ site, netRows, locRows, members, onSelectMember, onExit }) {
  const [zoom, setZoom] = useState(1);
  const [rates, setRates] = useState({});
  const rooms = roomsOf(locRows, site.members);
  const { boxes, positions } = homeLayout(rooms);
  const net = Object.fromEntries(netRows.map((r) => [r.name, r]));
  const hub = site.members.find((n) => net[n]?.role === "hub") || null;
  const exit = { x: W - 40, y: H - 24 };
  const hubPos = hub ? positions[hub] : null;
  const publicIp = (hub && net[hub]?.seen?.ip) || site.members.map((n) => net[n]?.seen?.ip).find(Boolean) || "unknown";
  const serverAddress = typeof window !== "undefined" ? window.location.host : "LTMS";

  const key = site.members.join(",");
  useEffect(() => {
    let live = true;
    async function poll() {
      const out = {};
      await Promise.all(key.split(",").filter(Boolean).map(async (n) => {
        try {
          out[n] = ratesFrom(await api("/admin/profile/summary", { params: { member: n, hours: 1 } }));
        } catch {
          out[n] = ratesFrom(null);
        }
      }));
      if (live) setRates(out);
    }
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => { live = false; clearInterval(id); };
  }, [key]);

  const max = Math.max(0.01, ...Object.values(rates).flatMap((r) => Object.values(r)));
  function zoomBy(f) {
    const next = zoom * f;
    if (next < 1) onExit();
    else setZoom(Math.min(3, next));
  }

  return (
    <div>
      <style>{FLOW_STYLE}</style>
      <div className="overflow-hidden rounded-md border border-ink-line bg-ink">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" onDoubleClick={() => zoomBy(2)}
             aria-label={`Home network at ${site.label}: ${rooms.length} rooms. The members table below lists every address.`}
             style={{ transform: `scale(${zoom})`, transformOrigin: "center" }}>
          {boxes.map((b) => (
            <g key={b.room}>
              <rect x={b.x} y={b.y} width={b.w} height={b.h} rx="8" fill="none" stroke={NEUTRAL} strokeOpacity="0.35" />
              <text x={b.x + 8} y={b.y + 16} fontSize="11" fill="#93A0B8">{b.room}</text>
            </g>
          ))}
          {site.members.map((n) => {
            const from = positions[n];
            if (!from || n === hub) return null;
            const to = net[n]?.path?.startsWith("via ") && hubPos ? hubPos : exit;
            const flows = CATEGORIES.filter((c) => (rates[n]?.[c.key] || 0) > 0);
            const dx = to.x - from.x;
            const dy = to.y - from.y;
            const len = Math.hypot(dx, dy) || 1;
            return flows.map((c, i) => {
              const off = (i - (flows.length - 1) / 2) * 5;
              const ox = (-dy / len) * off;
              const oy = (dx / len) * off;
              return (
                <line key={`${n}-${c.key}`} x1={from.x + ox} y1={from.y + oy} x2={to.x + ox} y2={to.y + oy}
                      stroke={c.color} strokeWidth={flowWidth(rates[n][c.key], max)} strokeDasharray={c.dash}
                      className={`ltms-flow${c.key === "learn" ? " in" : ""}`} strokeOpacity="0.85" />
              );
            });
          })}
          {hubPos && CATEGORIES.map((c, i) => {
            const total = site.members.reduce((s, n) => s + (rates[n]?.[c.key] || 0), 0);
            return total > 0 ? (
              <line key={`hub-${c.key}`} x1={hubPos.x} y1={hubPos.y + (i - 1.5) * 5} x2={exit.x} y2={exit.y + (i - 1.5) * 5}
                    stroke={c.color} strokeWidth={flowWidth(total, max * site.members.length)} strokeDasharray={c.dash}
                    className={`ltms-flow${c.key === "learn" ? " in" : ""}`} />
            ) : null;
          })}
          {site.members.map((n) => {
            const p = positions[n];
            if (!p) return null;
            const m = members.find((x) => x.name === n) || { name: n };
            const role = ROLES[net[n]?.role] ? net[n].role : "free";
            const kind = kindOf({ ...m, role });
            const ip = net[n]?.reported?.[0]?.ip || net[n]?.seen?.ip || "";
            return (
              <a key={n} href={`#member-${n}`} aria-label={`${n}, ${ROLES[role].label} · ${KINDS[kind].label}`}
                 onClick={(e) => { e.preventDefault(); onSelectMember(n); }}>
                <circle cx={p.x} cy={p.y} r="13" fill="#0F1828" stroke={ROLES[role].color} strokeWidth="2.5" />
                <g transform={`translate(${p.x - 8} ${p.y - 8}) scale(${16 / 24})`} aria-hidden="true">
                  <path d={KIND_PATHS[kind]} fill="none" stroke={ROLES[role].color} strokeWidth="1.8" strokeLinecap="round" />
                </g>
                <text x={p.x} y={p.y + 26} fontSize="11" textAnchor="middle" fill="#E7EAF0">{n}</text>
                <text x={p.x} y={p.y + 38} fontSize="9" textAnchor="middle" fill="#93A0B8">{ip}</text>
              </a>
            );
          })}
          <g>
            <path d={`M${exit.x - 8} ${exit.y - 8}l16 8-16 8z`} fill={NEUTRAL} />
            <text x={exit.x - 14} y={exit.y + 4} fontSize="10" textAnchor="end" fill="#E7EAF0">
              {`to LTMS · ${publicIp} → ${serverAddress}`}
            </text>
          </g>
        </svg>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2" role="group" aria-label="Home view controls">
        <button type="button" className="btn" onClick={() => zoomBy(2)} disabled={zoom >= 3}>Zoom in</button>
        <button type="button" className="btn" onClick={() => zoomBy(0.5)}>Zoom out</button>
        <button type="button" className="btn" onClick={onExit}>Back to globe</button>
        <span className="text-xs text-paper-muted">{`Home: ${site.label} · zoom ${zoom}×`}</span>
      </div>
    </div>
  );
}
