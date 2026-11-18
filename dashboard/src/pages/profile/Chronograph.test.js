import { beforeAll, describe, expect, it } from "vitest";
import {
  ECHO_ALPHA, EDGE, LANES, LEAP_WALL_S, NODE_LANES, angleAt, clockTicks, danceAt, drawChronograph,
  episodeArcs, episodeRows, geometry, laneAtPoint, laneTouchText, leapPoint, leapSeconds, leapsOf,
  narrationAt, nodeAxis, nodeRadius, polar, stateRows, stateVector, tickStep,
  PASS_MAX_S, chronographSound, lastSelfAt, pulsesPassed,
} from "./Chronograph.jsx";
import { CAT_SELF, LTMS_CORE, MIND, category } from "../../lib/visual.js";

import { EPISODES } from "./episodes.fixture.js";

const SINCE = Date.parse(EPISODES.since);

beforeAll(() => {
  HTMLCanvasElement.prototype.getContext = () => null; // jsdom has no 2D canvas
});

describe("rings", () => {
  it("are in order inside out, with the lane colors", () => {
    expect(LANES.map((l) => l.name)).toEqual(["Mind", "Voice", "Attention", "Body", "Motion", "Power", "Behavior"]);
    const c = Object.fromEntries(LANES.map((l) => [l.key, l.colors]));
    expect(c.mind).toEqual({ thinking: MIND });
    expect(c.voice).toEqual({ listening: "#7fb6ff", heard: "#4d86ff", replying: "#ffffff" });
    expect(c.attention).toEqual({ known: "#d6ff6b", unknown: "#9b7bff" });
    expect(c.body).toEqual({ held: "#ff7a59", cliff: "#ffbe3b", falling: "#ff3d57" });
    expect(c.motion).toEqual({ nudging: "#1f8f78", driving: category("sense").color });
    expect(c.power).toEqual({ charger: LTMS_CORE, battery: "#3fd47a" });
    expect(LANES.at(-1).dashed).toBe(true);
    expect(NODE_LANES).toEqual(["mind", "voice", "attention", "body", "motion", "power"]);
  });

  it("put 0 at 12 o'clock and run clockwise over one turn", () => {
    expect(angleAt(0, 3600)).toBeCloseTo(-Math.PI / 2);
    expect(angleAt(900, 3600)).toBeCloseTo(0); // 3 o'clock
    expect(angleAt(3600, 3600)).toBeCloseTo(1.5 * Math.PI);
  });

  it("draw the live episode only up to the hand", () => {
    const ep = [1200, 1500, "held"];
    const live = episodeArcs(ep, 1350, 3600);
    expect(live.live).toBe(true);
    expect(live.lit[0]).toBeCloseTo(angleAt(1200, 3600));
    expect(live.lit[1]).toBeCloseTo(angleAt(1350, 3600));
    expect(live.asleep[0]).toBeCloseTo(angleAt(1350, 3600));
    expect(live.asleep[1]).toBeCloseTo(angleAt(1500, 3600));
    const past = episodeArcs(ep, 2000, 3600);
    expect(past).toMatchObject({ live: false, asleep: null });
    expect(past.lit[1]).toBeCloseTo(angleAt(1500, 3600));
    const future = episodeArcs(ep, 100, 3600);
    expect(future).toMatchObject({ live: false, lit: null });
  });

  it("label every 15 minutes for short ranges, coarser for long ones", () => {
    expect(tickStep(3600)).toBe(900);
    expect(tickStep(24 * 3600)).toBe(2 * 3600);
    expect(tickStep(30 * 86400)).toBeGreaterThanOrEqual(2 * 86400);
    const ticks = clockTicks(SINCE, 3600);
    expect(ticks.length).toBe(4);
    ticks.forEach((s) => expect(s).toBeGreaterThanOrEqual(0));
  });

  it("find the lane under a point", () => {
    const g = geometry(500, 500);
    expect(laneAtPoint(g.cx, g.cy, g)).toBeNull(); // the core
    expect(laneAtPoint(g.cx, g.cy - g.radius(0), g)).toBe("mind");
    expect(laneAtPoint(g.cx + g.radius(6), g.cy, g)).toBe("behavior");
  });
});

describe("core polygon", () => {
  it("is the state vector at the hand", () => {
    expect(stateVector(EPISODES, 50)).toEqual([0, 0, 0, 0, 0, 0]); // no volts yet
    expect(stateVector(EPISODES, 301)[1]).toBe(0.5); // listening
    expect(stateVector(EPISODES, 665)).toEqual([0, 1, 0, 0, 0, 0]); // replying; volts 3.6 = min
    expect(stateVector(EPISODES, 630)[0]).toBe(1); // thinking
    expect(stateVector(EPISODES, 405)[2]).toBe(1); // known
    expect(stateVector(EPISODES, 905)[2]).toBe(0.5); // unknown
    expect(stateVector(EPISODES, 1300)[3]).toBe(1); // held
    expect(stateVector(EPISODES, 1610)[4]).toBeCloseTo(20 / 120); // speed ÷ max
    expect(stateVector(EPISODES, 1710)[4]).toBe(1);
    expect(stateVector(EPISODES, 1900)[5]).toBe(1); // volts at max
  });
});

