import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import React from "react";
import Globe from "./Globe.jsx";
import { buildGeography } from "../../lib/geography.js";
import { createGlobeCamera } from "../../lib/globe.js";
import { buildScene } from "../../lib/topology.js";

afterEach(() => cleanup());

// A 2D context that records calls and accepts anything, so the draw loop runs in jsdom.
function fakeContext(calls) {
  const grad = { addColorStop: () => {} };
  return new Proxy({}, {
    get(target, prop) {
      if (prop in target) return target[prop];
      if (prop === "createRadialGradient" || prop === "createLinearGradient") return () => grad;
      if (prop === "canvas") return { width: 10, height: 10 };
      return (...args) => { calls.push([prop, args]); };
    },
    set(target, prop, value) { target[prop] = value; return true; },
  });
}

describe("Globe draw loop (smoke)", () => {
  it("draws frames at globe and system level without throwing", async () => {
    const calls = [];
    const errors = [];
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => fakeContext(calls));
    const frames = [];
    vi.stubGlobal("requestAnimationFrame", (fn) => { frames.push(fn); return frames.length; });
    vi.stubGlobal("cancelAnimationFrame", () => {});
    const locations = { members: [
      { name: "hub-1", best: { source: "network", label: "Town", lat: 10, lon: 20, accuracy_km: 25 } },
      { name: "rover-1", best: { source: "declared", label: "Lab", lat: 11, lon: 21 } },
      { name: "helper", best: null }, { name: "lost", best: null, unplaced: "x" }] };
    const swarms = [{ name: "lab", members: [{ name: "hub-1", role: "hub" }, { name: "rover-1", role: "member" }] }];
    const members = [{ name: "hub-1", kind: "relay" }, { name: "rover-1", kind: "robot" }, { name: "helper", kind: "agent" }];
    const geo = buildGeography({ locations, swarms, members, counts: { "hub-1": 3 } });
    const t0 = Date.parse("2026-11-14T00:00:00Z");
    const events = [{ t: t0 + 1000, member: "rover-1", category: "sense", source: "camera", transit: 1, summary: "saw 10.1.2.3" },
      { t: t0 + 2000, member: "helper", category: "comm", source: "hub", transit: 1, summary: "hi" }];
    const scene = buildScene({ roster: [{ name: "hub-1", role: "hub" }, { name: "rover-1", role: "member" }],
      episodes: { "rover-1": { since: "2026-11-14T00:00:00Z", pulses: [[1, 1, "perception", "sense", "camera", "saw"]] } } });
    let t = t0;
    const said = [];
    const frame = { animate: true, t0, clock: { getState: () => ({ t, mode: "replay", speed: 60 }) }, narrate: (x) => said.push(x) };
    for (const [zoom, server] of [[1.6, { lon: 0, lat: 30, label: "S" }], [14, { lon: 0, lat: 30 }], [6, null]]) {
      const camera = createGlobeCamera({ lon: 20, lat: 10, zoom }, 0);
      render(React.createElement(Globe, { geo, server, events, sceneFor: () => scene, frame, camera }));
      for (let i = 0; i < 6; i++) {
        t += 600;
        const fn = frames.shift();
        try { fn?.(performance.now()); } catch (e) { errors.push(e); }
      }
      cleanup();
      frames.length = 0;
    }
    expect(errors).toEqual([]);
    expect(calls.some(([p]) => p === "fill")).toBe(true);
    expect(said).toContain("rover-1: saw an address"); // narration never carries an address
    vi.unstubAllGlobals();
  });
});
