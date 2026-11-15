import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import React from "react";
import VizFrame, { HoloTable } from "./VizFrame.jsx";
import { MINUTE, createReplayClock } from "../lib/replay.js";
import { setCalmStart } from "../lib/light.js";
import { createSound, sound as sharedSound } from "../lib/sound.js";

const h = React.createElement;
const HOUR = 3600_000;

function replayClock() {
  return createReplayClock({ start: 0, end: HOUR, t: 30 * MINUTE, mode: "replay", now: () => HOUR });
}

function setup(props = {}) {
  const view = vi.fn(() => h("canvas", { "data-testid": "canvas" }));
  const table = vi.fn(() => h(HoloTable, {
    caption: "Members",
    columns: [{ key: "name", label: "Name" }, { key: "cat", label: "Data", category: (r) => r.cat },
      { key: "n", label: "Records", numeric: true }],
    rows: [{ id: "a", name: "rover-1", cat: "sense", n: 12 }, { id: "b", name: "rover-2", cat: "act", n: 3 }],
    isLive: (r) => r.id === "a",
  }));
  const clock = props.clock ?? replayClock();
  const utils = render(h(VizFrame, { title: "Chronology", clock, renderTable: table, ...props }, view));
  return { ...utils, view, table, clock };
}

const pressed = (name) => screen.getByRole("button", { name }).getAttribute("aria-pressed");

beforeEach(() => {
  window.matchMedia = vi.fn().mockReturnValue({ matches: false });
});
afterEach(() => {
  cleanup();
  sharedSound.setEnabled(false); // the shared engine outlives each render
  vi.useRealTimers();
  delete window.matchMedia;
  try { window.localStorage.clear(); } catch { /* ignore */ }
});

describe("VizFrame header", () => {
  it("shows the title and every control with aria-pressed", () => {
    const { clock } = setup();
    expect(screen.getByRole("heading", { name: "Chronology" })).toBeDefined();
    expect(pressed("Live")).toBe("false");
    expect(pressed("Replay")).toBe("true");
    expect(pressed("Play")).toBe("false");
    expect(pressed("60×")).toBe("true");
    expect(pressed("Sound")).toBe("false"); // off by default
    expect(pressed("View")).toBe("true");
    expect(pressed("Table")).toBe("false");

    fireEvent.click(screen.getByRole("button", { name: "Play" }));
    expect(pressed("Play")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "240×" }));
    expect(pressed("240×")).toBe("true");
    expect(pressed("60×")).toBe("false");
    expect(clock.getState().speed).toBe(240);
    fireEvent.click(screen.getByRole("button", { name: "Sound" }));
    expect(pressed("Sound")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Live" }));
    expect(pressed("Live")).toBe("true");
    expect(clock.getState().mode).toBe("live");
  });
  it("Sound reports its changes", () => {
    const onSoundChange = vi.fn();
    setup({ onSoundChange });
    fireEvent.click(screen.getByRole("button", { name: "Sound" }));
    expect(onSoundChange).toHaveBeenCalledWith(true);
  });
});

