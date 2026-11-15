import { describe, expect, it } from "vitest";
import {
  LEVEL, MASTER_GAIN, LOWPASS_HZ, TICK_MS, TICKS_PER_BAR, beatLevel, createSound, getSoundPref,
  heartbeatState, pitch,
} from "./sound.js";

// A fake Web Audio graph: records every node, oscillator and gain ramp.
function fakeAudio() {
  const made = [];
  const param = (value = 0) => ({
    value, calls: [],
    setValueAtTime(v, t) { this.calls.push(["set", v, t]); },
    linearRampToValueAtTime(v, t) { this.calls.push(["lin", v, t]); },
    exponentialRampToValueAtTime(v, t) { this.calls.push(["exp", v, t]); },
  });
  const node = (kind, extra = {}) => {
    const n = { kind, outs: [], connect(o) { this.outs.push(o); }, ...extra };
    return n;
  };
  class FakeContext {
    constructor() {
      this.currentTime = 0;
      this.state = "running";
      this.destination = node("destination");
      made.push(this);
    }
    createGain() { return node("gain", { gain: param(1) }); }
    createBiquadFilter() { return node("filter", { type: "", frequency: param(350) }); }
    createDelay() { return node("delay", { delayTime: param(0) }); }
    createOscillator() {
      const o = node("osc", { type: "sine", frequency: param(440), started: null,
        start(t) { this.started = t; }, stop(t) { this.stopped = t; } });
      FakeContext.oscs.push(o);
      return o;
    }
    resume() { this.state = "running"; return Promise.resolve(); }
  }
  FakeContext.oscs = [];
  return { FakeContext, made };
}

function memStorage() {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k) };
}

function engine(opts = {}) {
  const { FakeContext, made } = fakeAudio();
  const voices = [];
  let t = opts.start ?? 1_000_000; // a bar boundary (1 000 000 / 500 = 2000 ticks, 2000 % 8 = 0)
  const storage = opts.storage ?? memStorage();
  const s = createSound({ AudioContext: FakeContext, now: () => t, storage, autoTick: false,
    onVoice: (v) => voices.push(v) });
  return { s, made, voices, storage, FakeContext, at: (ms) => { t = ms; }, now: () => t };
}

/** Run the beat clock tick by tick from `from` for `ticks` eighth notes. */
function run(s, from, ticks) {
  for (let i = 0; i < ticks; i++) s.tick(from + i * TICK_MS);
}

describe("pitch", () => {
  it("is D major pentatonic from D3", () => {
    expect(pitch(0)).toBeCloseTo(146.83, 1);
    expect(pitch(3, 1)).toBeCloseTo(440, 0); // A4
    expect(pitch(5)).toBeCloseTo(293.66, 1); // D4
    expect(pitch(-1)).toBeCloseTo(123.47, 1); // B2: one step below D3
  });
});

describe("on and off", () => {
  it("is off by default and creates no audio until turned on", () => {
    const { s, made } = engine();
    expect(s.getState().on).toBe(false);
    expect(s.pulse("sense")).toBe(false);
    expect(s.note("rover")).toBe(false);
    expect(made).toHaveLength(0);
  });

  it("turning it on creates the context with the soft chain", () => {
    const { s, made, storage } = engine();
    s.setEnabled(true);
    expect(made).toHaveLength(1);
    expect(s.getState()).toMatchObject({ on: true, playing: true });
    expect(s.context).toBe(made[0]);
    expect(getSoundPref(storage)).toBe(true);
    s.setEnabled(false);
    expect(getSoundPref(storage)).toBe(false);
  });

  it("uses a −30 dBFS master and a 2.2 kHz low-pass, sine only", () => {
    const { s, FakeContext } = engine();
    s.setEnabled(true);
    s.note(2);
    expect(MASTER_GAIN).toBe(0.07);
    expect(LOWPASS_HZ).toBe(2200);
    expect(FakeContext.oscs.length).toBeGreaterThan(0);
    for (const o of FakeContext.oscs) expect(o.type).toBe("sine");
    const g = FakeContext.oscs[0].outs[0];
    const master = g.outs[0];
    expect(master.gain.value).toBe(MASTER_GAIN);
    expect(master.outs[0]).toMatchObject({ type: "lowpass" });
    expect(master.outs[0].frequency.value).toBe(LOWPASS_HZ);
    // attack 30–60 ms
    const lin = g.gain.calls.find((c) => c[0] === "lin");
    expect(lin[2]).toBeGreaterThanOrEqual(0.03);
    expect(lin[2]).toBeLessThanOrEqual(0.06);
  });

  it("a remembered choice starts on, but makes no audio before a gesture", () => {
    const storage = memStorage();
    storage.setItem("ltms.sound", "1");
    const { s, made } = engine({ storage });
    expect(s.getState().on).toBe(true);
    expect(made).toHaveLength(0);
    document.dispatchEvent(new Event("pointerdown"));
    expect(made).toHaveLength(1);
  });
});

