import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import DecisionTrace from "./DecisionTrace.jsx";
import RecordPage from "./RecordPage.jsx";

const root = {
  v: 1, agent: "v3-c7r", seq: 5, ts: "2026-11-13T21:00:00+00:00", type: "decision", summary: "Pick it up",
  prev_hash: "b".repeat(64), hash: "a".repeat(64), payload_hash: "c".repeat(64), payload: {},
  refs: { based_on: "v3-c7r:4" },
};
vi.mock("../api/useApi.js", () => ({
  useApi: vi.fn((path) => {
    if (path === "/admin/decision-trace") return { data: { inputs: [], root, children: [], grandchildren: [] }, loading: false };
    if (path === "/admin/members") return { data: { members: [] }, loading: false };
    return { data: root, loading: false, reload: () => {} };
  }),
}));
vi.mock("../api/client.js", () => ({ api: vi.fn(async () => root) }));
afterEach(() => cleanup());

const FILTERED = "/timeline?agent=v3-c7r&type=decision&q=pick";

function renderAt(url) {
  return render(React.createElement(MemoryRouter, { initialEntries: [url] },
    React.createElement(Routes, null,
      React.createElement(Route, { path: "/record/:agent/:seq", element: React.createElement(RecordPage) }),
      React.createElement(Route, { path: "/decision-trace/:agent/:seq", element: React.createElement(DecisionTrace) }))));
}
const crumbs = () => within(screen.getByRole("navigation", { name: "Breadcrumb" }));
const href = (el) => el.getAttribute("href");

describe("breadcrumbs on record and trace pages (#393)", () => {
  it("a trace opened from a filtered timeline leads back to the same filters", () => {
    renderAt(`/decision-trace/v3-c7r/5?from=${encodeURIComponent(FILTERED)}`);
    expect(href(crumbs().getByRole("link", { name: "Timeline (v3-c7r)" }))).toBe(`${FILTERED}`);
    expect(href(crumbs().getByRole("link", { name: "Record #5" })))
      .toBe(`/record/v3-c7r/5?from=${encodeURIComponent(FILTERED)}`);
    const current = crumbs().getByText("Trace");
    expect(current.getAttribute("aria-current")).toBe("page");
    // The member's name opens the profile; View in timeline opens it at this record.
    expect(href(screen.getByRole("link", { name: "Profile of v3-c7r" }))).toBe("/members/v3-c7r/profile");
    expect(href(screen.getByRole("link", { name: "View in timeline" }))).toBe("/timeline?agent=v3-c7r&focus=5");
    // Links to other records keep the original origin.
    expect(href(screen.getByRole("link", { name: "Open record" })))
      .toBe(`/record/v3-c7r/5?from=${encodeURIComponent(FILTERED)}`);
    expect(href(screen.getByRole("link", { name: "Trace this record" })))
      .toBe(`/decision-trace/v3-c7r/5?from=${encodeURIComponent(FILTERED)}`);
    expect(href(screen.getByRole("link", { name: "v3-c7r:4" })))
      .toBe(`/record/v3-c7r/4?from=${encodeURIComponent(FILTERED)}`);
  });

  it("opened from the profile, it leads back to the profile tab", () => {
    const from = "/members/v3-c7r/profile?tab=chronology";
    renderAt(`/record/v3-c7r/5?from=${encodeURIComponent(from)}`);
    expect(crumbs().getAllByRole("link").map((a) => [a.textContent, href(a)])).toEqual([
      ["Members", "/members"], ["v3-c7r", "/members/v3-c7r/profile"], ["Chronology", from]]);
    expect(crumbs().getByText("Record #5").getAttribute("aria-current")).toBe("page");
  });

  it("a pasted trace link with no origin falls back to the member's Timeline", () => {
    renderAt("/decision-trace/v3-c7r/5");
    expect(href(crumbs().getByRole("link", { name: "Timeline (v3-c7r)" }))).toBe("/timeline?agent=v3-c7r");
    expect(href(crumbs().getByRole("link", { name: "Record #5" }))).toBe("/record/v3-c7r/5");
  });

  it("a from that leaves the app is ignored", () => {
    renderAt(`/decision-trace/v3-c7r/5?from=${encodeURIComponent("//evil.example/x")}`);
    expect(href(crumbs().getByRole("link", { name: "Timeline (v3-c7r)" }))).toBe("/timeline?agent=v3-c7r");
  });
});
