import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { NOTHING_READ, WhatItRead } from "./Activity.jsx";

afterEach(() => cleanup());

const reading = {
  datasets: [{ dataset: "d1", name: "Manuals", reads: 4, items: 2, last_read: "2026-11-11T08:00:00+00:00" }],
  cycles: [
    { cycle: "c2", agent: "z3-r0", started: "2026-11-11T07:00:00+00:00", ended: "2026-11-11T07:05:00+00:00",
      items_read: 1, lessons: [{ agent: "z3-r0", seq: 9, summary: "Check the wheel" },
                               { agent: "z3-r0", seq: 10, summary: "Log the stall" }] },
    { cycle: "c1", agent: "vector", started: "2026-11-10T07:00:00+00:00", ended: null, items_read: null, lessons: [] },
  ],
};

function show(props) {
  render(React.createElement(MemoryRouter, null, React.createElement(WhatItRead, props)));
}

describe("What it read", () => {
  it("lists datasets, then cycles with their lessons linked to the records", () => {
    show({ reading });
    const table = screen.getByRole("table", { name: "Datasets read" });
    expect(within(table).getByText("Manuals")).toBeDefined();
    expect(within(table).getByText("4")).toBeDefined();
    const cycles = screen.getByRole("list", { name: "Dream cycles" });
    expect(within(cycles).getByText(/1 items read · 2 lessons/)).toBeDefined();
    expect(within(cycles).getByText("not finished")).toBeDefined();
    const link = screen.getByRole("link", { name: "Check the wheel" });
    expect(link.getAttribute("href")).toBe("/record/z3-r0/9");
  });

  it("names the member on a swarm profile", () => {
    show({ reading, swarm: true });
    expect(screen.getByText("vector")).toBeDefined();
  });

  it("has an empty state", () => {
    show({ reading: { datasets: [], cycles: [] } });
    expect(screen.getByText(NOTHING_READ)).toBeDefined();
  });
});

// ---------------------------------------------------------------- Chronology (#403)
import { fireEvent, waitFor } from "@testing-library/react";
import { Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, vi } from "vitest";
import Activity, { memberChronologyPath, rangeWindow, swarmRows } from "./Activity.jsx";
import { EPISODES } from "./episodes.fixture.js";
import { api } from "../../api/client.js";
import { TABS, tabOf } from "../../lib/profile.js";

vi.mock("../../api/client.js", () => ({ api: vi.fn() }));

const summary = {
  flow: { sensed: 5, perceived: 3, reasoned: 2, decided: 2, performed: 1, learned: 1, self_report: 9 },
  outcomes: { success: 3, failed: 1 }, lessons: [],
  daily: [{ day: "2026-11-14", sense: 4, act: 2, comm: 1, learn: 0 }],
};

let where;
function Where() {
  where = useLocation();
  return null;
}

function showTab(props, url = "/members/rover-1/profile?tab=chronology") {
  return render(React.createElement(MemoryRouter, { initialEntries: [url] },
    React.createElement(Routes, null,
      React.createElement(Route, { path: "*", element: React.createElement(React.Fragment, null,
        React.createElement(Activity, { summary, range: { key: "24h", hours: 24 }, ...props }),
        React.createElement(Where)) }))));
}

beforeEach(() => {
  HTMLCanvasElement.prototype.getContext = () => null;
  window.matchMedia = vi.fn().mockReturnValue({ matches: false });
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation(async (path, opts) => {
    if (path === "/admin/profile/episodes") return { ...EPISODES, subject: { member: opts?.params?.member } };
    if (path === "/admin/profile/reading") return { datasets: [], cycles: [] };
    throw new Error(`unexpected ${path}`);
  });
});

