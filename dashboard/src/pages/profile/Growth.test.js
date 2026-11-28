import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import Growth, { GrowthView } from "./Growth.jsx";
import { budgetPatch, daysFor, headlines, ratioText, trendRows } from "../../lib/growth.js";
import { tabsFor } from "../../lib/profile.js";
import { api } from "../../api/client.js";

vi.mock("../../api/client.js", () => ({ api: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

const LEDGER = {
  member: "v3-c7r", days: 7, families: ["qwen"],
  lessons: { proposed: 3, verified: 1, refuted: 0, unsure: 1, unreviewed: 1, promoted: 0, studied: 1, same_family_reviews: 1 },
  reviews_given: { total: 0, verified: 0, refuted: 0, unsure: 0, cross_family: 0 },
  predictions: { made: 1, checked: 1, true: 1, false: 0, unclear: 0, brier: 0.09 },
  questions: { asked: 2, answered_by_others: 1 }, answers: { given: 0 },
  directives: { reported: 2, done: 1, partial: 0, couldnt: 1 },
  energy: { docked: 1, powered_off: 2 },
  actions: { total: 0, outcomes: {} },
  cycles: { count: 4, dreams: 2, paused: 0, written: 3, brain_errors: 1, tokens: 2400, gated: { bad_ref: 2 }, rejected: {} },
  volume: { records: 2400 }, learning_ratio: 1 / 2400,
  daily: { "2026-11-27": { records: 1200, lessons: 2, verified: 0 }, "2026-11-28": { records: 1200, lessons: 1, verified: 1 } },
  lesson_list: [{ key: "v3-c7r:72557", summary: "show_icon takes five icon names", ts: "2026-11-28T15:00:00Z",
    family: "qwen", status: "verified", promoted: false,
    reviews: [{ key: "m4-c2:10", by: "m4-c2", family: "gemma", verdict: "verified", reasons: "matches q3-u3:56" },
              { key: "q3-u3:9", by: "q3-u3", family: "qwen", verdict: "unsure", reasons: "" }] }],
  recent_cycles: [{ key: "v3-c7r:72600", ts: "2026-11-28T15:25:00Z", mode: "dreaming", brain: "local", considered: 2,
    written: 1, paused: false, gated: { bad_ref: 1 }, rejected: [] }],
};

describe("Growth tab (#343)", () => {
  it("is a tab on member and swarm profiles", () => {
    expect(tabsFor("member").map((t) => t.key)).toContain("growth");
    expect(tabsFor("swarm").map((t) => t.key)).toContain("growth");
  });

  it("helpers: days, ratio, headlines, trend, budget body", () => {
    expect(daysFor({ hours: 168 })).toBe(7);
    expect(daysFor({ hours: 0 })).toBe(365);
    expect(ratioText(null)).toBe("—");
    expect(ratioText(0.25)).toBe("25.0%");
    const h = Object.fromEntries(headlines(LEDGER).map((x) => [x.key, x.value]));
    expect(h.verified).toBe(1);
    expect(h.brier).toBe("0.090");
    expect(h.answered).toBe("1 of 2");
    expect(h.energy).toBe("1 / 0 / 2");
    expect(headlines({ ...LEDGER, energy: undefined }).map((x) => x.key)).not.toContain("energy");
    expect(trendRows(LEDGER.daily)[1]).toEqual({ day: "2026-11-28", records: 1200, lessons: 1, verified: 1 });
    expect(budgetPatch({ calls_per_wake: "3", tokens_per_wake: "", writes_per_day: "5" }))
      .toEqual({ body: { budget: { calls_per_wake: 3, tokens_per_wake: null, writes_per_day: 5 } } });
    expect(budgetPatch({ calls_per_wake: "-1" }).error).toMatch(/whole number/);
  });

  it("shows headlines, the trend with its table twin, reviews by family, and recent cycles", () => {
    render(React.createElement(GrowthView, { ledger: LEDGER, subject: "member", settings: null, onSave: () => {} }));
    expect(screen.getByText("Lessons verified by another model family")).toBeTruthy();
    expect(screen.getByRole("img", { name: /Lessons verified per day/ })).toBeTruthy();
    expect(screen.getByRole("table", { name: /lessons verified per day/i })).toBeTruthy();   // the chart's text twin
    expect(screen.getByText(/m4-c2 \(gemma\): verified/)).toBeTruthy();
    expect(screen.getByText(/q3-u3 \(qwen\): unsure · same family, not counted/)).toBeTruthy();
    expect(screen.getByText("bad_ref 1")).toBeTruthy();
    expect(screen.getByText(/Model families: qwen/)).toBeTruthy();
  });

  it("says so when there's no mind activity", () => {
    const quiet = { ...LEDGER, lessons: { proposed: 0 }, cycles: { count: 0 }, reviews_given: { total: 0 } };
    render(React.createElement(GrowthView, { ledger: quiet, subject: "member", settings: null, onSave: () => {} }));
    expect(screen.getByText("No mind activity in this range")).toBeTruthy();
  });

  it("pauses, resumes and saves a budget with PATCH bodies", async () => {
    api.mockImplementation(async (path, opts) => {
      if (path.endsWith("/growth")) return LEDGER;
      if (path.endsWith("/mind") && opts?.method === "PATCH") return { paused: opts.body.paused ?? false, budget: opts.body.budget ?? {} };
      if (path.endsWith("/mind")) return { paused: false, budget: { calls_per_wake: 3 } };
      throw new Error(`unexpected ${path}`);
    });
    render(React.createElement(Growth, { subject: "member", name: "v3-c7r", range: { hours: 168 },
                                         members: [{ name: "v3-c7r", id: "id-1" }] }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toMatch(/running/));
    expect(api).toHaveBeenCalledWith("/admin/members/id-1/growth", { params: { days: 7 } });
    expect(screen.getByLabelText("Brain calls per wake").value).toBe("3");

    fireEvent.click(screen.getByRole("button", { name: "Pause the mind" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/admin/members/id-1/mind", { method: "PATCH", body: { paused: true } }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toMatch(/paused/));

    fireEvent.change(screen.getByLabelText("Records written per day"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save budget" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/admin/members/id-1/mind", {
      method: "PATCH", body: { budget: { calls_per_wake: null, tokens_per_wake: null, writes_per_day: 5 } } }));
  });

  it("shows the swarm roll-up with its members and no mind controls", async () => {
    api.mockResolvedValue({ ...LEDGER, member: undefined, lesson_list: undefined, recent_cycles: undefined,
      members: [{ name: "v3-c7r", verified_lessons: 1, learning_ratio: 1 / 2400 }] });
    render(React.createElement(Growth, { subject: "swarm", name: "echo-base", range: { hours: 168 }, members: [] }));
    await waitFor(() => expect(screen.getByRole("table", { name: /Each member's verified lessons/ })).toBeTruthy());
    expect(api).toHaveBeenCalledWith("/admin/swarms/echo-base/growth", { params: { days: 7 } });
    expect(screen.queryByText("Mind controls")).toBeNull();
  });
});
