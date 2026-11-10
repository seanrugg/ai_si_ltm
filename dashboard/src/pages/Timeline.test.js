import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import Timeline from "./Timeline.jsx";
import { api } from "../api/client.js";

vi.mock("../api/client.js", () => ({ api: vi.fn() }));
afterEach(() => cleanup());

const rec = (seq, summary) => ({
  v: 1, agent: "z3-r0", seq, ts: `2026-11-10T12:0${seq}:00+00:00`, type: "event", summary,
  prev_hash: "b".repeat(64), hash: String(seq).repeat(64), payload_hash: "c".repeat(64), payload: {},
});
const tombstone = {
  deleted: true, agent: "z3-r0", seq: 4, hash: "4".repeat(64), prev_hash: "3".repeat(64),
  deleted_at: "2026-11-10T13:00:00+00:00", deleted_by: "admin:sean", reason: "test data",
};

function mockApi() {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation(async (path) => {
    if (path === "/admin/timeline") return { records: [rec(6, "six"), rec(5, "five"), rec(3, "three"), rec(1, "one")] };
    if (path === "/admin/records/z3-r0/4") return tombstone;
    if (path === "/admin/records/z3-r0/2") return rec(2, "hidden telemetry");
    throw new Error(`unexpected ${path}`);
  });
}

function renderAt(url) {
  return render(React.createElement(MemoryRouter, { initialEntries: [url] }, React.createElement(Timeline)));
}

describe("tombstones on a member's timeline", () => {
  it("shows a deleted record where its seq falls, and skips seqs that are only hidden", async () => {
    mockApi();
    renderAt("/?agent=z3-r0");
    await screen.findByText("six");
    await waitFor(() => expect(screen.getByText(/Deleted record z3-r0:4\./)).toBeDefined());
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toHaveLength(5);
    expect(items[1]).toContain("five");
    expect(items[2]).toMatch(/Deleted record z3-r0:4\. Deleted by sean on .+: test data/);
    expect(items[3]).toContain("three");
    expect(screen.queryByText("hidden telemetry")).toBeNull();
  });

  it("doesn't look for tombstones when other filters explain the gaps", async () => {
    mockApi();
    renderAt("/?agent=z3-r0&type=event");
    await screen.findByText("six");
    expect(vi.mocked(api).mock.calls.every(([path]) => path === "/admin/timeline")).toBe(true);
    expect(screen.queryByText(/Deleted record/)).toBeNull();
  });

  it("doesn't look for tombstones across all members", async () => {
    mockApi();
    renderAt("/");
    await screen.findByText("six");
    expect(vi.mocked(api).mock.calls.every(([path]) => path === "/admin/timeline")).toBe(true);
  });
});
