// Holo Light sound (#402, docs/holo-light.md, "Motion and sound").
//
// One shared engine for every visualization. Off by default; VizFrame's Sound
// toggle turns it on, and that click is the user gesture browsers need to
// start audio, so there is no start screen. It is soft, sine-only, pentatonic,
// and held to a 60 BPM beat clock in wall time, so a burst of records never
// turns into noise. Calm view and reduced motion silence it (VizFrame holds it).
//
// Views import the shared engine and call it whether or not sound is on; when
// it is off or held, every call is a no-op (heartbeat still returns the beat
// for the light):
//
//   import { sound } from "../lib/sound.js";
//
//   sound.pulse("sense")                 a record arrived (sense|act|comm|learn).
//   sound.pulse("act", {failed: true})   a failed outcome: same voice, one step lower.
//       Quantized: it waits for its voice's next slot on the beat clock. Many
//       records in one beat give one note, slightly louder (capped).
//   sound.note("rover-1")                a light was touched: one short note now
//   sound.note(3)                        (a scale degree, or any key: the same key
//                                        always gets the same pitch).
//   const hb = sound.heartbeat(memberId, {intervalSec, sinceLastSec})
//       Call every animation frame while the clock plays. intervalSec: the
//       member's usual self-report interval; sinceLastSec: replay/live time
//       since its last self-report (null if none). Returns
//       {bpm, level, alive, slowing, lastBeat, swell}; draw the member's light
//       with `swell` (0..1, heartbeatSwell × level) so light and sound beat
//       together (skip the swell when frame.animate is false). Only one
//       member's heartbeat is audible at a time (the first one heard keeps it
//       until it goes quiet or stops being called); pass {audible: false} for
//       members that should only beat visually.
//   sound.forget(memberId)               drop a member's heartbeat state.
//
// VizFrame uses: sound.setEnabled(on), sound.hold(reason) → release,
// sound.subscribe / sound.getState (or useSound()).
//
// The engine is testable without audio: createSound({AudioContext, now,
// storage, autoTick: false, onVoice}) and call tick(nowMs) yourself.
import { useSyncExternalStore } from "react";
import { HEARTBEAT, heartbeatBpm, heartbeatSwell, smoothstep } from "./light.js";

// ---------------------------------------------------------------- constants

export const BPM = 60;
export const BEAT_MS = 60_000 / BPM;
export const TICK_MS = BEAT_MS / 2; // eighth-note grid
export const TICKS_PER_BAR = 8; // 4 beats
export const MASTER_GAIN = 0.07; // about −30 dBFS
export const LOWPASS_HZ = 2200;
export const ROOM = { delay: 0.25, feedback: 0.2, wet: 0.35 };
/** Relative loudness of one beat's voice: 1 record → base, each more adds step, up to cap. */
export const LEVEL = { base: 0.6, step: 0.08, cap: 1 };
/** Self-report interval assumed until a member has two reports. */
export const DEFAULT_INTERVAL_SEC = 30;
export const SOUND_KEY = "ltms.sound";

const D3 = 146.83;
const PENTA = [0, 2, 4, 7, 9]; // D major pentatonic: D E F# A B
const MIN_LEVEL = 0.0001;

/** Frequency of a D major pentatonic degree (0 = D3; 5 = D4; −1 = B2) in `octave` above that. */
export function pitch(degree, octave = 0) {
  const d = Math.round(degree);
  const oct = octave + Math.floor(d / 5);
  const step = PENTA[((d % 5) + 5) % 5];
  return D3 * 2 ** ((step + 12 * oct) / 12);
}

/** The level for `n` records merged into one beat. */
export function beatLevel(n) {
  if (!(n > 0)) return 0;
  return Math.min(LEVEL.cap, LEVEL.base + LEVEL.step * (n - 1));
}

// slot: "down" = on the beat, "off" = the eighth between, "bar" = at most once per bar.
// degrees cycle with the beat so a steady stream is a gentle figure, not one repeated pitch.
export const VOICES = {
  sense: { slot: "down", degrees: [0, 1, 2, 3], octave: 0, vol: 0.5, attack: 0.04, decay: 1.1 }, // D3–A3
  act: { slot: "off", degrees: [2, 3, 4, 3], octave: 0, vol: 0.4, attack: 0.05, decay: 1.3, partial: 0.25 }, // F#3–B3
  comm: { slot: "bar", degrees: [3, 4, 5], octave: 1, vol: 0.32, attack: 0.04, decay: 2.4 }, // A4–D5
  learn: { slot: "down", degrees: [0], octave: 0, vol: 0.18, pad: true }, // root, 2 s swell
};
const TOUCH = { octave: 1, vol: 0.28, attack: 0.03, decay: 1.0 };

