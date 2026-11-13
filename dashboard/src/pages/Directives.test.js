import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import Directives, { deliveryText, targetText } from "./Directives.jsx";
import { api } from "../api/client.js";

vi.mock("../api/client.js", () => ({ api: vi.fn(async () => ({})) }));
const DIRS = [{ id: "d1", text: "Dock by 22:00", scope: { swarm: "s1", swarm_name: "echo-base" }, issued_by: "admin:sean",
  issued_at: "2026-11-13T02:00:00Z", status: "open",
  delivery: { meant_for: 3, received: 2, accepted: 1, declined: 1, declines: [{ member: "vector", reason: "on the charger" }] } }];
vi.mock("../api/useApi.js", () => ({
  useApi: vi.fn((path) => ({ data: path === "/admin/directives" ? { directives: DIRS }
    : [{ id: "s1", name: "echo-base", status: "active" }], error: null, loading: false, reload: () => {} })),
}));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("Directives page (#307)", () => {
  it("lists directives with target, status in text, delivery and declines", () => {
    render(React.createElement(Directives));
    expect(screen.getByRole("table", { name: "Directives, newest first" })).toBeTruthy();
    expect(screen.getByText("meant for 3 · received 2 · accepted 1 · declined 1")).toBeTruthy();
    expect(screen.getByText("Open")).toBeTruthy();
    expect(screen.getByText(/on the charger/)).toBeTruthy();
    expect(targetText({ scope: { all: true } })).toBe("All swarms");
    expect(deliveryText(null)).toBe("—");
  });

  it("issues only after a confirm step that says directives are followed by default", async () => {
    render(React.createElement(Directives));
    fireEvent.change(screen.getByLabelText("Target"), { target: { value: "echo-base" } });
    fireEvent.change(screen.getByLabelText("Directive"), { target: { value: "Report health" } });
    fireEvent.click(screen.getByRole("button", { name: "Review" }));
    expect(api).not.toHaveBeenCalled();
    expect(screen.getByText(/Members follow directives by default/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Issue directive" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/admin/directives", { method: "POST",
      body: { scope: { swarm: "echo-base" }, text: "Report health", expires_in_days: 7 } }));
  });

  it("cancels an open directive after confirming", async () => {
    render(React.createElement(Directives));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm cancel" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/admin/directives/d1/cancel", { method: "POST" }));
  });
});
