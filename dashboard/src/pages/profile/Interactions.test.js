import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import Interactions from "./Interactions.jsx";
import { api } from "../../api/client.js";

vi.mock("../../api/client.js", () => ({ api: vi.fn() }));
afterEach(() => cleanup());

const graph = {
  nodes: [
    { name: "z3-r0", role: "member", kind: "robot", cognition: null, is_hub: false, swarm: "echo-base" },
    { name: "vector", role: "free", kind: "robot", cognition: { value: "offboard" }, is_hub: false, swarm: null },
    { name: "jetson-hub", role: "hub", kind: "relay", cognition: null, is_hub: true, swarm: "echo-base" },
  ],
  edges: [{ a: "vector", b: "z3-r0", category: "comm", count: 12 }],
  relays: [{ hub: "jetson-hub", member: "z3-r0", count: 30 }],
};

describe("Interactions tab", () => {
  it("draws neurons as links and fills the side panel on selection", async () => {
    vi.mocked(api).mockReset();
    vi.mocked(api).mockResolvedValue(graph);
    render(React.createElement(MemoryRouter, null,
      React.createElement(Interactions, { subject: "member", name: "z3-r0", range: { hours: 168 } })));
    const vector = await screen.findByRole("link", { name: "vector, Free agent · Hybrid" });
    fireEvent.click(vector);
    expect(await screen.findByRole("link", { name: "Open conversation" })).toBeDefined();
    expect(screen.getByRole("link", { name: "Open conversation" }).getAttribute("href"))
      .toBe("/conversation?members=z3-r0%2Cvector");
    expect(screen.getByText(/Each thread is one data type/)).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: "View as table" }));
    expect(screen.getByText("vector ↔ z3-r0")).toBeDefined();
    expect(screen.getByText("jetson-hub relays for z3-r0")).toBeDefined();
  });
});
