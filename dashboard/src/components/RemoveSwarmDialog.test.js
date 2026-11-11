import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import RemoveSwarmDialog from "./RemoveSwarmDialog.jsx";
import { api } from "../api/client.js";

vi.mock("../api/client.js", () => ({ api: vi.fn() }));
afterEach(() => cleanup());

function mock(remove = async () => ({})) {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation(async (path, opts) => {
    if (path.endsWith("/remove-preview")) return { members: 4, records: 12380 };
    if (path.endsWith("/remove")) return remove(opts);
    throw new Error(path);
  });
}

describe("RemoveSwarmDialog", () => {
  it("shows counts; Remove waits for the name; defaults keep records and free members", async () => {
    mock();
    const onClose = vi.fn();
    const onRemoved = vi.fn();
    render(React.createElement(RemoveSwarmDialog, { name: "echo-base", onClose, onRemoved }));
    expect(await screen.findByText("This affects 4 members and 12,380 records.")).toBeDefined();
    const remove = screen.getByRole("button", { name: "Remove" });
    expect(remove.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Type echo-base to confirm"), { target: { value: "echo-base" } });
    expect(remove.disabled).toBe(false);
    fireEvent.click(remove);
    await waitFor(() => expect(onRemoved).toHaveBeenCalled());
    expect(vi.mocked(api)).toHaveBeenCalledWith("/admin/swarms/echo-base/remove",
      { method: "POST", body: { confirm: "echo-base", members: "free", records: "keep" } });
    expect(onClose).toHaveBeenCalled();
  });

  it("sends the chosen options, warns about them, and shows a server error", async () => {
    mock(async () => { throw new Error("purge in steps: too many"); });
    render(React.createElement(RemoveSwarmDialog, { name: "echo-base", onClose: () => {} }));
    fireEvent.click(screen.getByLabelText("Remove them from LTMS"));
    fireEvent.click(screen.getByLabelText("Purge records that came through this swarm"));
    expect(screen.getByText(/can't send records until re-enrolled/)).toBeDefined();
    expect(screen.getByText(/can't be undone/)).toBeDefined();
    fireEvent.change(screen.getByLabelText("Type echo-base to confirm"), { target: { value: "echo-base" } });
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(await screen.findByText("purge in steps: too many")).toBeDefined();
    expect(vi.mocked(api).mock.calls.at(-1)[1].body).toEqual({ confirm: "echo-base", members: "remove", records: "purge" });
  });

  it("Escape cancels", () => {
    mock();
    const onClose = vi.fn();
    render(React.createElement(RemoveSwarmDialog, { name: "echo-base", onClose }));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
