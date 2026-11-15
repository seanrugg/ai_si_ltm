// VizFrame: the Holo Light frame every visualization sits in (#400,
// docs/holo-light.md, "Common frame" and "Calm view and access").
//
// Props (all optional except title and children):
//   title        string. The view name, in Bebas Neue ("Chronology").
//   clock        a clock from lib/replay.js createReplayClock. Pass the profile
//                page's shared clock so tabs keep the replay time; without one
//                the frame makes its own. The frame drives it (useReplayDriver)
//                while mounted, so mount one frame per clock at a time.
//   children     (frame) => node. The view's drawing, on the `void` stage. The
//                stage is aria-hidden: put nothing interactive in it, and give
//                its meaning to `touchables`, `narration` and `renderTable`.
//   renderTable  (frame) => node. The table twin (use <HoloTable>). Shown first
//                in Calm view; the still view follows it.
//   narration    string | {text, self?, key?}. The latest notable record in
//                plain words. Shown in the narration line (aria-live polite) at
//                most once per 2 s; the newest pending text wins. Self-reports
//                ({self: true}) are skipped. `key` makes a repeat of the same
//                text count as new.
//   touchables   [{id, label, narration?, onTouch?}]. The touchable lights at
//                the current level, rendered as real buttons (Enter = click).
//                Touching narrates `narration ?? label`, then calls the item's
//                onTouch(item), or the frame's onTouch(item) if it has none.
//   onTouch      (item) => void. Default touch handler for touchables.
//   onEscape     () => void. Escape anywhere in the frame: go up one level.
//   legend       {categories?: ["sense", ...], selfReport?: bool, extra?: node},
//                or false to hide. Default: all four categories and the
//                self-report dot.
//   defaultCalm  bool. Start in Calm view. Default: the system's
//                prefers-reduced-motion, or the per-user setting
//                "Start visualizations in Calm view" (lib/light.js getCalmStart).
//   onCalmChange (calm) => void.   onSoundChange (on) => void.
//
// `frame`, as given to children and renderTable:
//   {t, mode, playing, speed, start, end,   from the clock (epoch ms)
//    calm, animate, sound, reducedMotion,    animate=false: no travel, breathing
//                                            or rotation (the hand still moves)
//    clock, narrate(text)}                   narrate pushes through the throttle
import "@fontsource/cormorant-garamond/500-italic.css";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CATEGORIES, CAT_SELF, category as categoryByKey } from "../lib/visual.js";
import { getCalmStart, prefersReducedMotion } from "../lib/light.js";
import { MINUTE, SPEEDS, createReplayClock, useReplay, useReplayDriver } from "../lib/replay.js";
import { CategoryLine } from "./visual.jsx";

export const NARRATION_INTERVAL_MS = 2000;

/** Narration, throttled to one update per interval; the newest pending text wins. */
export function useThrottledNarration(intervalMs = NARRATION_INTERVAL_MS) {
  const [shown, setShown] = useState("");
  const last = useRef(-Infinity);
  const timer = useRef(null);
  const pending = useRef(null);
  const push = useCallback((text) => {
    if (!text) return;
    pending.current = text;
    const wait = last.current + intervalMs - Date.now();
    if (wait <= 0) {
      last.current = Date.now();
      pending.current = null;
      setShown(text);
    } else if (!timer.current) {
      timer.current = setTimeout(() => {
        timer.current = null;
        const next = pending.current;
        pending.current = null;
        if (next) {
          last.current = Date.now();
          setShown(next);
        }
      }, wait);
    }
  }, [intervalMs]);
  useEffect(() => () => clearTimeout(timer.current), []);
  return [shown, push];
}

function normalizeNarration(n) {
  if (!n) return { text: "", self: false, key: undefined };
  if (typeof n === "string") return { text: n, self: false, key: undefined };
  return { text: n.text ?? "", self: Boolean(n.self), key: n.key };
}

