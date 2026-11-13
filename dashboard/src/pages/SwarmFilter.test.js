// Timeline and Volume read ?swarm= (#331).
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import Volume from "./Volume.jsx";
import Timeline from "./Timeline.jsx";
import { api } from "../api/client.js";
import { useApi } from "../api/useApi.js";

vi.mock("../api/client.js", () => ({ api: vi.fn(async () => ({ records: [], count: 0 })) }));
vi.mock("../api/useApi.js", () => ({
  useApi: vi.fn(() => ({ data: { days: 14, rows: [{ day: "2026-11-12", agent: "z3-r0", type: "event", records: 2, payload_bytes: 0 }] },
    error: null, loading: false, reload: () => {} })),
}));

afterEach(() => { cleanup(); vi.clearAllMocks(); });

let where = "";
function Where() {
  where = useLocation().search;
  return null;
}

function at(url, Page) {
  return render(React.createElement(MemoryRouter, { initialEntries: [url] },
    React.createElement(Routes, null, React.createElement(Route, { path: "*", element:
      React.createElement(React.Fragment, null, React.createElement(Page), React.createElement(Where)) }))));
}

describe("swarm filter", () => {
  it("Volume passes swarm= and the chip removes it", () => {
    at("/volume?swarm=echo-base", Volume);
    expect(useApi).toHaveBeenCalledWith("/admin/volume", { days: "14", swarm: "echo-base" });
    fireEvent.click(screen.getByRole("button", { name: "Remove filter Swarm: echo-base" }));
    expect(where).toBe("");
  });

  it("Timeline passes swarm=, shows a chip, and says deleted records aren't shown", async () => {
    at("/?swarm=echo-base", Timeline);
    await waitFor(() => expect(api).toHaveBeenCalled());
    expect(api.mock.calls[0][1].params.swarm).toBe("echo-base");
    expect(screen.getByRole("button", { name: "Remove filter Swarm: echo-base" })).toBeTruthy();
    expect(screen.getByText("Deleted records aren't shown when filtering by swarm.")).toBeTruthy();
  });
});