describe("the beat clock", () => {
  it("merges a burst of 20 records into one note per category, level capped", () => {
    const { s, voices } = engine();
    s.setEnabled(true);
    const t0 = 1_000_000 + 10; // just after a downbeat that already ticked
    s.tick(1_000_000);
    voices.length = 0;
    for (let i = 0; i < 20; i++) {
      s.pulse("sense");
      s.pulse("act");
      s.pulse("comm");
      s.pulse("learn");
    }
    run(s, t0 + TICK_MS, 2 * TICKS_PER_BAR);
    const by = (v) => voices.filter((x) => x.voice === v);
    for (const v of ["sense", "act", "comm", "learn"]) expect(by(v)).toHaveLength(1);
    for (const v of voices) expect(v.level).toBe(LEVEL.cap);
    expect(beatLevel(1)).toBeLessThan(beatLevel(3));
    expect(beatLevel(20)).toBe(LEVEL.cap);
    // Each waited only for its own slot: act on the next off-beat, sense and learn on the next downbeat.
    expect(voices.slice(0, 1).map((v) => v.voice)).toEqual(["act"]);
  });

  it("puts sense on downbeats and act on off-beats", () => {
    const { s, voices, at } = engine();
    s.setEnabled(true);
    const base = 1_000_000;
    s.tick(base);
    const seen = [];
    for (let i = 1; i <= 8; i++) {
      s.pulse("sense");
      s.pulse("act");
      const t = base + i * TICK_MS;
      at(t);
      s.tick(t);
      seen.push([i % 2 === 0 ? "down" : "off", voices.splice(0).map((v) => v.voice)]);
    }
    for (const [slot, vs] of seen) {
      if (slot === "down") expect(vs).toContain("sense");
      else expect(vs).not.toContain("sense");
      if (slot === "off") expect(vs).toContain("act");
      else expect(vs).not.toContain("act");
    }
  });

  it("plays comm at most once per bar", () => {
    const { s, voices } = engine();
    s.setEnabled(true);
    const base = 1_000_000;
    for (let i = 0; i < 3 * TICKS_PER_BAR; i++) {
      s.pulse("comm");
      s.tick(base + i * TICK_MS);
    }
    const comm = voices.filter((v) => v.voice === "comm");
    expect(comm).toHaveLength(3);
    const pitches = comm.map((v) => v.hz);
    for (const hz of pitches) {
      expect(hz).toBeGreaterThanOrEqual(pitch(3, 1) - 0.01); // A4
      expect(hz).toBeLessThanOrEqual(pitch(5, 1) + 0.01); // D5
    }
  });

  it("plays a failed outcome one step lower, never a different sound", () => {
    const ok = engine();
    ok.s.setEnabled(true);
    ok.s.tick(1_000_000 + TICK_MS);
    ok.s.pulse("sense");
    ok.s.tick(1_000_000 + 2 * TICK_MS);
    const bad = engine();
    bad.s.setEnabled(true);
    bad.s.tick(1_000_000 + TICK_MS);
    bad.s.pulse("sense", { failed: true });
    bad.s.tick(1_000_000 + 2 * TICK_MS);
    expect(ok.voices[0].voice).toBe("sense");
    expect(bad.voices[0].voice).toBe("sense");
    expect(bad.voices[0].failed).toBe(true);
    expect(bad.voices[0].hz).toBeCloseTo(pitch(0), 1); // D3, one step below E3
    expect(ok.voices[0].hz).toBeCloseTo(pitch(1), 1);
  });

  it("plays a touch at once, with the same pitch for the same key", () => {
    const { s, voices } = engine();
    s.setEnabled(true);
    s.note("rover-1");
    s.note("rover-1");
    expect(voices.filter((v) => v.voice === "touch")).toHaveLength(2);
    expect(voices[0].hz).toBe(voices[1].hz);
  });
});