/** The scale degree (0..4) a touch key plays: numbers as given, strings hashed. */
export function touchDegree(key) {
  if (typeof key === "number" && Number.isFinite(key)) return Math.round(key);
  const s = String(key ?? "");
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h) % 5;
}

// ---------------------------------------------------------------- heartbeat

/**
 * A member's heartbeat from its self-reports. Pure.
 *   intervalSec   the usual interval between its self-reports (missing → 30 s)
 *   sinceLastSec  time since its last self-report (null → never reported: quiet)
 * Rate 48–60 BPM (light.js heartbeatBpm). Over 2 missed intervals it slows by 8;
 * between 3 and 4 it fades; after 4 it is silent (alive false). No alarm.
 */
export function heartbeatState({ intervalSec, sinceLastSec } = {}) {
  if (sinceLastSec == null || !Number.isFinite(sinceLastSec)) {
    return { bpm: 0, level: 0, alive: false, slowing: false, missed: Infinity };
  }
  const iv = Number.isFinite(intervalSec) && intervalSec > 0 ? intervalSec : DEFAULT_INTERVAL_SEC;
  const missed = Math.max(0, sinceLastSec) / iv;
  if (missed >= 4) return { bpm: 0, level: 0, alive: false, slowing: true, missed };
  const slowing = missed > 2;
  const base = heartbeatBpm(iv);
  return { bpm: slowing ? base - 8 : base, level: 1 - smoothstep(3, 4, missed), alive: true, slowing, missed };
}

// ---------------------------------------------------------------- preference

/** The remembered Sound choice (this browser). Off unless turned on. */
export function getSoundPref(storage) {
  try {
    return (storage ?? window.localStorage).getItem(SOUND_KEY) === "1";
  } catch {
    return false;
  }
}

export function setSoundPref(on, storage) {
  try {
    const s = storage ?? window.localStorage;
    if (on) s.setItem(SOUND_KEY, "1");
    else s.removeItem(SOUND_KEY);
  } catch {
    // storage unavailable: the choice applies for this visit only
  }
}

// ---------------------------------------------------------------- engine

function defaultAudioContext() {
  if (typeof window === "undefined") return null;
  return window.AudioContext || window.webkitAudioContext || null;
}

/**
 * Create a sound engine. Options (all optional, for tests):
 *   AudioContext  constructor (default window.AudioContext)
 *   now           wall clock in ms (default Date.now)
 *   storage       localStorage-like, for the remembered choice
 *   autoTick      run the beat clock on timers (default true); false: call tick()
 *   onVoice       ({voice, hz, level, failed}) => void, for each sound played
 */
