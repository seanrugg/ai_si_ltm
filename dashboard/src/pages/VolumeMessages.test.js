import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { SwarmMessages } from "./Volume.jsx";

afterEach(() => cleanup());

describe("swarm message share (#302)", () => {
  it("shows the share, who is summarised, and a notice over half", () => {
    render(React.createElement(SwarmMessages, { swarms: [
      { swarm: "echo-base", records: 100, messages: 60, share: 0.6, summarised: ["z3-r0"], days_over_half: ["2026-11-12"] },
      { swarm: "quiet", records: 10, messages: 1, share: 0.1, summarised: [], days_over_half: [] },
    ] }));
    expect(screen.getByText(/60 of 100 records are messages \(60%\)/)).toBeTruthy();
    expect(screen.getByText(/summarised for the LRS now: z3-r0/)).toBeTruthy();
    expect(screen.getAllByRole("note")).toHaveLength(1);
    expect(screen.getByRole("note").textContent).toMatch(/over half of this swarm's LRS traffic.*\(1 day in this range\)/);
  });
  it("renders nothing without swarms", () => {
    const { container } = render(React.createElement(SwarmMessages, { swarms: [] }));
    expect(container.textContent).toBe("");
  });
});