describe("heartbeat", () => {
  it("beats 48 at a 30 s interval, faster for 15 s, never above 60", () => {
    expect(heartbeatState({ intervalSec: 30, sinceLastSec: 5 }).bpm).toBe(48);
    const fifteen = heartbeatState({ intervalSec: 15, sinceLastSec: 5 }).bpm;
    expect(fifteen).toBeGreaterThan(48);
    expect(fifteen).toBeLessThan(60);
    expect(heartbeatState({ intervalSec: 0.2, sinceLastSec: 0 }).bpm).toBeLessThanOrEqual(60);
  });

  it("uses the resting rate when the interval is missing, and is quiet with no reports", () => {
    expect(heartbeatState({ sinceLastSec: 10 })).toMatchObject({ bpm: 48, alive: true });
    expect(heartbeatState({ intervalSec: 30, sinceLastSec: null })).toMatchObject({ alive: false, level: 0 });
  });

  it("slows by 8 after 2 missed intervals and is silent after 4", () => {
    expect(heartbeatState({ intervalSec: 30, sinceLastSec: 75 })).toMatchObject({ bpm: 40, slowing: true, level: 1 });
    expect(heartbeatState({ intervalSec: 30, sinceLastSec: 105 }).level).toBeLessThan(1);
    expect(heartbeatState({ intervalSec: 30, sinceLastSec: 120 })).toMatchObject({ alive: false, bpm: 0, level: 0 });
  });

  it("sounds a lub-dub at the rate and drives the visual swell", () => {
    const { s, voices, FakeContext } = engine();
    s.setEnabled(true);
    FakeContext.oscs.length = 0;
    const t0 = 2_000_000;
    const hb = s.heartbeat("m1", { intervalSec: 30, sinceLastSec: 1 }, { at: t0 });
    expect(hb.lastBeat).toBe(t0);
    expect(voices.filter((v) => v.voice === "heartbeat")).toHaveLength(1);
    const freqs = FakeContext.oscs.map((o) => o.frequency.value).sort((a, b) => a - b);
    expect(freqs).toEqual([48, 55, 96, 110]);
    // the swell peaks just after the beat, and is 0 between beats
    expect(s.heartbeat("m1", { intervalSec: 30, sinceLastSec: 1.04 }, { at: t0 + 40 }).swell).toBeCloseTo(1, 5);
    expect(s.heartbeat("m1", { intervalSec: 30, sinceLastSec: 1.6 }, { at: t0 + 600 }).swell).toBe(0);
    // next beat after 60/48 s
    s.heartbeat("m1", { intervalSec: 30, sinceLastSec: 2 }, { at: t0 + 1200 });
    expect(voices.filter((v) => v.voice === "heartbeat")).toHaveLength(1);
    s.heartbeat("m1", { intervalSec: 30, sinceLastSec: 2.3 }, { at: t0 + 1250 });
    expect(voices.filter((v) => v.voice === "heartbeat")).toHaveLength(2);
  });

  it("is silent after 4 missed intervals", () => {
    const { s, voices } = engine();
    s.setEnabled(true);
    const hb = s.heartbeat("m1", { intervalSec: 30, sinceLastSec: 121 }, { at: 3_000_000 });
    expect(hb).toMatchObject({ alive: false, swell: 0 });
    expect(voices).toHaveLength(0);
  });

  it("lets only one member's heartbeat be heard at a time", () => {
    const { s, voices } = engine();
    s.setEnabled(true);
    const t0 = 4_000_000;
    const a = s.heartbeat("a", { intervalSec: 30, sinceLastSec: 1 }, { at: t0 });
    const b = s.heartbeat("b", { intervalSec: 30, sinceLastSec: 1 }, { at: t0 + 300 });
    expect(a.lastBeat).toBe(t0);
    expect(b.lastBeat).toBe(t0 + 300); // b still beats for the light
    expect(voices.filter((v) => v.voice === "heartbeat")).toHaveLength(1);
  });

  it("still beats for the light while sound is off", () => {
    const { s, voices } = engine();
    const hb = s.heartbeat("m1", { intervalSec: 30, sinceLastSec: 1 }, { at: 5_000_000 });
    expect(hb.lastBeat).toBe(5_000_000);
    expect(voices).toHaveLength(0);
  });
});

describe("Calm view", () => {
  it("plays nothing while held, and drops what was queued", () => {
    const { s, voices } = engine();
    s.setEnabled(true);
    voices.length = 0;
    s.pulse("sense");
    const release = s.hold("Sound is off in Calm view");
    expect(s.getState()).toMatchObject({ on: true, held: true, playing: false, reason: "Sound is off in Calm view" });
    expect(s.pulse("act")).toBe(false);
    expect(s.note("x")).toBe(false);
    s.heartbeat("m1", { intervalSec: 30, sinceLastSec: 1 }, { at: 1_000_000 });
    run(s, 1_000_000, TICKS_PER_BAR);
    expect(voices).toHaveLength(0);
    release();
    expect(s.getState().held).toBe(false);
    s.pulse("sense");
    run(s, 1_000_000 + TICKS_PER_BAR * TICK_MS, 2);
    expect(voices.map((v) => v.voice)).toEqual(["sense"]);
  });
});