function timeText(t) {
  if (!Number.isFinite(t)) return "";
  return new Date(t).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function Toggle({ pressed, onClick, children, disabled, title }) {
  return (
    <button type="button" className="viz-btn" aria-pressed={pressed ? "true" : "false"}
            onClick={onClick} disabled={disabled} title={title}>
      {children}
    </button>
  );
}

/** The time bar: the led hand on a led-dim track. role="slider"; ←/→ ±1 min, Space plays or pauses. */
export function TimeBar({ clock, state }) {
  const bar = useRef(null);
  const dragging = useRef(false);
  const span = Math.max(1, state.end - state.start);
  const frac = Math.min(1, Math.max(0, (state.t - state.start) / span));

  const seekAt = (clientX) => {
    const rect = bar.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0) return;
    const f = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    clock.seek(state.start + f * span);
  };
  const onKeyDown = (e) => {
    const moves = { ArrowLeft: -MINUTE, ArrowDown: -MINUTE, ArrowRight: MINUTE, ArrowUp: MINUTE,
                    PageDown: -10 * MINUTE, PageUp: 10 * MINUTE };
    if (e.key in moves) clock.step(moves[e.key]);
    else if (e.key === "Home") clock.seek(state.start);
    else if (e.key === "End") clock.seek(state.end);
    else if (e.key === " " || e.key === "Spacebar") clock.toggle();
    else return;
    e.preventDefault();
  };
  return (
    <div ref={bar} className="viz-timebar" role="slider" tabIndex={0} aria-label="Time"
         aria-valuemin={state.start} aria-valuemax={state.end} aria-valuenow={Math.round(state.t)}
         aria-valuetext={`${timeText(state.t)}${state.mode === "live" ? " (live)" : ""}`}
         onKeyDown={onKeyDown}
         onPointerDown={(e) => {
           dragging.current = true;
           e.currentTarget.setPointerCapture?.(e.pointerId);
           seekAt(e.clientX);
         }}
         onPointerMove={(e) => { if (dragging.current) seekAt(e.clientX); }}
         onPointerUp={() => { dragging.current = false; }}
         onPointerCancel={() => { dragging.current = false; }}>
      <span className="viz-timebar-track" aria-hidden="true" />
      <span className="viz-timebar-hand" aria-hidden="true" style={{ left: `${frac * 100}%` }} />
    </div>
  );
}

/** The legend row: each category's color, dash and name, and the self-report dot. */
export function VizLegend({ categories, selfReport = true, extra }) {
  const cats = (categories ?? CATEGORIES.map((c) => c.key)).map((k) => categoryByKey(k)).filter(Boolean);
  return (
    <div role="group" aria-label="Legend" className="viz-legend">
      {cats.map((c) => (
        <span key={c.key} className="inline-flex items-center gap-1.5">
          <CategoryLine category={c} width={24} />
          {c.label}
        </span>
      ))}
      {selfReport && (
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden="true" className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: CAT_SELF }} />
          Self-report (heartbeat)
        </span>
      )}
      {extra}
    </div>
  );
}

/**
 * A Holo table, for a view's table twin.
 *   caption   the question it answers ("Members", "Episodes"), in display type
 *   columns   [{key, label, numeric?, category?: (row) => "sense"|..., render?: (row) => node}]
 *             numeric: DM Mono, right-aligned. category: the category's line sample, then its name.
 *   rows      array;  rowKey (row, i) => key (default row.id ?? i)
 *   isLive    (row) => bool: the row true at the hand gets a live dot.
 */
