// The shared replay clock (#400, docs/holo-light.md). One clock per profile
// page: every view (tab) reads the same {t, playing, speed}, so switching tabs
// keeps the replay time.
//
// Usage on a profile page:
//   const clock = useMemo(() => createReplayClock({ start, end }), []);
//   <ReplayContext.Provider value={clock}> ... <VizFrame clock={clock} .../> ...
// A view reads it with useReplay(clock) (or useReplay() under the provider).
import { createContext, useContext, useEffect, useSyncExternalStore } from "react";

export const SPEEDS = [30, 60, 120, 240];
export const DEFAULT_SPEED = 60;
export const MINUTE = 60_000;
const LIVE_STEP_MS = 250;

const clampTo = (t, start, end) => Math.min(end, Math.max(start, t));

/**
 * A replay clock. State: {mode, t, playing, speed, start, end}, times in epoch
 * ms. `mode` is "live" (t follows now, end is now) or "replay" (t plays the
 * range [start, end] at `speed`× wall time). `now` is injectable for tests.
 *
 * Methods: getState, subscribe(fn) → unsubscribe, play, pause, toggle,
 * seek(t), step(deltaMs), setSpeed(x), setMode("live"|"replay"),
 * setRange(start, end), tick(wallMs). Call tick from an animation loop (see
 * useReplayDriver); it advances t by the wall time since the last tick.
 */
export function createReplayClock({ start, end, t, mode = "live", speed = DEFAULT_SPEED, now = Date.now } = {}) {
  const n = now();
  let state = {
    mode,
    start: start ?? n - 24 * 3600_000,
    end: end ?? n,
    t: 0,
    playing: mode === "live",
    speed: SPEEDS.includes(speed) ? speed : DEFAULT_SPEED,
  };
  if (mode === "live") state.end = Math.max(state.end, n);
  state.t = clampTo(t ?? (mode === "live" ? n : state.start), state.start, state.end);
  let lastWall = null;
  const listeners = new Set();

  function set(patch) {
    const next = { ...state, ...patch };
    if (next.t === state.t && next.playing === state.playing && next.speed === state.speed
        && next.mode === state.mode && next.start === state.start && next.end === state.end) return;
    state = next;
    for (const fn of listeners) fn(state);
  }

  const clock = {
    getState: () => state,
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    play() {
      lastWall = null;
      if (state.mode === "replay" && state.t >= state.end) set({ playing: true, t: state.start });
      else set({ playing: true });
    },
    /** Pausing Live freezes the hand: it becomes a paused replay at that moment. */
    pause() {
      lastWall = null;
      set({ playing: false, mode: "replay" });
    },
    toggle() {
      if (state.playing) clock.pause();
      else clock.play();
    },
    /** Move the hand. Seeking leaves Live: the hand only sits at now in Live. */
    seek(t) {
      lastWall = null;
      if (state.mode === "live") {
        set({ mode: "replay", playing: false, end: now(), t: clampTo(t, state.start, now()) });
      } else {
        set({ t: clampTo(t, state.start, state.end) });
      }
    },
    step(deltaMs) {
      clock.seek(state.t + deltaMs);
    },
    setSpeed(speed) {
      if (SPEEDS.includes(speed)) set({ speed });
    },
    setMode(mode) {
      lastWall = null;
      if (mode === "live") {
        const n2 = now();
        set({ mode: "live", playing: true, end: Math.max(state.end, n2), t: n2 });
      } else if (mode === "replay") {
        set({ mode: "replay", t: state.t >= state.end ? state.start : state.t });
      }
    },
    setRange(start, end) {
      const s = Math.min(start, end);
      let e = Math.max(start, end);
      if (state.mode === "live") e = Math.max(e, now());
      set({ start: s, end: e, t: clampTo(state.t, s, e) });
    },
    /** Advance by the wall time since the last tick. Returns the new state. */
    tick(wallMs) {
      const prev = lastWall;
      lastWall = wallMs;
      if (state.mode === "live") {
        // The live hand needs no more than a few moves a second.
        const n2 = now();
        if (n2 - state.t >= LIVE_STEP_MS) set({ t: n2, end: Math.max(state.end, n2) });
        return state;
      }
      if (!state.playing || prev == null) return state;
      const t2 = state.t + (wallMs - prev) * state.speed;
      if (t2 >= state.end) set({ t: state.end, playing: false });
      else set({ t: t2 });
      return state;
    },
  };
  return clock;
}

// ---------------------------------------------------------------- event cursor

/** Index of the first event with time > t, in events sorted by time (binary search). */
export function indexAfter(events, t, timeOf = (e) => e.t) {
  let lo = 0, hi = events.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (timeOf(events[mid]) <= t) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/**
 * An event cursor over events sorted by time. `advance(t)` returns
 * {fresh, reset}: the events in (previous t, t], and reset=true when the hand
 * moved backwards (the view should redraw from `upTo(t)`, nothing found
 * disappears but future events go back to asleep). `upTo(t)` is every event at
 * or before t.
 */
export function createEventCursor(events, timeOf = (e) => e.t) {
  let pos = 0;
  let lastT = -Infinity;
  return {
    advance(t) {
      if (t < lastT) {
        pos = indexAfter(events, t, timeOf);
        lastT = t;
        return { fresh: [], reset: true };
      }
      const next = indexAfter(events, t, timeOf);
      const fresh = events.slice(pos, next);
      pos = next;
      lastT = t;
      return { fresh, reset: false };
    },
    upTo(t) {
      return events.slice(0, indexAfter(events, t, timeOf));
    },
    get position() {
      return pos;
    },
  };
}

// ---------------------------------------------------------------- React

/** The profile page's shared clock. Provide it once; frames and views read it. */
export const ReplayContext = createContext(null);

/** Subscribe to a clock's state (the given clock, else the one in ReplayContext). */
export function useReplay(clock) {
  const fromContext = useContext(ReplayContext);
  const c = clock ?? fromContext;
  return useSyncExternalStore(
    c ? c.subscribe : noopSubscribe,
    c ? c.getState : nullState,
    c ? c.getState : nullState,
  );
}
const noopSubscribe = () => () => {};
const nullState = () => null;

/**
 * Drive a clock: tick it every animation frame while it is Live or playing.
 * Run it once per clock (VizFrame does, for the clock it shows).
 */
export function useReplayDriver(clock, active = true) {
  useEffect(() => {
    if (!clock || !active) return undefined;
    const raf = typeof requestAnimationFrame === "function"
      ? requestAnimationFrame : (fn) => setTimeout(() => fn(performance.now()), 50);
    const caf = typeof cancelAnimationFrame === "function" ? cancelAnimationFrame : clearTimeout;
    let id = null;
    let stopped = false;
    const loop = (wall) => {
      if (stopped) return;
      const s = clock.getState();
      if (s.mode === "live" || s.playing) clock.tick(wall);
      id = raf(loop);
    };
    id = raf(loop);
    return () => {
      stopped = true;
      caf(id);
    };
  }, [clock, active]);
}
