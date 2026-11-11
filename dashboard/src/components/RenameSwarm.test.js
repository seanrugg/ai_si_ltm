import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import RenameSwarm from "./RenameSwarm.jsx";
import { api } from "../api/client.js";

vi.mock("../api/client.js", () => ({ api: vi.fn() }));
afterEach(() => cleanup());

describe("RenameSwarm", () => {
  it("asks for confirmation with the MQTT warning, then sends PATCH", async () => {
    vi.mocked(api).mockReset();
    vi.mocked(api).mockResolvedValue({});
    const onDone = vi.fn();
    render(React.createElement(RenameSwarm, { name: "echo-base", onDone }));
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    fireEvent.change(screen.getByLabelText("New name"), { target: { value: "hoth-base" } });
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    expect(screen.getByRole("alert").textContent).toContain("Rename echo-base to hoth-base?");
    expect(screen.getByRole("alert").textContent).toContain("broker ACL");
    expect(vi.mocked(api)).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Yes, rename" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(vi.mocked(api)).toHaveBeenCalledWith("/admin/swarms/echo-base", { method: "PATCH", body: { name: "hoth-base" } });
  });

  it("shows the server's error", async () => {
    vi.mocked(api).mockReset();
    vi.mocked(api).mockRejectedValue(new Error("a swarm named other already exists"));
    render(React.createElement(RenameSwarm, { name: "echo-base" }));
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    fireEvent.change(screen.getByLabelText("New name"), { target: { value: "other" } });
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, rename" }));
    expect(await screen.findByText("a swarm named other already exists")).toBeDefined();
  });
});
