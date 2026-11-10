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
  render(React.createElement("ul", null,
    React.createElement(EndpointRow, { endpoint: paused, backlog: 3, onReload: vi.fn(), ...props })));
}

describe("pausing and resuming an LRS endpoint (issue #175)", () => {
  it("says paused records are never sent", () => {
    row();
    expect(screen.getByText("Paused", { selector: "label" })).toBeTruthy();
    expect(screen.getByText(/new records aren.t sent to this LRS, now or later/)).toBeTruthy();
  });

  it("resuming is a plain PATCH, with no backlog question", async () => {
    row();
    fireEvent.click(screen.getByRole("checkbox", { name: "Send to lrs-one" }));
    await vi.waitFor(() => expect(api).toHaveBeenCalled());
    expect(api).toHaveBeenCalledWith("/admin/lrs/ep1", { method: "PATCH", body: { enabled: true } });
    expect(screen.queryByText(/written while sending was off/)).toBeNull();
  });

  it("pausing is a plain PATCH", async () => {
    row({ endpoint: { ...paused, enabled: true } });
    expect(screen.getByText("Sending", { selector: "label" })).toBeTruthy();
    expect(screen.queryByText(/now or later/)).toBeNull();
    fireEvent.click(screen.getByRole("checkbox", { name: "Send to lrs-one" }));
    await vi.waitFor(() => expect(api).toHaveBeenCalled());
    expect(api).toHaveBeenCalledWith("/admin/lrs/ep1", { method: "PATCH", body: { enabled: false } });
  });
});
