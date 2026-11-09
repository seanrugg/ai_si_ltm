import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { EndpointRow } from "./Lrs.jsx";
import { api } from "../api/client.js";

vi.mock("../api/client.js", () => ({ api: vi.fn() }));
afterEach(() => cleanup());

const paused = { id: "ep1", name: "lrs-one", url: "https://lrs.example.org", xapi_version: "2.0.0", enabled: false };

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockResolvedValue({});
});

function row(props) {
  const onReload = vi.fn();
  render(React.createElement("ul", null,
    React.createElement(EndpointRow, { endpoint: paused, backlog: 3, onReload, ...props })));
  return onReload;
}

describe("resuming a paused LRS endpoint (issue #151)", () => {
  it("labels the toggle Sending / Paused", () => {
    row();
    expect(screen.getByText("Paused", { selector: "label" })).toBeTruthy();
  });

  it("asks before resuming with a backlog, with Skip preselected", () => {
    row();
    fireEvent.click(screen.getByRole("checkbox", { name: "Send to lrs-one" }));
    expect(api).not.toHaveBeenCalled();
    expect(screen.getByText("3 records were written while sending was off.")).toBeTruthy();
    expect(screen.getByRole("radio", { name: /Skip them/ }).checked).toBe(true);
    expect(screen.getByText(/includes records from real members/)).toBeTruthy();
    expect(screen.getByRole("group", { name: /What should happen/ })).toBeTruthy();
  });

  it("sends the chosen mode to the resume endpoint", async () => {
    const onReload = row();
    fireEvent.click(screen.getByRole("checkbox", { name: "Send to lrs-one" }));
    fireEvent.click(screen.getByRole("radio", { name: /Send them to the LRS/ }));
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    await vi.waitFor(() => expect(onReload).toHaveBeenCalled());
    expect(api).toHaveBeenCalledWith("/admin/lrs/ep1/resume", { method: "POST", body: { backlog: "send" } });
  });

  it("Cancel closes the panel without a request", () => {
    row();
    fireEvent.click(screen.getByRole("checkbox", { name: "Send to lrs-one" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText(/written while sending was off/)).toBeNull();
    expect(api).not.toHaveBeenCalled();
  });

  it("with no backlog, turning on is a plain PATCH", async () => {
    row({ backlog: 0 });
    fireEvent.click(screen.getByRole("checkbox", { name: "Send to lrs-one" }));
    await vi.waitFor(() => expect(api).toHaveBeenCalled());
    expect(api).toHaveBeenCalledWith("/admin/lrs/ep1", { method: "PATCH", body: { enabled: true } });
  });

  it("turning off is a plain PATCH", async () => {
    row({ endpoint: { ...paused, enabled: true } });
    expect(screen.getByText("Sending", { selector: "label" })).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: "Send to lrs-one" }));
    await vi.waitFor(() => expect(api).toHaveBeenCalled());
    expect(api).toHaveBeenCalledWith("/admin/lrs/ep1", { method: "PATCH", body: { enabled: false } });
  });
});
