// The offline globe (issue #195): drag to rotate, double-click to zoom on a
// point, buttons for keyboard and touch. The wheel is left to the page.
import { useMemo, useRef } from "react";
import { feature } from "topojson-client";
import land110 from "world-atlas/land-110m.json";
import { MAX_ZOOM, MIN_ZOOM, arc, arcPath, clampZoom, graticule, pathFor, project, ringsOf, zoomAt } from "../../lib/globe.js";
import { haloPx } from "../../lib/sites.js";
import { CATEGORIES, NEUTRAL, RELAY } from "../../lib/visual.js";

const W = 640;
const H = 440;
const CX = W / 2;
const CY = H / 2;
const BASE_R = 190;
const LAND = ringsOf(feature(land110, land110.objects.land));

export const FLOW_STYLE = `
@keyframes ltms-flow { to { stroke-dashoffset: -28; } }
@keyframes ltms-flow-in { to { stroke-dashoffset: 28; } }
.ltms-flow { animation: ltms-flow 1.4s linear infinite; }
.ltms-flow.in { animation-name: ltms-flow-in; }
@media (prefers-reduced-motion: reduce) { .ltms-flow { animation: none; } }
`;

export default function Globe({ view, setView, sites, server, rates, home, onHome, onSelectSite }) {
  const R = BASE_R * view.zoom;
  const drag = useRef(null);
  const svgRef = useRef(null);
  const landPaths = useMemo(() => pathFor(LAND, view, R, CX, CY), [view, R]);
  const grid = useMemo(() => graticule(view, R, CX, CY), [view, R]);

  function toSvg(e) {
    const box = svgRef.current.getBoundingClientRect();
    return { x: ((e.clientX - box.left) / box.width) * W, y: ((e.clientY - box.top) / box.height) * H };
  }
  function onPointerDown(e) {
    drag.current = { ...toSvg(e), view };
    e.currentTarget.setPointerCapture?.(e.pointerId);
  }
  function onPointerMove(e) {
    if (!drag.current) return;
    const p = toSvg(e);
    const k = 180 / (Math.PI * R);
    setView({
      ...view,
      lon: drag.current.view.lon - (p.x - drag.current.x) * k,
      lat: Math.max(-89, Math.min(89, drag.current.view.lat + (p.y - drag.current.y) * k)),
    });
  }
  function onPointerUp() {
    drag.current = null;
  }
  function onDoubleClick(e) {
    const p = toSvg(e);
    const out = zoomAt(p.x, p.y, view, R, CX, CY, home);
    if (!out) return;
    if (out.home) onHome();
    else setView(out.view);
  }
  const zoomBy = (f) => setView({ ...view, zoom: clampZoom(view.zoom * f) });
  const rotate = (d) => setView({ ...view, lon: view.lon + d / view.zoom });

  return (
    <div>
      <style>{FLOW_STYLE}</style>
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="w-full select-none rounded-md border border-ink-line bg-ink"
           style={{ touchAction: "none" }} role="img"
           aria-label="Globe of where members connect from, with data flowing to the server. The buttons below list every site and member."
           onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp}
           onPointerLeave={onPointerUp} onDoubleClick={onDoubleClick}>
        <circle cx={CX} cy={CY} r={R} fill="#0b1626" stroke={RELAY} strokeOpacity="0.6" />
        <path d={grid} fill="none" stroke={RELAY} strokeOpacity="0.25" strokeWidth="0.6" />
        <path d={landPaths.fill} fill="#1d3352" stroke="#2c4a72" strokeWidth="0.6" />
        <path d={landPaths.stroke} fill="none" stroke="#2c4a72" strokeWidth="0.8" />
        {server && sites.map((s) => CATEGORIES.map((c, i) => {
          const rate = rates[s.key]?.[c.key] || 0;
          if (!(rate > 0)) return null;
          const d = arcPath(arc(s, server, 0.06 + 0.035 * i), view, R, CX, CY);
          return d ? (
            <g key={`${s.key}-${c.key}`}>
              <path d={d} fill="none" stroke={c.color} strokeWidth="1.4" strokeDasharray={c.dash} strokeOpacity="0.8" />
              <path d={d} fill="none" stroke={c.color} strokeWidth="2.2" strokeDasharray="3 25"
                    className={`ltms-flow${c.key === "learn" ? " in" : ""}`} />
            </g>
          ) : null;
        }))}
        {sites.map((s) => {
          const p = project(s.lon, s.lat, view, R, CX, CY);
          if (!p.visible) return null;
          return (
            <g key={s.key} onClick={() => onSelectSite(s)} style={{ cursor: "pointer" }}>
              {s.source === "network" && s.accuracy_km != null && (
                <circle cx={p.x} cy={p.y} r={haloPx(s.accuracy_km, R)} fill={NEUTRAL} fillOpacity="0.08"
                        stroke={NEUTRAL} strokeOpacity="0.3" strokeDasharray="2 3" />
              )}
              <circle cx={p.x} cy={p.y} r={s.home ? 6 : 4.5} fill={NEUTRAL} />
              <text x={p.x + 8} y={p.y - 6} fontSize="11" fill="#E7EAF0">{s.label}</text>
              <text x={p.x + 8} y={p.y + 7} fontSize="9" fill="#93A0B8">{s.note}</text>
            </g>
          );
        })}
        {server && (() => {
          const p = project(server.lon, server.lat, view, R, CX, CY);
          return p.visible ? (
            <g>
              <rect x={p.x - 5} y={p.y - 5} width="10" height="10" fill="none" stroke={NEUTRAL} strokeWidth="2" />
              <text x={p.x + 9} y={p.y + 4} fontSize="11" fill="#E7EAF0">{server.label || "LTMS server"}</text>
            </g>
          ) : null;
        })()}
      </svg>
      <div className="mt-2 flex flex-wrap items-center gap-2" role="group" aria-label="Globe controls">
        <button type="button" className="btn" onClick={() => zoomBy(2)} disabled={view.zoom >= MAX_ZOOM}>Zoom in</button>
        <button type="button" className="btn" onClick={() => zoomBy(0.5)} disabled={view.zoom <= MIN_ZOOM}>Zoom out</button>
        <button type="button" className="btn" onClick={() => rotate(-20)}>Rotate west</button>
        <button type="button" className="btn" onClick={() => rotate(20)}>Rotate east</button>
        <button type="button" className="btn" onClick={() => setView(null)}>Reset</button>
        <span className="text-xs text-paper-muted">Drag to rotate · double-click to zoom · zoom {view.zoom}×</span>
      </div>
    </div>
  );
}