describe("the dance", () => {
  const g = geometry(500, 500);
  const list = leapsOf(EPISODES);

  it("a leap goes from the vertex at its start to the core edge at that moment's angle, and back", () => {
    const leap = list.find((l) => l.lane === "body");
    const from = polar(g.cx, g.cy, nodeAxis(3), nodeRadius(1, g.core)); // held = 1 at its start
    const to = polar(g.cx, g.cy, angleAt(1200, 3600), g.core * EDGE);
    const d = danceAt(list, 1200 + 0.5 * LEAP_WALL_S * 60, 3600, g, { leapS: LEAP_WALL_S * 60 });
    const active = d.active[3];
    expect(active.u).toBeCloseTo(0.5);
    expect(leap.from).toBe(1);
    expect(leapPoint(active.from, active.to, 0)).toEqual(from);
    const mid = leapPoint(active.from, active.to, 0.5);
    expect(mid[0]).toBeCloseTo(to[0]);
    expect(mid[1]).toBeCloseTo(to[1]);
    const back = leapPoint(active.from, active.to, 1);
    expect(back[0]).toBeCloseTo(from[0]);
    expect(back[1]).toBeCloseTo(from[1]);
  });

  it("leaves one echo per leap up to the hand, all inside the core", () => {
    const hand = 3600;
    const { echoes } = danceAt(list, hand, 3600, g);
    const eligible = Object.entries(EPISODES.lanes)
      .flatMap(([k, eps]) => eps.map((e) => [k, e]))
      .filter(([k, e]) => e[0] <= hand && k !== "power" && k !== "behavior" && !(k === "motion" && e[2] !== "driving"));
    expect(echoes.length).toBe(eligible.length);
    expect(echoes.length).toBe(7);
    for (const e of echoes) {
      for (const p of [e.from, e.to]) expect(Math.hypot(p[0] - g.cx, p[1] - g.cy)).toBeLessThan(g.core);
    }
    expect(echoes.find((e) => e.node === 4).alpha).toBe(ECHO_ALPHA.motion);
    expect(echoes.find((e) => e.node === 0).alpha).toBe(0.22);
  });

  it("is deterministic: scrubbing back removes echoes, a paused view equals a played one", () => {
    expect(danceAt(list, 500, 3600, g).echoes.length).toBe(2); // listening, known
    expect(danceAt(list, 3600, 3600, g).echoes.length).toBe(7);
    expect(danceAt(list, 500, 3600, g).echoes.length).toBe(2);
    expect(danceAt(list, 1000, 3600, g)).toEqual(danceAt(list, 1000, 3600, g));
  });

  it("has no leaps with reduced motion or Calm, but keeps the echo web", () => {
    const still = danceAt(list, 1201, 3600, g, { animate: false, leapS: 84 });
    expect(still.active).toEqual({});
    expect(still.echoes.length).toBe(6);
    expect(Object.keys(danceAt(list, 1201, 3600, g, { animate: true, leapS: 84 }).active)).toEqual(["3"]);
  });

  it("lasts 1.4 s of wall time: × the speed in Replay", () => {
    expect(leapSeconds("live", 60)).toBeCloseTo(1.4);
    expect(leapSeconds("replay", 60)).toBeCloseTo(84);
  });
});

describe("words", () => {
  it("narrates the latest notable record in plain words", () => {
    expect(narrationAt(EPISODES, 301, "rover-1", SINCE).text).toMatch(/· rover-1 heard its name\.$/);
    expect(narrationAt(EPISODES, 1250, "rover-1", SINCE).text).toMatch(/· Someone picked rover-1 up\.$/);
    expect(narrationAt(EPISODES, 665, "rover-1", SINCE).text).toMatch(/rover-1 answered: “Hello there”$/);
    expect(narrationAt({ ...EPISODES, lanes: {} }, 1100, "rover-1", SINCE).text).toMatch(/turned toward the door$/);
    expect(narrationAt({ ...EPISODES, lanes: {}, pulses: [] }, 1100, "rover-1", SINCE)).toBeNull();
  });

  it("says a touched ring's lane, state at the hand and episode count", () => {
    expect(laneTouchText("body", EPISODES, 1300)).toBe("Body · held · 1 episode this range");
    expect(laneTouchText("voice", EPISODES, 665)).toBe("Voice · replying — Hello there · 2 episodes this range");
    expect(laneTouchText("mind", EPISODES, 50)).toBe("Mind · idle right now · 1 episode this range");
    expect(laneTouchText("behavior", EPISODES, 50)).toBe("Behavior · not logged yet · 0 episodes this range");
  });
});

