import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import PingButton from "./PingButton.jsx";

vi.mock("../api/client.js", () => ({ api: vi.fn() }));
afterEach(() => cleanup());

const status = (mode, extra = {}) => ({
  member: "m", requested_at: null, delivered_at: null, answered_at: null, mode, ...extra,
});

describe("PingButton answer modes (#384)", () => {
  it("is off for a member that can't answer, with the reason as visible text", () => {
    render(React.createElement(PingButton, {
      name: "held", initial: status({ mode: "none", source: "admin", reason: "an admin submits for it" }),
    }));
    const button = screen.getByRole("button", { name: "Ping held" });
    expect(button.disabled).toBe(true);
    const reason = screen.getByText(/Ping is off: .*an admin submits for it/);
    expect(button.getAttribute("aria-describedby")).toBe(reason.id);
  });

  it("explains an on-use member in the keyboard-reachable help, and says it answers when next used", () => {
    render(React.createElement(PingButton, {
      name: "chat", initial: status({ mode: "on_use", source: "inferred", reason: null },
        { requested_at: new Date(Date.now() - 60_000).toISOString() }),
    }));
    expect(screen.getByRole("button", { name: "Ping chat" }).disabled).toBe(false);
    expect(screen.getByRole("status").textContent).toMatch(/^Waiting: answers when next used/);
    const help = screen.getByRole("button", { name: "About ping" });
    fireEvent.click(help);
    expect(screen.getByRole("note").textContent).toMatch(/only runs when someone uses it/);
  });
});
