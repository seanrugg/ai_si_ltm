import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
// Profile first: Overview imports from Profile.jsx, so loading Overview first would leave Profile's tab table empty.
import Profile from "../Profile.jsx";
import Overview, {
  BEAT_GROW, BeatDot, agoText, beatScale, bpmOf, heartbeatFact, membersBeating, pulseOf, pulseText, selfReportNote,
} from "./Overview.jsx";
import { heartbeatSwell } from "../../lib/light.js";
import { api } from "../../api/client.js";
import { COUNT_SELF_REPORT, COUNT_SENSE } from "../../lib/help.js";

vi.mock("../../api/client.js", () => ({ api: vi.fn() }));
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

const NOW = Date.parse("2026-11-14T12:00:00Z");
const ago = (s) => new Date(NOW - s * 1000).toISOString();
const zero = { sense: 0, act: 0, comm: 0, learn: 0 };

function summaryOf(extra = {}) {
  return { subject: { type: "member", name: "m1" }, hours: 168, categories: { ...zero, sense: 4 }, daily: [], places: [],
    outcomes: {}, flow: { self_report: 1716 }, lessons: [], peers: [], members: [],
    self_report_last: ago(12), self_report_interval_s: 30, ...extra };
}

function renderOverview(props) {
  return render(React.createElement(MemoryRouter, { initialEntries: ["/members/m1/profile"] },
    React.createElement(Overview, { subject: "member", name: "m1", history: { events: [] }, members: [], swarms: [],
      onSelectPeer: () => {}, ...props })));
}

function tile() {
  return screen.getByText("Self-reports").closest("li");
}

describe("self-reports are counted apart from sensing (#348, #406)", () => {
  it("the Sensing tile no longer carries a self-report footnote", () => {
    expect(selfReportNote({ flow: { self_report: 0 } })).toBeNull();
    expect(selfReportNote({ flow: { self_report: 1200 } })).toBeNull();
    vi.useFakeTimers({ now: NOW });
    renderOverview({ summary: summaryOf() });
    const sensing = screen.getByText("Sensing").closest("li");
    expect(within(sensing).getByText("sensed and perceived records")).toBeTruthy();
    expect(within(sensing).queryByText(/self-report/)).toBeNull();
  });
  it("help says what is and isn't counted", () => {
    expect(COUNT_SENSE).toMatch(/Heartbeats and health reports are not counted/);
    expect(COUNT_SELF_REPORT).toMatch(/heartbeat/);
  });
});

describe("heartbeat rate and state (#406)", () => {
  it("maps the median interval to 48–60 bpm", () => {
    expect(bpmOf(30)).toBe(48);
    expect(bpmOf(300)).toBe(48);
    expect(bpmOf(10)).toBe(60);
    expect(bpmOf(2)).toBe(60);
    expect(bpmOf(20)).toBe(54);
    expect(bpmOf(null)).toBeNull();
  });
  it("is beating, slowing, then quiet as the last report ages", () => {
    expect(pulseText(pulseOf(ago(12), 30, NOW))).toBe("beating · 48 bpm · last 12 s ago");
    expect(pulseText(pulseOf(ago(120), 30, NOW))).toBe("slowing · last 2 min ago");
    expect(pulseText(pulseOf(ago(540), 30, NOW))).toBe("quiet · last 9 min ago");
    expect(pulseText(pulseOf(ago(60), null, NOW))).toBe("last 1 min ago");
    expect(pulseText(null)).toBe("no self-reports yet");
    expect(agoText(7200)).toBe("2 h ago");
  });
  it("gives the header fact", () => {
    expect(heartbeatFact(pulseOf(ago(12), 30, NOW))).toBe("● 48 bpm");
    expect(heartbeatFact(pulseOf(ago(540), 30, NOW))).toBe("quiet");
    expect(heartbeatFact(pulseOf(ago(12), null, NOW))).toBeNull();
    expect(heartbeatFact(null)).toBeNull();
  });
});

