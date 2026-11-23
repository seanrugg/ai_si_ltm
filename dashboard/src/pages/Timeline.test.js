import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

function mockApi({ page = [rec(6, "six"), rec(5, "five"), rec(3, "three"), rec(1, "one")] } = {}) {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation(async (path, opts = {}) => {
    if (path === "/admin/timeline") return { records: opts.params?.type === "none" ? [] : page };
    if (path === "/admin/tombstones") return { tombstones: [tombstone] };
    throw new Error(`unexpected ${path}`);
  });
}

const tombstoneCalls = () => vi.mocked(api).mock.calls.filter(([path]) => path === "/admin/tombstones");

function renderAt(url) {
  return render(React.createElement(MemoryRouter, { initialEntries: [url] }, React.createElement(Timeline)));
}

describe("tombstones on a member's timeline", () => {
  it("shows a deleted record where its seq falls, from one request", async () => {
    mockApi();
    renderAt("/?agent=z3-r0");
    await screen.findByText("six");
    await waitFor(() => expect(screen.getByText(/Deleted record z3-r0:4\./)).toBeDefined());
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toHaveLength(5);
    expect(items[1]).toContain("five");
    expect(items[2]).toMatch(/Deleted record z3-r0:4\. Deleted by sean on .+: test data/);
    expect(items[3]).toContain("three");
    // One request for the page, not one per missing seq; a short page is the
    // last, so its range has no lower bound.
    expect(tombstoneCalls()).toHaveLength(1);
    expect(tombstoneCalls()[0][1].params).toEqual({ agent: "z3-r0" });
  });

  it("still looks for tombstones with a date filter (gaps inside the range are deletions)", async () => {
    mockApi();
    renderAt("/?agent=z3-r0&since=2026-11-01T00:00:00Z");
    await screen.findByText("six");
    await waitFor(() => expect(screen.getByText(/Deleted record z3-r0:4\./)).toBeDefined());
    expect(tombstoneCalls()).toHaveLength(1);
    expect(tombstoneCalls()[0][1].params).toEqual({ agent: "z3-r0", seq_from: 1 });
  });

  it("doesn't look for tombstones when other filters explain the gaps", async () => {
    for (const filter of ["type=event", "outcome=success", "place=lab", "q=six"]) {
      mockApi();
      renderAt(`/?agent=z3-r0&${filter}`);
      await screen.findByText("six");
      expect(tombstoneCalls()).toHaveLength(0);
      expect(screen.queryByText(/Deleted record/)).toBeNull();
      cleanup();
    }
  });

  it("doesn't look for tombstones across all members", async () => {
    mockApi();
    renderAt("/");
    await screen.findByText("six");
    expect(tombstoneCalls()).toHaveLength(0);
  });
});

describe("the Types picker's None", () => {
  it("pressing None leaves nothing selected and the timeline empty", async () => {
    mockApi();
    renderAt("/");
    await screen.findByText("six");
    fireEvent.click(screen.getByRole("button", { name: "All except sensor data" }));
    fireEvent.click(screen.getByRole("button", { name: "None" }));
    await screen.findByText("No records match");
    expect(screen.getByRole("button", { name: "No types" })).toBeDefined();
    const group = screen.getByRole("group", { name: "Record types" });
    expect(within(group).getAllByRole("checkbox").every((c) => !c.checked)).toBe(true);
    expect(vi.mocked(api).mock.calls.at(-1)[1].params.type).toBe("none");
  });

  it("shows no records and a \"Type: none\" chip", async () => {
    mockApi();
    renderAt("/?type=none");
    await screen.findByText("No records match");
    expect(screen.getByRole("button", { name: /Remove filter Type: none/ })).toBeDefined();
    expect(vi.mocked(api).mock.calls[0][1].params.type).toBe("none");
  });
});

describe("View in timeline: focus on one record (#393)", () => {
  it("opens and marks the focused record", async () => {
    mockApi();
    renderAt("/timeline?agent=z3-r0&focus=5");
    const five = (await screen.findAllByText("five"))[0];
    const row = five.closest("li");
    expect(row.getAttribute("aria-current")).toBe("true");
    expect(within(row).getByRole("button", { name: /five/ }).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("six").closest("li").getAttribute("aria-current")).toBeNull();
    // Links opened from here carry the timeline (with its filters) as their origin.
    expect(within(row).getByRole("link", { name: "Trace this record" }).getAttribute("href"))
      .toBe(`/decision-trace/z3-r0/5?from=${encodeURIComponent("/timeline?agent=z3-r0&focus=5")}`);
  });

  it("loads older pages until it finds the record, and says so when it can't", async () => {
    const page1 = Array.from({ length: 50 }, (_, i) => rec(100 - i, `r${100 - i}`));
    const page2 = [rec(9, "nine"), rec(8, "eight")];
    vi.mocked(api).mockReset();
    vi.mocked(api).mockImplementation(async (path, opts = {}) => {
      if (path === "/admin/timeline") return { records: opts.params.offset ? page2 : page1 };
      return { tombstones: [] };
    });
    renderAt("/timeline?agent=z3-r0&focus=8");
    expect((await screen.findAllByText("eight"))[0].closest("li").getAttribute("aria-current")).toBe("true");
    cleanup();
    renderAt("/timeline?agent=z3-r0&focus=2");
    expect(await screen.findByText(/z3-r0 #2 isn't in this list/)).toBeDefined();
    expect(screen.getByRole("link", { name: "Open #2 on its own" })).toBeDefined();
  });
});

describe("records naming a member (#477)", () => {
  const timelineCalls = () => vi.mocked(api).mock.calls.filter(([path]) => path === "/admin/timeline");

  it("offers the checkbox only with a Member filter", async () => {
    mockApi();
    renderAt("/");
    await screen.findByText("six");
    expect(screen.queryByLabelText("Include records naming this member")).toBeNull();
  });

  it("asks for records naming the member and shows a chip", async () => {
    mockApi();
    renderAt("/?agent=z3-r0");
    await screen.findByText("six");
    expect(timelineCalls().at(-1)[1].params.involving).toBeUndefined();
    fireEvent.click(screen.getByLabelText("Include records naming this member"));
    await waitFor(() => expect(timelineCalls().at(-1)[1].params.involving).toBe(true));
    expect(timelineCalls().at(-1)[1].params.agent).toBe("z3-r0");
    const chips = screen.getByRole("group", { name: "Active filters" });
    expect(within(chips).getByText(/Including records naming z3-r0/)).toBeDefined();
  });

  it("ignores involving without a member", async () => {
    mockApi();
    renderAt("/?involving=1");
    await screen.findByText("six");
    expect(timelineCalls().at(-1)[1].params.involving).toBeUndefined();
    expect(screen.queryByText(/Including records naming/)).toBeNull();
  });
});
