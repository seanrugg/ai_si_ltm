import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import Charter, { CharterDiff, Editor, History, ReadStatus, sortReadStatus, visibleVersions } from "./Charter.jsx";
import { api } from "../api/client.js";

vi.mock("../api/client.js", () => ({ api: vi.fn() }));
const VIEW = {
  current: { version: 2, hash: "a".repeat(64), published_at: "2026-11-11T08:00:00+00:00", published_by: "admin:sean",
    markdown: "# Charter\n\nHumans stay in charge. <script>alert(1)</script>" },
  versions: [{ version: 2, hash: "a".repeat(64), published_at: "2026-11-11T08:00:00+00:00", published_by: "admin:sean",
               note: "Added Article 2", status: "current", reads: 1 },
             { version: 1, hash: "b".repeat(64), published_at: "2026-11-01T08:00:00+00:00", published_by: "admin:sean",
               note: null, status: "superseded", reads: 3 }],
  members: [
    { name: "z3-r0", read: true, read_at: "2026-11-11T09:00:00+00:00", due: null, overdue: false },
    { name: "vector", read: false, read_at: null, due: "2026-11-25T08:00:00+00:00", overdue: false },
    { name: "jetson-hub", read: false, read_at: null, due: "2026-11-01T08:00:00+00:00", overdue: true },
  ],
  grace_days: 14,
};
vi.mock("../api/useApi.js", () => ({
  useApi: vi.fn(() => ({ data: VIEW, error: null, loading: false, reload: () => {} })),
}));

afterEach(() => { cleanup(); api.mockReset(); });

