import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import Health from "./Health.jsx";
import { api } from "../api/client.js";

let healthData;
vi.mock("../api/useApi.js", () => ({
  useApi: vi.fn((path) => (path === "/admin/health"
    ? { data: healthData, error: null, loading: false, reload: () => {} }
    : { data: null, error: null, loading: false, reload: () => {} })),
}));
vi.mock("../api/client.js", () => ({ api: vi.fn() }));

const ago = (s) => new Date(Date.now() - s * 1000).toISOString();
const noPing = { requested_at: null };

beforeEach(() => {
  healthData = {
    members: [
      { id: "1", name: "rover", kind: "robot", swarm: "alpha", role: "member", status: "on_time",
        last_heard: ago(60), heartbeat: { interval_s: 600 }, health: null, spec_summary: "Dev Board 8 GB", ping: noPing },
      { id: "2", name: "alpha-hub", kind: "hub", swarm: "alpha", role: "hub", status: "late",
        last_heard: ago(1000), heartbeat: { interval_s: 600 }, spec_summary: "", ping: noPing,
        health: { ts: ago(1000), throttle_events_24h: 1, payload: { temp_c: { gpu: { last: 71 } } } } },
      { id: "3", name: "quiet", kind: "member", swarm: "beta", role: "member", status: "silent",
        last_heard: ago(5000), heartbeat: { interval_s: 600 }, health: null, spec_summary: "", ping: noPing },
      { id: "4", name: "chatty", kind: "chat member", swarm: null, role: null, status: "unknown",
        last_heard: ago(30), heartbeat: { interval_s: null }, health: null, spec_summary: "", ping: noPing },
    ],
  };
  api.mockReset();
});
afterEach(() => cleanup());

const page = (url = "/health") => render(React.createElement(MemoryRouter, { initialEntries: [url] }, React.createElement(Health)));
const captions = () => screen.getAllByRole("table").map((t) => t.querySelector("caption").textContent);
const rowNames = () => screen.getAllByRole("rowheader").map((th) => within(th).getByRole("link").textContent);

describe("Health page (#381)", () => {
  it("groups members by swarm in captioned tables, free agents last", () => {
    page();
    expect(captions()).toEqual(["Swarm alpha (2)", "Swarm beta (1)", "Free agents (1)"]);
    expect(screen.getAllByRole("columnheader").slice(0, 6).map((h) => h.textContent))
      .toEqual(["Member", "Last heard", "Status", "Health", "Specs", "Ping"]);
  });

  it("states status in words and gives last heard an exact-time tooltip", () => {
    page();
    const row = (name) => screen.getByRole("link", { name }).closest("tr");
    expect(within(row("rover")).getByText("On time")).toBeTruthy();
    expect(within(row("alpha-hub")).getByText("Late")).toBeTruthy();
    expect(within(row("quiet")).getByText("Silent")).toBeTruthy();
    expect(within(row("chatty")).getByText("No interval yet")).toBeTruthy();
    expect(row("rover").querySelector("time").getAttribute("title")).toBeTruthy();
  });

  it("shows health only for members that reported it, labelled as a self-report", () => {
    page();
    const hub = screen.getByRole("link", { name: "alpha-hub" }).closest("tr");
    expect(within(hub).getByText(/gpu 71 °C, 1 throttle event in 24 h/)).toBeTruthy();
    const rover = screen.getByRole("link", { name: "rover" }).closest("tr");
    expect(within(rover).getByText("Not reported")).toBeTruthy();
  });

  it("links rows to the member's profile tabs", () => {
    page();
    expect(screen.getByRole("link", { name: "rover" }).getAttribute("href")).toBe("/members/rover/profile?tab=senses");
    expect(screen.getByRole("link", { name: "Dev Board 8 GB" }).getAttribute("href")).toBe("/members/rover/profile?tab=specs");
  });

  it("each Ping button names its member and calls the existing ping endpoint, then shows the result", async () => {
    api.mockResolvedValue({ requested_at: ago(2), answered_at: ago(0), answer: { summary: "heartbeat" } });
    page();
    expect(screen.getAllByRole("button", { name: /^Ping / })).toHaveLength(4);
    expect(api).not.toHaveBeenCalled(); // rows already carry the ping status
    fireEvent.click(screen.getByRole("button", { name: "Ping quiet" }));
    expect(api).toHaveBeenCalledWith("/admin/members/quiet/ping", { method: "POST" });
    const row = screen.getByRole("link", { name: "quiet" }).closest("tr");
    await waitFor(() => expect(within(row).getByRole("status").textContent).toMatch(/^Answered in/));
  });

  it("filters to late or silent, health warnings, and one swarm", () => {
    page();
    fireEvent.click(screen.getByRole("button", { name: "Late or silent" }));
    expect(rowNames().sort()).toEqual(["alpha-hub", "quiet"]);
    fireEvent.click(screen.getByRole("button", { name: "Health warnings" }));
    expect(rowNames()).toEqual(["alpha-hub"]);
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    fireEvent.change(screen.getByLabelText("Swarm"), { target: { value: "beta" } });
    expect(rowNames()).toEqual(["quiet"]);
  });

  it("sorts inside each group by status or last heard", () => {
    page("/health?sort=status");
    expect(rowNames()).toEqual(["alpha-hub", "rover", "quiet", "chatty"]);
    cleanup();
    page("/health?sort=heard");
    expect(rowNames()).toEqual(["rover", "alpha-hub", "quiet", "chatty"]);
  });
});