export function createSound({
  AudioContext: AC, now = Date.now, storage, autoTick = true, onVoice,
} = {}) {
  let wanted = getSoundPref(storage);
  const holds = new Map(); // token → reason
  let ctx = null;
  let master = null;
  let pending = {};
  let lastTick = null;
  let commBar = null;
  let timer = null;
  const hearts = new Map();
  let heartOwner = null;
  const listeners = new Set();
  let snapshot = null;

  const active = () => wanted && holds.size === 0;

  function emit() {
    snapshot = null;
    for (const fn of listeners) fn();
  }

  function getState() {
    if (!snapshot) {
      const reasons = [...holds.values()];
      snapshot = {
        on: wanted,
        held: reasons.length > 0,
        reason: reasons[0] ?? null,
        playing: active() && ctx != null,
      };
    }
    return snapshot;
  }

  function subscribe(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  }

  function ensureContext() {
    if (ctx) return ctx;
    const Ctor = AC ?? defaultAudioContext();
    if (!Ctor) return null;
    try {
      ctx = new Ctor();
      master = ctx.createGain();
      master.gain.value = MASTER_GAIN;
      const lp = ctx.createBiquadFilter();
      lp.type = "lowpass";
      lp.frequency.value = LOWPASS_HZ;
      const delay = ctx.createDelay(1);
      delay.delayTime.value = ROOM.delay;
      const fb = ctx.createGain();
      fb.gain.value = ROOM.feedback;
      const wet = ctx.createGain();
      wet.gain.value = ROOM.wet;
      master.connect(lp);
      lp.connect(ctx.destination);
      lp.connect(delay);
      delay.connect(fb);
      fb.connect(delay);
      delay.connect(wet);
      wet.connect(ctx.destination);
    } catch {
      ctx = null;
      master = null;
    }
    return ctx;
  }

  function resume() {
    if (ctx?.state === "suspended") ctx.resume?.()?.catch?.(() => {});
  }

  // A remembered "on" can't start audio before the person interacts: wait for
  // their first press or key anywhere, which counts as the gesture.
  let armed = false;
  function armGesture() {
    if (armed || ctx || typeof document === "undefined") return;
    armed = true;
    const start = () => {
      document.removeEventListener("pointerdown", start, true);
      document.removeEventListener("keydown", start, true);
      armed = false;
      if (wanted) {
        ensureContext();
        resume();
        emit();
      }
    };
    document.addEventListener("pointerdown", start, true);
    document.addEventListener("keydown", start, true);
  }

  // One sine voice: attack, then an exponential fall to silence by `decay` s.
  function tone(hz, { vol, attack = 0.04, decay = 1.2, partial = 0, when = 0 }) {
    const t0 = ctx.currentTime + when;
    const voice = (f, v, len) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = "sine";
      o.frequency.value = f;
      g.gain.setValueAtTime(0, t0);
      g.gain.linearRampToValueAtTime(v, t0 + attack);
      g.gain.exponentialRampToValueAtTime(MIN_LEVEL, t0 + Math.max(len, attack + 0.01));
      o.connect(g);
      g.connect(master);
      o.start(t0);
      o.stop(t0 + len + 0.05);
    };
    voice(hz, vol, decay);
    if (partial) voice(hz * 2, vol * partial, decay * 0.6);
  }

  // The learning pad: a 2 s swell on the root with a quiet fifth.
  function pad(hz, vol) {
    const t0 = ctx.currentTime;
    [1, 1.5].forEach((m, i) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = "sine";
      o.frequency.value = hz * m;
      g.gain.setValueAtTime(0, t0);
      g.gain.linearRampToValueAtTime(vol / (i + 1), t0 + 2);
      g.gain.exponentialRampToValueAtTime(MIN_LEVEL, t0 + 5);
      o.connect(g);
      g.connect(master);
      o.start(t0);
      o.stop(t0 + 5.1);
    });
  }

  function canPlay() {
    if (!active()) return false;
    if (!ctx) return false;
    resume();
    return true;
  }

  function playVoice(name, idx, level, failed) {
    if (!canPlay()) return;
    const v = VOICES[name];
    const degree = v.degrees[idx % v.degrees.length] - (failed ? 1 : 0);
    const hz = pitch(degree, v.octave);
    if (v.pad) pad(hz, v.vol * level);
    else tone(hz, { vol: v.vol * level, attack: v.attack, decay: v.decay, partial: v.partial });
    onVoice?.({ voice: name, hz, level, failed });
  }

  /** Advance the beat clock to wall time `t` and play what is due. Safe to call often. */
  function tick(t = now()) {
    const idx = Math.floor(t / TICK_MS);
    if (idx === lastTick) return;
    lastTick = idx;
    if (!active()) {
      pending = {};
      return;
    }
    const down = idx % 2 === 0;
    const beat = Math.floor(idx / 2);
    const bar = Math.floor(idx / TICKS_PER_BAR);
    for (const name of Object.keys(pending)) {
      const p = pending[name];
      const v = VOICES[name];
      let due;
      if (v.slot === "down") due = down;
      else if (v.slot === "off") due = !down;
      else due = down && bar !== commBar;
      if (!due) continue;
      if (v.slot === "bar") commBar = bar;
      delete pending[name];
      // A failed outcome plays one step lower when most of the beat's records failed.
      playVoice(name, v.slot === "bar" ? bar : beat, beatLevel(p.n), p.failed * 2 > p.n);
    }
    schedule();
  }

  function schedule() {
    if (!autoTick || timer != null) return;
    if (!active() || Object.keys(pending).length === 0) return;
    const t = now();
    const wait = TICK_MS - (((t % TICK_MS) + TICK_MS) % TICK_MS) + 1;
    timer = setTimeout(() => {
      timer = null;
      tick(now());
    }, wait);
  }

  function stopTimer() {
    if (timer != null) clearTimeout(timer);
    timer = null;
  }

  function quiet() {
    pending = {};
    stopTimer();
  }

  if (wanted) armGesture();

  return {
    getState,
    subscribe,
    tick,

    /** The Sound toggle. Call from the click handler: turning on creates the AudioContext. */
    setEnabled(on, { gesture = true } = {}) {
      wanted = Boolean(on);
      setSoundPref(wanted, storage);
      if (wanted) {
        if (gesture) {
          ensureContext();
          resume();
          // A soft D4 says "sound is on" (not while held).
          if (canPlay()) tone(pitch(5), { vol: 0.2, attack: 0.04, decay: 1.2 });
        } else {
          armGesture();
        }
      } else {
        quiet();
        if (heartOwner != null) heartOwner = null;
      }
      emit();
      return wanted;
    },

    /** Silence the engine until the returned release() is called (Calm view, reduced motion). */
    hold(reason = "held") {
      const token = {};
      holds.set(token, reason);
      quiet();
      emit();
      return () => {
        if (holds.delete(token)) emit();
      };
    },

    /** A record arrived in `category`. Queued for its voice's next slot on the beat. */
    pulse(category, { failed = false } = {}) {
      if (!active() || !VOICES[category]) return false;
      const p = (pending[category] ??= { n: 0, failed: 0 });
      p.n += 1;
      if (failed) p.failed += 1;
      schedule();
      return true;
    },

    /** A light was touched: one short note at its pitch, now (not quantized). */
    note(key, { failed = false } = {}) {
      if (!canPlay()) return false;
      const hz = pitch(touchDegree(key) - (failed ? 1 : 0), TOUCH.octave);
      tone(hz, { vol: TOUCH.vol, attack: TOUCH.attack, decay: TOUCH.decay });
      onVoice?.({ voice: "touch", hz, level: 1, failed });
      return true;
    },

    /** A member's heartbeat; see the header. Call every frame while the clock plays. */
    heartbeat(key, info, { audible = true, at = now() } = {}) {
      const st = heartbeatState(info);
      let h = hearts.get(key);
      if (!h) {
        h = { lastBeat: null, next: null, lastCall: at };
        hearts.set(key, h);
      }
      h.lastCall = at;
      if (!st.alive) {
        h.next = null;
        if (heartOwner === key) heartOwner = null;
        return { ...st, lastBeat: h.lastBeat, swell: 0 };
      }
      if (h.next == null || at >= h.next) {
        h.lastBeat = at;
        h.next = at + 60_000 / st.bpm;
        const owner = heartOwner != null ? hearts.get(heartOwner) : null;
        if (!owner || at - owner.lastCall > 2 * BEAT_MS) heartOwner = audible ? key : null;
        if (audible && heartOwner === key && canPlay()) {
          const lvl = st.level;
          const attack = HEARTBEAT.attackMs / 1000;
          const decay = (HEARTBEAT.attackMs + HEARTBEAT.decayMs) / 1000;
          [[55, 0, 0.55], [48, HEARTBEAT.dubDelayMs / 1000, 0.55 * HEARTBEAT.dubLevel]].forEach(([f, when, v]) => {
            tone(f, { vol: v * lvl, attack, decay, when });
            tone(f * 2, { vol: v * lvl * 0.25, attack, decay, when }); // so laptop speakers carry it
          });
          onVoice?.({ voice: "heartbeat", hz: 55, level: lvl, failed: false });
        }
      }
      return { ...st, lastBeat: h.lastBeat, swell: heartbeatSwell(at, h.lastBeat) * st.level };
    },

    forget(key) {
      hearts.delete(key);
      if (heartOwner === key) heartOwner = null;
    },

    /** The AudioContext, once created (for tests). */
    get context() {
      return ctx;
    },
  };
}

/** The shared engine every view and VizFrame use. */
export const sound = createSound();

/** React: the engine's {on, held, reason, playing}. */
export function useSound(engine = sound) {
  return useSyncExternalStore(engine.subscribe, engine.getState, engine.getState);
}