describe("Charter page", () => {
  it("shows the current charter rendered safely, with its hash", () => {
    render(React.createElement(Charter));
    expect(screen.getByRole("heading", { name: "Current charter: version 2" })).toBeDefined();
    expect(screen.getAllByText("aaaaaaaaaaaa…").length).toBe(2); // the current charter and its history row
    expect(screen.getByRole("button", { name: "Copy the charter hash" })).toBeDefined();
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getAllByText(/<script>alert\(1\)<\/script>/).length).toBeGreaterThan(0);
  });

  it("puts the help button inside the page heading, beside the title (#377)", () => {
    render(React.createElement(Charter));
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading.textContent.startsWith("Fleet charter")).toBe(true);
    expect(within(heading).getByRole("button", { name: "About the charter" })).toBeDefined();
  });

  it("puts members who haven't read it first, overdue first", () => {
    expect(sortReadStatus(VIEW.members).map((m) => m.name)).toEqual(["jetson-hub", "vector", "z3-r0"]);
    render(React.createElement(ReadStatus, { members: VIEW.members }));
    const table = screen.getByRole("table");
    expect(within(table).getByText("Which members have read the current charter")).toBeDefined();
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows[0].textContent).toMatch(/jetson-hub.*writes paused/);
    expect(rows[0].querySelector(".text-warn")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Not yet (2)" }));
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(3);
  });

  it("previews, confirms with the grace period, then publishes", async () => {
    api.mockResolvedValueOnce({ published: true, version: 3 });
    const onPublished = vi.fn();
    render(React.createElement(Editor, { initial: "# Old", graceDays: 21, onPublished }));
    const box = screen.getByLabelText("Charter (markdown)");
    expect(screen.getByRole("button", { name: "Publish" }).disabled).toBe(true); // unchanged
    fireEvent.change(box, { target: { value: "# New\n\n**Bold** rule" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    const preview = screen.getByRole("region", { name: "Preview" });
    expect(within(preview).getByText("Bold").tagName).toBe("STRONG");
    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    expect(screen.getByText(/After 21 days, members who haven't read it can't write records/)).toBeDefined();
    expect(api).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Publish this version" }));
    await screen.findByText("Published version 3.");
    expect(api).toHaveBeenCalledWith("/admin/charter", { method: "PUT",
      body: { markdown: "# New\n\n**Bold** rule", note: null } });
    await waitFor(() => expect(onPublished).toHaveBeenCalled());
  });

  it("views an old version", async () => {
    api.mockResolvedValueOnce({ version: 1, hash: "b".repeat(64), markdown: "# First draft" });
    render(React.createElement(Charter));
    fireEvent.click(screen.getByRole("button", { name: "View version 1" }));
    expect(await screen.findByText("First draft")).toBeDefined();
    expect(api).toHaveBeenCalledWith("/admin/charter/versions/1");
  });

  it("publishes with a change note", async () => {
    api.mockResolvedValueOnce({ published: true, version: 3, note: "Article 4 tightened" });
    render(React.createElement(Editor, { initial: "# Old", graceDays: 14 }));
    fireEvent.change(screen.getByLabelText("Charter (markdown)"), { target: { value: "# New" } });
    const note = screen.getByRole("textbox", { name: /Change note/ });
    expect(note.maxLength).toBe(200);
    fireEvent.change(note, { target: { value: "  Article 4 tightened " } });
    expect(screen.getByText("22/200")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    fireEvent.click(screen.getByRole("button", { name: "Publish this version" }));
    await screen.findByText("Published version 3.");
    expect(api).toHaveBeenCalledWith("/admin/charter", { method: "PUT", body: { markdown: "# New", note: "Article 4 tightened" } });
  });

  it("says when publishing old text made that version current", async () => {
    api.mockResolvedValueOnce({ published: true, version: 4, reverted_from: 1 });
    render(React.createElement(Editor, { initial: "# Old", graceDays: 14 }));
    fireEvent.change(screen.getByLabelText("Charter (markdown)"), { target: { value: "# First" } });
    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    fireEvent.click(screen.getByRole("button", { name: "Publish this version" }));
    expect(await screen.findByText("Same text as version 1: published as version 4.")).toBeDefined();
  });
});

const HISTORY = [
  { version: 4, hash: "d".repeat(64), published_at: "2026-11-12T08:00:00+00:00", published_by: "admin:sean",
    note: "Reverted to version 1", reverted_from: 1, status: "current", reads: 0 },
  { version: 3, hash: "c".repeat(64), published_at: "2026-11-11T08:00:00+00:00", published_by: "admin:sean",
    note: null, status: "superseded", reads: 0 },
  { version: 2, hash: "a".repeat(64), published_at: "2026-11-10T08:00:00+00:00", published_by: "admin:sean",
    note: "Typo", status: "withdrawn", withdrawn: { reason: "Wrong article numbers" }, reads: 2 },
  { version: 1, hash: "d".repeat(64), published_at: "2026-11-01T08:00:00+00:00", published_by: "admin:sean",
    note: null, status: "superseded", reads: 3 },
  { version: 5, hash: "e".repeat(64), status: "deleted", deleted_by: "admin:sean",
    deleted_at: "2026-11-13T08:00:00+00:00", reads: 0 },
];

describe("Charter history (#345)", () => {
  const renderHistory = (onChanged = vi.fn()) =>
    render(React.createElement(History, { versions: HISTORY, current: 4, onChanged }));
  const row = (n) => screen.getByText(`v${n}`).closest("tr");

  it("lists status, note and reads, hiding withdrawn and deleted until asked", () => {
    expect(visibleVersions(HISTORY, false).map((v) => v.version)).toEqual([4, 3, 1]);
    renderHistory();
    expect(within(row(4)).getByText("Current")).toBeDefined();
    expect(within(row(4)).getByText("Reverted to version 1")).toBeDefined();
    expect(within(row(3)).getByText("no note")).toBeDefined();
    expect(within(row(1)).getByText("3")).toBeDefined();
    expect(screen.queryByText("v2")).toBeNull();
    fireEvent.click(screen.getByLabelText(/Show withdrawn and deleted \(2\)/));
    expect(within(row(2)).getByText("Withdrawn")).toBeDefined();
    expect(within(row(2)).getByText("Withdrawn: Wrong article numbers")).toBeDefined();
    expect(within(row(5)).getByText("Deleted")).toBeDefined();
    expect(within(row(5)).queryByRole("button")).toBeNull();
  });

  it("offers each action only where it's allowed", () => {
    renderHistory();
    expect(within(row(4)).queryByRole("button", { name: /Make version 4 current/ })).toBeNull();
    expect(within(row(4)).queryByRole("button", { name: /Withdraw/ })).toBeNull();
    expect(within(row(4)).queryByRole("button", { name: /Delete/ })).toBeNull();
    expect(within(row(3)).getByRole("button", { name: "Delete version 3…" })).toBeDefined(); // unread
    expect(within(row(1)).queryByRole("button", { name: /Delete/ })).toBeNull(); // read: withdraw only
    expect(within(row(1)).getByRole("button", { name: "Withdraw version 1…" })).toBeDefined();
  });

  it("makes an old version current", async () => {
    const onChanged = vi.fn();
    api.mockResolvedValueOnce({ published: true, version: 5, reverted_from: 3 });
    renderHistory(onChanged);
    fireEvent.click(screen.getByRole("button", { name: "Make version 3 current" }));
    expect(await screen.findByText("Version 3 is current again, as version 5.")).toBeDefined();
    expect(api).toHaveBeenCalledWith("/admin/charter/versions/3/make-current", { method: "POST" });
    expect(onChanged).toHaveBeenCalled();
  });

  it("withdraws with a reason", async () => {
    api.mockResolvedValueOnce({ version: 1, status: "withdrawn" });
    renderHistory();
    fireEvent.click(screen.getByRole("button", { name: "Withdraw version 1…" }));
    const submit = screen.getByRole("button", { name: "Withdraw version 1" });
    expect(submit.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "Replaced" } });
    fireEvent.click(submit);
    expect(await screen.findByText("Withdrew version 1.")).toBeDefined();
    expect(api).toHaveBeenCalledWith("/admin/charter/versions/1/withdraw", { method: "POST", body: { reason: "Replaced" } });
  });

  it("deletes only after the version number is typed", async () => {
    api.mockResolvedValueOnce({ version: 3, status: "deleted" });
    renderHistory();
    fireEvent.click(screen.getByRole("button", { name: "Delete version 3…" }));
    const submit = screen.getByRole("button", { name: "Delete version 3" });
    const box = screen.getByLabelText(/Type 3 to delete it for good/);
    fireEvent.change(box, { target: { value: "4" } });
    expect(submit.disabled).toBe(true);
    fireEvent.change(box, { target: { value: "3" } });
    fireEvent.click(submit);
    expect(await screen.findByText("Deleted version 3.")).toBeDefined();
    expect(api).toHaveBeenCalledWith("/admin/charter/versions/3?confirm=3", { method: "DELETE" });
  });

  it("compares two versions", async () => {
    api.mockImplementation(async (path) => (path.endsWith("/1")
      ? { version: 1, markdown: "## Article 1\nHumans stay in charge.\n" }
      : { version: 4, markdown: "## Article 1\nHumans stay in charge, always.\n" }));
    renderHistory();
    fireEvent.click(screen.getByRole("button", { name: "Compare version 1" }));
    expect(await screen.findByText("Version 1 → version 4: 1 line added, 1 removed, in Article 1.")).toBeDefined();
  });
});

describe("CharterDiff", () => {
  it("marks lines with + and − in the text, not just colour", () => {
    render(React.createElement(CharterDiff, {
      from: { version: 1, markdown: "a\nb\n" }, to: { version: 2, markdown: "a\nc\n" } }));
    const text = screen.getByLabelText("Line diff").textContent;
    expect(text).toMatch(/−removed: b/);
    expect(text).toMatch(/\+added: c/);
    expect(screen.getByRole("status").textContent).toBe("Version 1 → version 2: 1 line added, 1 removed.");
  });
});