describe("table twin", () => {
  it("state rows follow the hand", () => {
    const rows = stateRows(EPISODES, 1300);
    expect(rows.map((r) => r.lane)).toEqual(LANES.map((l) => l.name));
    expect(rows.find((r) => r.id === "body")).toMatchObject({ state: "held", count: 1, live: true });
    expect(rows.find((r) => r.id === "mind")).toMatchObject({ state: "idle right now", live: false });
  });

  it("episode rows match the episodes up to the hand, newest first", () => {
    const rows = episodeRows(EPISODES, 670, SINCE);
    expect(rows.map((r) => [r.lane, r.state])).toEqual([
      ["Voice", "replying"], ["Mind", "thinking"], ["Attention", "known"], ["Voice", "listening"], ["Power", "battery"],
    ]);
    expect(rows[0]).toMatchObject({ live: true, length: "about 10 s" });
    expect(rows[0].detail).toBe("Hello there · estimated");
    expect(episodeRows(EPISODES, 3600, SINCE, 3)).toHaveLength(3);
  });
});

describe("drawing", () => {
  function recorder() {
    const calls = [];
    const ctx = new Proxy({}, {
      get(target, prop) {
        if (prop in target) return target[prop];
        return (...args) => { calls.push([prop, ...args]); };
      },
      set(target, prop, value) {
        target[prop] = value;
        return true;
      },
    });
    return { ctx, calls };
  }

  it("draws without a glow canvas (jsdom) and strokes the hand", () => {
    const { ctx, calls } = recorder();
    drawChronograph(ctx, 400, 400, { data: EPISODES, hand: 1300, leapList: leapsOf(EPISODES), animate: false,
                                     leapS: 84, small: false, sinceMs: SINCE, nowMs: 0 });
    expect(calls.some(([p]) => p === "clip")).toBe(true);
    expect(calls.filter(([p]) => p === "fillText").length).toBeGreaterThan(LANES.length);
    expect(CAT_SELF).toBeDefined();
  });
});

/** A fake sound engine: records the calls, plays nothing (#428). */
function fakeEngine(swell = 0.6) {
  const calls = [];
  return {
    calls,
    pulse: (category, opts) => { calls.push(["pulse", category, opts]); return true; },
    heartbeat: (key, info, opts) => { calls.push(["heartbeat", key, info, opts]); return { swell }; },
    note: (key) => { calls.push(["note", key]); return true; },
    forget: (key) => { calls.push(["forget", key]); },
  };
}

describe("Chronology sound (#428)", () => {
  it("passes the records between two hands, without self-reports", () => {
    expect(pulsesPassed(EPISODES.pulses, 200, 1000, Infinity).map((p) => p[0])).toEqual([300, 1000]);
    expect(pulsesPassed(EPISODES.pulses, 290, 300).map((p) => p[0])).toEqual([300]);
    expect(pulsesPassed(EPISODES.pulses, 95, 105)).toEqual([]); // only a self-report
  });

  it("passes nothing on the first frame, going back, standing still or a seek", () => {
    expect(pulsesPassed(EPISODES.pulses, null, 1000)).toEqual([]);
    expect(pulsesPassed(EPISODES.pulses, 1000, 200)).toEqual([]);
    expect(pulsesPassed(EPISODES.pulses, 300, 300)).toEqual([]);
    expect(pulsesPassed(EPISODES.pulses, 0, PASS_MAX_S + 1000)).toEqual([]);
  });

  it("finds the latest self-report at the hand", () => {
    expect(lastSelfAt(EPISODES.pulses, 50)).toBe(null);
    expect(lastSelfAt(EPISODES.pulses, 1000)).toBe(100);
    expect(lastSelfAt(EPISODES.pulses, 1800)).toBe(1790);
  });

  it("pulses each passed record's category and beats the member's heart, audibly", () => {
    const engine = fakeEngine();
    const swell = chronographSound(engine, { data: EPISODES, member: "rover-1", from: 290, to: 305 });
    expect(engine.calls.filter((c) => c[0] === "pulse").map((c) => c[1])).toEqual(["act"]);
    const hb = engine.calls.find((c) => c[0] === "heartbeat");
    expect(hb[1]).toBe("rover-1");
    expect(hb[2]).toEqual({ intervalSec: EPISODES.heartbeat?.interval_s ?? undefined, sinceLastSec: 205 });
    expect(hb[3]).toEqual({ audible: true });
    expect(swell).toBe(0.6);
  });

  it("the engine's swell drives the core; still frames don't swell", () => {
    const radii = (opts) => {
      const out = [];
      const ctx = new Proxy({}, {
        get: (target, k) => (k in target ? target[k] : (...args) => {
          out.push([k, ...args.filter((a) => typeof a === "number")]);
          return k === "createRadialGradient" || k === "createLinearGradient" ? { addColorStop() {} } : undefined;
        }),
        set: (target, k, v) => { target[k] = v; return true; },
      });
      drawChronograph(ctx, 480, 480, { data: EPISODES, hand: 1000, leapList: [], sinceMs: SINCE, nowMs: 0, ...opts });
      return out;
    };
    expect(radii({ animate: true, swell: 1 })).not.toEqual(radii({ animate: true, swell: 0 }));
    expect(radii({ animate: false, swell: 1 })).toEqual(radii({ animate: false, swell: 0 }));
  });
});
