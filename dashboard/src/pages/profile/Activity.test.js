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