export function HoloTable({ caption, columns, rows, rowKey = (r, i) => r.id ?? i, isLive }) {
  return (
    <div className="holo-table-wrap">
      <table className="holo-table">
        {caption && <caption>{caption}</caption>}
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} scope="col" className={c.numeric ? "num" : undefined}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const live = Boolean(isLive?.(row));
            return (
              <tr key={rowKey(row, i)} data-live={live ? "true" : undefined}>
                {columns.map((c, ci) => {
                  let content = c.render ? c.render(row) : row[c.key];
                  if (c.category) {
                    const cat = categoryByKey(c.category(row));
                    content = cat ? (
                      <span className="inline-flex items-center gap-1.5">
                        <CategoryLine category={cat} width={20} />
                        {c.render ? content : cat.label}
                      </span>
                    ) : content;
                  }
                  return (
                    <td key={c.key} className={c.numeric ? "num" : undefined}>
                      {ci === 0 && live && (
                        <>
                          <span className="holo-live-dot" aria-hidden="true" />
                          <span className="sr-only">Now: </span>
                        </>
                      )}
                      {content}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function VizFrame({
  title, clock: clockProp, children, renderTable, narration, touchables = [], onTouch, onEscape,
  legend, defaultCalm, onCalmChange, onSoundChange,
}) {
  const [ownClock] = useState(() => (clockProp ? null : createReplayClock()));
  const clock = clockProp ?? ownClock;
  const state = useReplay(clock);
  useReplayDriver(clock);

  const [reducedMotion] = useState(() => prefersReducedMotion());
  const [calm, setCalm] = useState(() => defaultCalm ?? (reducedMotion || getCalmStart()));
  const [soundOn, setSoundOn] = useState(false); // off by default
  const sound = soundOn && !calm;
  const animate = !calm && !reducedMotion;

  const [shown, narrate] = useThrottledNarration();
  const n = normalizeNarration(narration);
  useEffect(() => {
    if (n.text && !n.self) narrate(n.text);
  }, [n.text, n.self, n.key, narrate]);

  const chooseCalm = (on) => {
    setCalm(on);
    onCalmChange?.(on);
  };
  const chooseSound = (on) => {
    setSoundOn(on);
    onSoundChange?.(on);
  };
  const touch = (item) => {
    narrate(item.narration ?? item.label);
    if (item.onTouch) item.onTouch(item);
    else onTouch?.(item);
  };

  const frame = useMemo(() => ({
    ...state, calm, animate, sound, reducedMotion, clock, narrate,
  }), [state, calm, animate, sound, reducedMotion, clock, narrate]);

  const view = (
    <div className="viz-stage" aria-hidden="true" data-animate={animate ? "true" : "false"}>
      {typeof children === "function" ? children(frame) : children}
    </div>
  );
  const live = state.mode === "live";

  return (
    <section className="viz-frame" aria-label={title}
             onKeyDown={(e) => {
               if (e.key === "Escape" && onEscape) {
                 e.preventDefault();
                 onEscape();
               }
             }}>
      <div className="viz-header">
        <h2 className="viz-title">{title}</h2>
        <div className="viz-group" role="group" aria-label="Time mode">
          <Toggle pressed={live} onClick={() => clock.setMode("live")}>Live</Toggle>
          <Toggle pressed={!live} onClick={() => clock.setMode("replay")}>Replay</Toggle>
        </div>
        <Toggle pressed={state.playing} onClick={() => clock.toggle()}>
          <span aria-hidden="true">{state.playing ? "❚❚" : "▶"}</span> Play
        </Toggle>
        <div className="viz-group" role="group" aria-label="Replay speed">
          {SPEEDS.map((s) => (
            <Toggle key={s} pressed={state.speed === s} onClick={() => clock.setSpeed(s)}>{`${s}×`}</Toggle>
          ))}
        </div>
        <Toggle pressed={sound} onClick={() => chooseSound(!soundOn)} disabled={calm}
                title={calm ? "Sound is off in Calm view" : undefined}>
          Sound
        </Toggle>
        <div className="viz-group" role="group" aria-label="Show">
          <Toggle pressed={!calm} onClick={() => chooseCalm(false)}>View</Toggle>
          <Toggle pressed={calm} onClick={() => chooseCalm(true)}>Table</Toggle>
        </div>
      </div>

      {calm ? (
        <>
          {renderTable && <div className="p-3" data-testid="table-twin">{renderTable(frame)}</div>}
          {view}
        </>
      ) : view}

      <p className="viz-narrate" aria-live="polite">{shown}</p>

      {touchables.length > 0 && (
        <ul className="viz-touch" aria-label={`${title} lights`}>
          {touchables.map((item) => (
            <li key={item.id}>
              <button type="button" className="viz-btn" onClick={() => touch(item)}>{item.label}</button>
            </li>
          ))}
        </ul>
      )}

      <TimeBar clock={clock} state={state} />

      {legend !== false && <VizLegend {...(legend ?? {})} />}
    </section>
  );
}
