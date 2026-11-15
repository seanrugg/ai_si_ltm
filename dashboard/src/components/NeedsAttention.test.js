import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import NeedsAttention from "./NeedsAttention.jsx";
import { api } from "../api/client.js";
import { useApi } from "../api/useApi.js";

let attention;
const reload = vi.fn();
vi.mock("../api/useApi.js", () => ({
  useApi: vi.fn(() => ({ data: attention, error: null, loading: false, reload })),
}));
vi.mock("../api/client.js", () => ({ api: vi.fn() }));

const ago = (s) => new Date(Date.now() - s * 1000).toISOString();

beforeEach(() => {
  reload.mockReset();
  api.mockReset();
  api.mockResolvedValue({});
  attention = {
    checked_at: ago(5),
    counts: { members_pending: 2, charter_overdue: 0, directive_declines: 1, lrs_failing: 0 },
    snoozed: [],
    items: [
      { key: "enrollments", kind: "enrollments", severity: "action", title: "2 enrollments waiting for approval",
        link: "/members", count: 2, since: ago(7200) },
      { key: "silent:rover", kind: "silent", severity: "action", title: "rover is silent: nothing heard for over 2× its heartbeat interval",
        link: "/members/rover/profile?tab=senses", subject: "rover", since: ago(90000), ping: true },
      { key: "directive_declines", kind: "directive_declines", severity: "action",
        title: "1 decline of a directive since you last looked", link: "/directives", count: 1,
        reasons: ["vector: outside my safety envelope"] },
      { key: "dataset_versions", kind: "dataset_versions", severity: "info", title: "1 dataset version waiting for approval",
        link: "/datasets", count: 1, since: ago(600) },
    ],
  };
});
afterEach(() => cleanup());

const panel = () => render(React.createElement(MemoryRouter, null, React.createElement(NeedsAttention)));
const entries = () => within(screen.getByRole("list", { name: "Items that need attention" }))
  .getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
const entry = (title) => screen.getByRole("link", { name: title }).closest("li");

describe("Needs attention panel (#380)", () => {
  it("lists the items in the server's order, each a heading that links to where it's resolved", () => {
    panel();
    expect(screen.getByRole("heading", { level: 2, name: "Needs attention" })).toBeTruthy();
    expect(entries()).toEqual(attention.items.map((i) => i.title));
    expect(screen.getByRole("link", { name: "2 enrollments waiting for approval" }).getAttribute("href")).toBe("/members");
  });

  it("says the severity in words, not colour alone", () => {
    panel();
    expect(within(entry("2 enrollments waiting for approval")).getByText("Action needed")).toBeTruthy();
    expect(within(entry("1 dataset version waiting for approval")).getByText("For your information")).toBeTruthy();
  });

  it("offers Ping for a silent member, named for it", () => {
    panel();
    const silent = entry(attention.items[1].title);
    expect(within(silent).getByRole("button", { name: "Ping rover" })).toBeTruthy();
    expect(within(entry("2 enrollments waiting for approval")).queryByRole("button", { name: /^Ping/ })).toBeNull();
  });

  it("shows decline reasons", () => {
    panel();
    expect(within(entry(attention.items[2].title)).getByText("vector: outside my safety envelope")).toBeTruthy();
  });

  it("snoozes an item by its key, with a button named for the item, then reloads", async () => {
    panel();
    fireEvent.click(screen.getByRole("button", { name: "Snooze for 24 hours: 2 enrollments waiting for approval" }));
    expect(api).toHaveBeenCalledWith("/admin/attention/snooze", { method: "POST", body: { key: "enrollments" } });
    await waitFor(() => expect(reload).toHaveBeenCalled());
  });

  it("lists snoozed items and can show one again", async () => {
    attention.snoozed = [{ key: "lrs", title: "1 LRS endpoint failing or paused", until: ago(-3600) }];
    panel();
    fireEvent.click(screen.getByRole("button", { name: "Show again now: 1 LRS endpoint failing or paused" }));
    expect(api).toHaveBeenCalledWith("/admin/attention/snooze/lrs", { method: "DELETE" });
    await waitFor(() => expect(reload).toHaveBeenCalled());
  });

  it("shows the empty state with when it last checked", () => {
    attention.items = [];
    panel();
    expect(screen.getByText(/Nothing needs you right now/)).toBeTruthy();
    expect(screen.getByText(/Checked/).querySelector("time").getAttribute("dateTime")).toBe(attention.checked_at);
  });

  it("asks with the same directives_seen as the nav badges", () => {
    window.localStorage.setItem("ltms.nav.directivesSeen", "2026-01-01T00:00:00Z");
    panel();
    expect(useApi).toHaveBeenCalledWith("/admin/attention", { directives_seen: "2026-01-01T00:00:00Z" });
    window.localStorage.removeItem("ltms.nav.directivesSeen");
  });
});
