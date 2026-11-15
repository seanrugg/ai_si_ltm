import { describe, expect, it } from "vitest";
import { DEFAULT_SPEED, MINUTE, createEventCursor, createReplayClock, indexAfter } from "./replay.js";

const HOUR = 3600_000;

function fakeNow(start) {
  let n = start;
  const now = () => n;
  now.set = (v) => { n = v; };
  return now;
}

describe("replay clock", () => {
  it("starts Live at now, playing, at 60×", () => {
    const now = fakeNow(10 * HOUR);
    const c = createReplayClock({ start: 0, end: 10 * HOUR, now });
    expect(c.getState()).toMatchObject({ mode: "live", t: 10 * HOUR, playing: true, speed: DEFAULT_SPEED });
  });
  it("Live follows now; ticking a replay advances at speed × wall time and stops at the end", () => {
    const now = fakeNow(10 * HOUR);
    const c = createReplayClock({ start: 0, end: 10 * HOUR, now });
    now.set(10 * HOUR + 5000);
    c.tick(1);
    expect(c.getState().t).toBe(10 * HOUR + 5000);

    const r = createReplayClock({ start: 0, end: HOUR, mode: "replay", now });
    expect(r.getState()).toMatchObject({ t: 0, playing: false });
    r.play();
    r.tick(1000);
    r.tick(2000); // 1 s of wall time at 60× = 1 min
    expect(r.getState().t).toBe(MINUTE);
    r.setSpeed(240);
    r.tick(3000);
    expect(r.getState().t).toBe(5 * MINUTE);
    r.setSpeed(7); // not a speed: ignored
    expect(r.getState().speed).toBe(240);
    r.tick(1_000_000);
    expect(r.getState()).toMatchObject({ t: HOUR, playing: false });
    r.play(); // at the end, play starts over
    expect(r.getState()).toMatchObject({ t: 0, playing: true });
  });
  it("seek and step clamp to the range; seeking or pausing Live leaves Live", () => {
    const now = fakeNow(HOUR);
    const c = createReplayClock({ start: 0, end: HOUR, now });
    c.seek(HOUR / 2);
    expect(c.getState()).toMatchObject({ mode: "replay", playing: false, t: HOUR / 2 });
    c.step(-HOUR);
    expect(c.getState().t).toBe(0);
    c.step(MINUTE);
    expect(c.getState().t).toBe(MINUTE);
    c.setMode("live");
    expect(c.getState()).toMatchObject({ mode: "live", playing: true, t: HOUR });
    c.pause();
    expect(c.getState()).toMatchObject({ mode: "replay", playing: false, t: HOUR });
  });
  it("notifies subscribers once per change", () => {
    const c = createReplayClock({ start: 0, end: HOUR, mode: "replay", now: () => HOUR });
    const seen = [];
    const off = c.subscribe((s) => seen.push(s.t));
    c.seek(MINUTE);
    c.seek(MINUTE); // no change
    off();
    c.seek(2 * MINUTE);
    expect(seen).toEqual([MINUTE]);
  });
});

describe("event cursor", () => {
  const events = [1, 2, 2, 5, 9].map((t, i) => ({ t, i }));
  it("indexAfter", () => {
    expect(indexAfter(events, 0)).toBe(0);
    expect(indexAfter(events, 2)).toBe(3);
    expect(indexAfter(events, 100)).toBe(5);
  });
  it("advance returns the events since the last time; going back resets", () => {
    const cur = createEventCursor(events);
    expect(cur.advance(2).fresh.map((e) => e.i)).toEqual([0, 1, 2]);
    expect(cur.advance(2).fresh).toEqual([]);
    expect(cur.advance(9).fresh.map((e) => e.i)).toEqual([3, 4]);
    expect(cur.advance(3)).toEqual({ fresh: [], reset: true });
    expect(cur.upTo(3).length).toBe(3);
    expect(cur.advance(5).fresh.map((e) => e.i)).toEqual([3]);
  });
});
