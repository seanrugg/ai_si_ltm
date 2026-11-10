import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import RecordRow, { RecordDetail, TombstoneRow } from "./RecordRow.jsx";
import { api } from "../api/client.js";
import { RECORD_DELETE, WIPE_VS_DELETE } from "../lib/help.js";

vi.mock("../api/client.js", () => ({ api: vi.fn() }));

const HASH = "a".repeat(64);
const record = {
  v: 1, agent: "z3-r0", seq: 4, ts: "2026-11-10T12:00:00+00:00", type: "action", summary: "Drove forward",
  prev_hash: "b".repeat(64), hash: HASH, payload_hash: "c".repeat(64), payload: { m: 0.5 },
};
const tombstone = {
  deleted: true, agent: "z3-r0", seq: 4, hash: HASH, prev_hash: "b".repeat(64),
  deleted_at: "2026-11-10T13:00:00+00:00", deleted_by: "admin:sean", reason: "test data",
};
let preview;

beforeEach(() => {
  preview = { agent: "z3-r0", seq: 4, type: "action", summary: "Drove forward", referenced_by: [], policy_effect: null };
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation(async (path, opts = {}) => {
    if (path.endsWith("/delete-preview")) return preview;
    if (opts.method === "DELETE") return tombstone;
    if (path === "/admin/records/vector/9") return { ...tombstone, agent: "vector", seq: 9 };
    return record;
  });
});
afterEach(() => cleanup());

function renderDetail(props = {}) {
  return render(React.createElement(MemoryRouter, null,
    React.createElement(RecordDetail, { record, ...props })));
}

async function openPanel() {
  fireEvent.click(screen.getByRole("button", { name: "Delete record" }));
  return screen.findByRole("region", { name: "Delete record z3-r0:4" });
}

describe("Delete record", () => {
  it("offers Delete record with its help next to Trace this record", () => {
    renderDetail();
    expect(screen.getByRole("link", { name: "Trace this record" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Delete record" })).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "About deleting a record" }));
    expect(screen.getByRole("note").textContent).toBe(RECORD_DELETE);
  });

  it("opens a labelled panel that takes focus, says what is deleted and explains wipe vs delete", async () => {
    renderDetail();
    const panel = await openPanel();
    expect(document.activeElement).toBe(panel);
    expect(panel.textContent).toContain("This permanently deletes action record z3-r0:4 (Drove forward).");
    expect(panel.textContent).toContain("Its chain stays valid: a tombstone with only its hash is kept.");
    expect(api).toHaveBeenCalledWith("/admin/records/z3-r0/4/delete-preview", { params: undefined });
    fireEvent.click(screen.getByRole("button", { name: "Wipe vs delete" }));
    expect(screen.getByRole("note").textContent).toBe(WIPE_VS_DELETE);
    expect(screen.getByLabelText("Reason (optional)")).toBeDefined();
  });

  it("lists the records that refer to it", async () => {
    preview.referenced_by = [
      { agent: "z3-r0", seq: 5, type: "decision", summary: "Slow down" },
      { agent: "vector", seq: 2, type: "lesson", summary: null },
    ];
    renderDetail();
    const panel = await openPanel();
    await waitFor(() => expect(panel.textContent).toContain("These 2 records refer to it; they'll show it as deleted."));
    expect(panel.textContent).toContain("decision z3-r0:5, Slow down");
    expect(panel.textContent).toContain("lesson vector:2");
  });

  it("needs the extra checkbox when deleting changes which policies are in force", async () => {
    preview.policy_effect = "z3-r0:4 is in force; deleting it takes it out of force.";
    renderDetail();
    await openPanel();
    const warning = await screen.findByText(/Warning: z3-r0:4 is in force/);
    expect(warning.tagName).toBe("STRONG");
    const del = screen.getByRole("button", { name: "Delete" });
    expect(del.disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("I understand this changes which policies are in force."));
    expect(del.disabled).toBe(false);
  });

  it("deletes with the reason and then shows the tombstone as a focused status", async () => {
    renderDetail();
    await openPanel();
    fireEvent.change(screen.getByLabelText("Reason (optional)"), { target: { value: " test data " } });
    const del = screen.getByRole("button", { name: "Delete" });
    await waitFor(() => expect(del.disabled).toBe(false));
    fireEvent.click(del);
    const status = await screen.findByRole("status");
    expect(api).toHaveBeenCalledWith("/admin/records/z3-r0/4", { method: "DELETE", body: { reason: "test data" } });
    expect(status.textContent).toContain("Deleted record z3-r0:4.");
    expect(status.textContent).toMatch(/Deleted by sean on .+: test data/);
    expect(document.activeElement).toBe(status);
    expect(screen.queryByRole("button", { name: "Delete record" })).toBeNull();
  });

  it("sends no reason when the field is empty", async () => {
    renderDetail();
    await openPanel();
    const del = screen.getByRole("button", { name: "Delete" });
    await waitFor(() => expect(del.disabled).toBe(false));
    fireEvent.click(del);
    await screen.findByRole("status");
    expect(api).toHaveBeenCalledWith("/admin/records/z3-r0/4", { method: "DELETE", body: { reason: null } });
  });

  it("returns focus to Delete record on Cancel", async () => {
    renderDetail();
    await openPanel();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("region", { name: "Delete record z3-r0:4" })).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Delete record" }));
  });

  it("shows the server's error and keeps the panel open", async () => {
    vi.mocked(api).mockImplementation(async (path, opts = {}) => {
      if (opts.method === "DELETE") throw new Error("z3-r0:4 was already deleted");
      return preview;
    });
    renderDetail();
    await openPanel();
    const del = screen.getByRole("button", { name: "Delete" });
    await waitFor(() => expect(del.disabled).toBe(false));
    fireEvent.click(del);
    expect((await screen.findByRole("alert")).textContent).toContain("already deleted");
    expect(screen.getByRole("region", { name: "Delete record z3-r0:4" })).toBeDefined();
  });

  it("turns a list row into its tombstone after the delete", async () => {
    render(React.createElement(MemoryRouter, null,
      React.createElement("ul", null, React.createElement(RecordRow, { record }))));
    fireEvent.click(screen.getByRole("button", { expanded: false, name: /Drove forward/ }));
    await openPanel();
    const del = screen.getByRole("button", { name: "Delete" });
    await waitFor(() => expect(del.disabled).toBe(false));
    fireEvent.click(del);
    expect((await screen.findByRole("status")).textContent).toContain("Deleted record z3-r0:4.");
    expect(screen.queryByText("Drove forward")).toBeNull();
  });
});

describe("tombstones and refs", () => {
  it("shows who deleted a record, when and why", () => {
    render(React.createElement("ul", null, React.createElement(TombstoneRow, { tombstone })));
    expect(screen.getByRole("listitem").textContent).toMatch(/Deleted record z3-r0:4\. Deleted by sean on .+: test data/);
  });

  it("shows a ref to a deleted record as text, and other refs as links", async () => {
    renderDetail({ record: { ...record, refs: { because: "vector:9", policy: "z3-r0:1" } } });
    expect(await screen.findByText("vector:9 (deleted record)")).toBeDefined();
    expect(screen.queryByRole("link", { name: "vector:9" })).toBeNull();
    expect(screen.getByRole("link", { name: "z3-r0:1" }).getAttribute("href")).toBe("/record/z3-r0/1");
  });
});