describe("Calm view", () => {
  it("View mode animates and hides the table twin", () => {
    const { view, table, container } = setup();
    expect(table).not.toHaveBeenCalled();
    expect(view.mock.calls.at(-1)[0]).toMatchObject({ calm: false, animate: true });
    const stage = container.querySelector(".viz-stage");
    expect(stage.getAttribute("aria-hidden")).toBe("true");
    expect(stage.getAttribute("data-animate")).toBe("true");
  });
  it("Table shows the table twin first, stops animation and sound", () => {
    const onCalmChange = vi.fn();
    const { view, container } = setup({ onCalmChange });
    fireEvent.click(screen.getByRole("button", { name: "Sound" }));
    fireEvent.click(screen.getByRole("button", { name: "Table" }));
    expect(onCalmChange).toHaveBeenCalledWith(true);
    expect(pressed("Table")).toBe("true");
    expect(pressed("Sound")).toBe("false");
    expect(screen.getByRole("button", { name: "Sound" }).disabled).toBe(true);
    expect(view.mock.calls.at(-1)[0]).toMatchObject({ calm: true, animate: false, sound: false });
    const twin = screen.getByTestId("table-twin");
    const stage = container.querySelector(".viz-stage");
    expect(twin.compareDocumentPosition(stage) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(stage.getAttribute("data-animate")).toBe("false");
    // The Holo table: caption, numbers right-aligned, category line sample, live row.
    const table = within(twin).getByRole("table", { name: "Members" });
    expect(within(table).getByText("12").className).toContain("num");
    expect(within(table).getByText("Sensing")).toBeDefined();
    expect(table.querySelector('tr[data-live="true"]').textContent).toContain("rover-1");
    expect(within(table).getByText("Now:")).toBeDefined();
  });
  it("prefers-reduced-motion starts in Calm view", () => {
    window.matchMedia = vi.fn().mockReturnValue({ matches: true });
    const { view } = setup();
    expect(pressed("Table")).toBe("true");
    expect(screen.getByTestId("table-twin")).toBeDefined();
    expect(view.mock.calls.at(-1)[0]).toMatchObject({ calm: true, animate: false, reducedMotion: true });
  });
  it("the per-user setting starts in Calm view", () => {
    setCalmStart(true);
    setup();
    expect(pressed("Table")).toBe("true");
  });
});

describe("narration", () => {
  it("is polite, updates at most once per 2 s, newest wins, and skips self-reports", () => {
    vi.useFakeTimers();
    const clock = replayClock();
    const props = { title: "Chronology", clock, legend: false };
    const { rerender, container } = render(h(VizFrame, { ...props, narration: "first" }, null));
    const line = container.querySelector("[aria-live]");
    expect(line.getAttribute("aria-live")).toBe("polite");
    expect(line.textContent).toBe("first");

    rerender(h(VizFrame, { ...props, narration: "second" }, null));
    rerender(h(VizFrame, { ...props, narration: "third" }, null));
    expect(line.textContent).toBe("first");
    act(() => { vi.advanceTimersByTime(1999); });
    expect(line.textContent).toBe("first");
    act(() => { vi.advanceTimersByTime(1); });
    expect(line.textContent).toBe("third");

    act(() => { vi.advanceTimersByTime(5000); });
    rerender(h(VizFrame, { ...props, narration: { text: "heartbeat", self: true } }, null));
    act(() => { vi.advanceTimersByTime(5000); });
    expect(line.textContent).toBe("third");
  });
});

describe("time bar", () => {
  it("is a slider: arrows move ±1 min, Space plays and pauses, Home/End", () => {
    const { clock } = setup();
    const slider = screen.getByRole("slider", { name: "Time" });
    expect(slider.getAttribute("aria-valuenow")).toBe(String(30 * MINUTE));
    fireEvent.keyDown(slider, { key: "ArrowRight" });
    expect(clock.getState().t).toBe(31 * MINUTE);
    fireEvent.keyDown(slider, { key: "ArrowLeft" });
    fireEvent.keyDown(slider, { key: "ArrowLeft" });
    expect(clock.getState().t).toBe(29 * MINUTE);
    expect(slider.getAttribute("aria-valuenow")).toBe(String(29 * MINUTE));
    fireEvent.keyDown(slider, { key: " " });
    expect(clock.getState().playing).toBe(true);
    fireEvent.keyDown(slider, { key: " " });
    expect(clock.getState().playing).toBe(false);
    fireEvent.keyDown(slider, { key: "End" });
    expect(clock.getState().t).toBe(HOUR);
    fireEvent.keyDown(slider, { key: "Home" });
    expect(clock.getState().t).toBe(0);
  });
  it("dragging seeks", () => {
    const { clock } = setup();
    const slider = screen.getByRole("slider", { name: "Time" });
    slider.getBoundingClientRect = () => ({ left: 0, width: 200, top: 0, height: 32 });
    fireEvent.pointerDown(slider, { clientX: 50, pointerId: 1 });
    expect(clock.getState().t).toBe(HOUR / 4);
    fireEvent.pointerMove(slider, { clientX: 100, pointerId: 1 });
    expect(clock.getState().t).toBe(HOUR / 2);
    fireEvent.pointerUp(slider, { pointerId: 1 });
    fireEvent.pointerMove(slider, { clientX: 150, pointerId: 1 });
    expect(clock.getState().t).toBe(HOUR / 2);
  });
});

describe("touch list", () => {
  it("buttons call the view's handlers and name the light in the narration", () => {
    const onTouch = vi.fn();
    const own = vi.fn();
    const touchables = [
      { id: "hub", label: "Hub star" },
      { id: "m1", label: "rover-1", narration: "rover-1 · online", onTouch: own },
    ];
    const { container } = setup({ touchables, onTouch });
    const list = screen.getByRole("list", { name: "Chronology lights" });
    const buttons = within(list).getAllByRole("button");
    expect(buttons.map((b) => b.textContent)).toEqual(["Hub star", "rover-1"]);
    fireEvent.click(buttons[0]);
    expect(onTouch).toHaveBeenCalledWith(touchables[0]);
    expect(container.querySelector("[aria-live]").textContent).toBe("Hub star");
    fireEvent.click(buttons[1]);
    expect(own).toHaveBeenCalledWith(touchables[1]);
    expect(onTouch).toHaveBeenCalledTimes(1);
  });
  it("Escape goes up a level", () => {
    const onEscape = vi.fn();
    setup({ onEscape });
    fireEvent.keyDown(screen.getByRole("slider", { name: "Time" }), { key: "Escape" });
    expect(onEscape).toHaveBeenCalled();
  });
});

describe("legend", () => {
  it("shows category lines with names, and the self-report dot", () => {
    setup({ legend: { categories: ["sense", "comm"] } });
    const legend = screen.getByRole("group", { name: "Legend" });
    expect(within(legend).getByText("Sensing")).toBeDefined();
    expect(within(legend).getByText("Communicating")).toBeDefined();
    expect(within(legend).queryByText("Learning & rules")).toBeNull();
    expect(within(legend).getByText("Self-report (heartbeat)")).toBeDefined();
  });
});

describe("sound", () => {
  function engine() {
    const played = [];
    const made = [];
    class FakeContext {
      constructor() {
        made.push(this);
        this.currentTime = 0;
        this.state = "running";
        this.destination = {};
      }
      createGain() { return { gain: { value: 1, setValueAtTime() {}, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {} }; }
      createBiquadFilter() { return { frequency: {}, connect() {} }; }
      createDelay() { return { delayTime: {}, connect() {} }; }
      createOscillator() { return { frequency: {}, connect() {}, start() {}, stop() {} }; }
    }
    const store = new Map();
    const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) };
    const eng = createSound({ AudioContext: FakeContext, storage, autoTick: false, onVoice: (v) => played.push(v) });
    return { eng, played, made };
  }

  it("the toggle creates the audio context and drives the engine", () => {
    const { eng, made } = engine();
    setup({ soundEngine: eng });
    expect(made).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Sound" }));
    expect(made).toHaveLength(1);
    expect(eng.getState().on).toBe(true);
    expect(pressed("Sound")).toBe("true");
  });

  it("plays a touched light's note when sound is on", () => {
    const { eng, played } = engine();
    setup({ soundEngine: eng, touchables: [{ id: "rover-1", label: "rover-1" }] });
    fireEvent.click(screen.getByRole("button", { name: "rover-1" }));
    expect(played).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Sound" }));
    played.length = 0;
    fireEvent.click(screen.getByRole("button", { name: "rover-1" }));
    expect(played.map((v) => v.voice)).toEqual(["touch"]);
  });

  it("nothing plays in Calm view, and the toggle says why", () => {
    const { eng, played } = engine();
    setup({ soundEngine: eng, touchables: [{ id: "rover-1", label: "rover-1" }] });
    fireEvent.click(screen.getByRole("button", { name: "Sound" }));
    played.length = 0;
    fireEvent.click(screen.getByRole("button", { name: "Table" }));
    const toggle = screen.getByRole("button", { name: "Sound" });
    expect(toggle.disabled).toBe(true);
    expect(toggle.getAttribute("title")).toBe("Sound is off in Calm view");
    expect(eng.pulse("sense")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "rover-1" }));
    eng.heartbeat("m1", { intervalSec: 30, sinceLastSec: 1 });
    for (let i = 0; i < 8; i++) eng.tick(1_000_000 + i * 500);
    expect(played).toHaveLength(0);
    // Back to View: the remembered choice is still on.
    fireEvent.click(screen.getByRole("button", { name: "View" }));
    expect(pressed("Sound")).toBe("true");
    expect(eng.pulse("sense")).toBe(true);
  });

  it("reduced motion holds sound off even in View", () => {
    window.matchMedia = vi.fn().mockReturnValue({ matches: true });
    const { eng } = engine();
    setup({ soundEngine: eng });
    fireEvent.click(screen.getByRole("button", { name: "View" }));
    const toggle = screen.getByRole("button", { name: "Sound" });
    expect(toggle.disabled).toBe(true);
    expect(toggle.getAttribute("title")).toMatch(/reduced motion/);
    expect(eng.getState().held).toBe(true);
  });

  it("releases its hold when unmounted", () => {
    const { eng } = engine();
    setup({ soundEngine: eng, defaultCalm: true });
    expect(eng.getState().held).toBe(true);
    cleanup();
    expect(eng.getState().held).toBe(false);
  });
});
