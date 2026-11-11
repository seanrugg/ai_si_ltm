import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import Charter, { Editor, ReadStatus, sortReadStatus } from "./Charter.jsx";
import { api } from "../api/client.js";

vi.mock("../api/client.js", () => ({ api: vi.fn() }));
const VIEW = {
  current: { version: 2, hash: "a".repeat(64), published_at: "2026-11-11T08:00:00+00:00", published_by: "admin:sean",
    markdown: "# Charter\n\nHumans stay in charge. <script>alert(1)</script>" },
  versions: [{ version: 2, hash: "a".repeat(64), published_at: "2026-11-11T08:00:00+00:00", published_by: "admin:sean" },
             { version: 1, hash: "b".repeat(64), published_at: "2026-11-01T08:00:00+00:00", published_by: "admin:sean" }],
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
    expect(api).toHaveBeenCalledWith("/admin/charter", { method: "PUT", body: { markdown: "# New\n\n**Bold** rule" } });
    await waitFor(() => expect(onPublished).toHaveBeenCalled());
  });

  it("views an old version", async () => {
    api.mockResolvedValueOnce({ version: 1, hash: "b".repeat(64), markdown: "# First draft" });
    render(React.createElement(Charter));
    const v1 = screen.getByText("v1").closest("li");
    fireEvent.click(within(v1).getByRole("button", { name: "View" }));
    expect(await screen.findByText("First draft")).toBeDefined();
    expect(api).toHaveBeenCalledWith("/admin/charter/versions/1");
  });
});