describe("Chronology tab", () => {
  it("is named Chronology, and ?tab=activity still opens it", () => {
    expect(TABS.find((t) => t.key === "chronology").label).toBe("Chronology");
    expect(TABS.find((t) => t.key === "activity")).toBeUndefined();
    expect(tabOf("activity", "member")).toBe("chronology");
    expect(tabOf("chronology", "swarm")).toBe("chronology");
  });

  it("keeps the chain row above the frame", async () => {
    showTab({ subject: "member", name: "rover-1" });
    const steps = screen.getByRole("group", { name: "Steps" });
    expect(within(steps).getAllByRole("button").map((b) => b.textContent.match(/^[A-Za-z]+/)[0]))
      .toEqual(["Sensed", "Perceived", "Reasoned", "Decided", "Performed", "Learned"]);
    expect(screen.getByRole("heading", { name: "Chronology" })).toBeDefined();
    await waitFor(() => expect(vi.mocked(api).mock.calls.some(([p]) => p === "/admin/profile/episodes")).toBe(true));
    const call = vi.mocked(api).mock.calls.find(([p]) => p === "/admin/profile/episodes");
    expect(call[1].params).toMatchObject({ member: "rover-1" });
    expect(Date.parse(call[1].params.until) - Date.parse(call[1].params.since)).toBe(24 * 3600_000);
  });

  it("narrates a touched ring with its state and episode count", async () => {
    showTab({ subject: "member", name: "rover-1" });
    const ring = await screen.findByRole("button", { name: "Power ring" });
    fireEvent.click(ring);
    await waitFor(() => expect(document.querySelector(".viz-narrate").textContent)
      .toBe("Power · charger — on charger · 2 episodes this range"), { timeout: 3000 });
  });

  it("shows the table twin in Calm view, matching the episodes", async () => {
    showTab({ subject: "member", name: "rover-1" });
    await screen.findByRole("button", { name: "Power ring" });
    fireEvent.click(screen.getByRole("button", { name: "Table" }));
    const twin = screen.getByTestId("table-twin");
    const tables = within(twin).getAllByRole("table");
    const stateRowsShown = within(tables[0]).getAllByRole("row").slice(1);
    expect(stateRowsShown.map((r) => r.cells[0].textContent.replace("Now: ", "")))
      .toEqual(["Mind", "Voice", "Attention", "Body", "Motion", "Power", "Behavior"]);
    const episodesShown = within(tables[1]).getAllByRole("row").slice(1);
    const all = Object.values(EPISODES.lanes).flat().filter((e) => e[2] !== "still");
    expect(episodesShown.length).toBe(all.length);
    expect(within(twin).getByText("Activity trend: action outcomes")).toBeDefined();
    expect(within(twin).getByText("failed")).toBeDefined();
  });

  it("shows one small chronograph per swarm member, sorted, each linking to its profile", async () => {
    const swarms = [{ name: "lab", members: [{ name: "zed", role: "member" }, { name: "amy", role: "hub" }] }];
    showTab({ subject: "swarm", name: "lab", swarms }, "/swarms/lab/profile?tab=chronology");
    await waitFor(() => expect(document.querySelectorAll("figure[data-member] canvas").length).toBe(2));
    expect([...document.querySelectorAll("figure[data-member]")].map((f) => f.dataset.member)).toEqual(["amy", "zed"]);
    fireEvent.click(screen.getByRole("button", { name: "zed" }));
    expect(where.pathname).toBe("/members/zed/profile");
    expect(where.search).toBe("?tab=chronology&range=24h");
  });

  it("helpers: member links, swarm twin rows, range windows", () => {
    expect(memberChronologyPath("a b", { key: "7d" })).toBe("/members/a%20b/profile?tab=chronology&range=7d");
    const rows = swarmRows(["amy"], { amy: EPISODES }, Date.parse(EPISODES.since) + 1300_000);
    expect(rows[0]).toMatchObject({ name: "amy", upTo: 7, total: 10, live: true });
    expect(rows[0].now).toBe("Body: held, Power: battery");
    const now = Date.parse("2026-11-15T00:00:00Z");
    const all = rangeWindow({ key: "all", hours: 0 }, { daily: [{ day: "2026-11-01" }] }, now);
    expect(Date.parse(all.since)).toBe(Date.parse("2026-11-01T00:00:00"));
    expect(rangeWindow({ key: "24h", hours: 24 }, null, now).since).toBe("2026-11-14T00:00:00.000Z");
  });
});