describe("the Self-reports tile (#406)", () => {
  it("shows the count and the live heartbeat", () => {
    vi.useFakeTimers({ now: NOW });
    renderOverview({ summary: summaryOf() });
    const t = tile();
    expect(within(t).getByText("1,716")).toBeTruthy();
    expect(within(t).getByText("heartbeat and health reports")).toBeTruthy();
    expect(within(t).getByText("beating · 48 bpm · last 12 s ago")).toBeTruthy();
    expect(t.querySelector("[data-beat]").dataset.beat).toBe("beating");
    expect(screen.getByRole("list", { name: "Records by category" }).children.length).toBeGreaterThanOrEqual(5);
  });
  it("is slowing, then quiet with a dimmed, still dot; never an alarm", () => {
    vi.useFakeTimers({ now: NOW });
    renderOverview({ summary: summaryOf({ self_report_last: ago(120) }) });
    expect(within(tile()).getByText("slowing · last 2 min ago")).toBeTruthy();
    cleanup();
    renderOverview({ summary: summaryOf({ self_report_last: ago(540) }) });
    expect(within(tile()).getByText("quiet · last 9 min ago")).toBeTruthy();
    const dot = tile().querySelector("[data-beat]");
    expect(dot.dataset.beat).toBe("still");
    expect(dot.style.opacity).toBe("0.35");
  });
  it("beats with the shared lub-dub envelope from light.js, not a stand-in CSS animation (#428)", () => {
    const bpm = 48;
    const period = 60_000 / bpm;
    for (const dt of [0, 80, 160, 300, 600, 1000]) {
      const now = 10 * period + dt;
      expect(beatScale(now, bpm)).toBeCloseTo(1 + BEAT_GROW * heartbeatSwell(now, 10 * period), 9);
    }
    expect(Math.max(...[0, 50, 100, 150, 200, 250].map((dt) => beatScale(10 * period + dt, bpm)))).toBeGreaterThan(1);
    expect(beatScale(1000, null)).toBe(1);
    render(React.createElement(BeatDot, { pulse: { state: "beating", bpm } }));
    const dot = document.querySelector("[data-beat]");
    expect(dot.dataset.beat).toBe("beating");
    expect(dot.className).not.toMatch(/ltms-beat/);
    expect(document.querySelector("style")).toBeNull();
  });

  it("is still with prefers-reduced-motion", () => {
    vi.stubGlobal("matchMedia", (q) => ({ matches: q.includes("reduce"), addEventListener() {}, removeEventListener() {} }));
    render(React.createElement(BeatDot, { pulse: pulseOf(new Date(Date.now() - 1000).toISOString(), 30) }));
    const dot = document.querySelector("[data-beat]");
    expect(dot.dataset.beat).toBe("still");
    expect(dot.className).not.toMatch(/ltms-beat/);
  });
  it("links a hub to Machine health", () => {
    vi.useFakeTimers({ now: NOW });
    renderOverview({ summary: summaryOf(), members: [{ name: "m1", kind: "relay" }] });
    expect(within(tile()).getByRole("link", { name: "Machine health" }).getAttribute("href")).toMatch(/tab=senses/);
    cleanup();
    renderOverview({ summary: summaryOf(), members: [{ name: "m1", kind: "robot" }] });
    expect(within(tile()).queryByRole("link", { name: "Machine health" })).toBeNull();
  });
  it("sums a swarm and says how many members are beating", () => {
    const rows = [{ name: "a", last: ago(5), interval_s: 30 }, { name: "b", last: ago(900), interval_s: 30 },
      { name: "c", last: null, interval_s: null }];
    expect(membersBeating(rows, NOW).text).toBe("1 of 3 members beating");
    vi.useFakeTimers({ now: NOW });
    renderOverview({ subject: "swarm", summary: summaryOf({ subject: { type: "swarm", name: "s" },
      flow: { self_report: 40 }, self_report_last: ago(5), self_report_members: rows }) });
    expect(within(tile()).getByText("40")).toBeTruthy();
    expect(within(tile()).getByText("1 of 3 members beating")).toBeTruthy();
  });
});

describe("the profile header Heartbeat fact (#406)", () => {
  function mockApi(summary) {
    vi.mocked(api).mockReset();
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === "/admin/profile/summary") return summary;
      if (path === "/admin/profile/history") return { events: [] };
      if (path === "/admin/members") return { members: [{ name: "m1", kind: "robot", status: "active", last_seen_at: null }] };
      if (path === "/admin/swarms") return [];
      if (path === "/admin/locations") return { members: [] };
      if (path.endsWith("/ping")) return null;
      throw new Error(`unexpected ${path}`);
    });
  }
  function renderProfile() {
    return render(React.createElement(MemoryRouter, { initialEntries: ["/members/m1/profile"] },
      React.createElement(Routes, null,
        React.createElement(Route, { path: "/members/:name/profile",
          element: React.createElement(Profile, { subject: "member" }) }))));
  }
  it("shows the rate beside Status", async () => {
    mockApi(summaryOf({ self_report_last: new Date(Date.now() - 5000).toISOString() }));
    renderProfile();
    const identity = await screen.findByRole("region", { name: "Identity" });
    expect(await within(identity).findByText("Heartbeat")).toBeTruthy();
    expect(within(identity).getByText("● 48 bpm")).toBeTruthy();
  });
  it("says quiet", async () => {
    mockApi(summaryOf({ self_report_last: new Date(Date.now() - 600000).toISOString() }));
    renderProfile();
    const identity = await screen.findByRole("region", { name: "Identity" });
    expect(await within(identity).findByText("quiet")).toBeTruthy();
  });
  it("is absent with no interval", async () => {
    mockApi(summaryOf({ self_report_interval_s: null }));
    renderProfile();
    const again = await screen.findByRole("region", { name: "Identity" });
    await screen.findByText("heartbeat and health reports");
    expect(within(again).queryByText("Heartbeat")).toBeNull();
  });
});
