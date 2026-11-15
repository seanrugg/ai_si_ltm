import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import Shell from "./Shell.jsx";
import { api } from "../api/client.js";

vi.mock("../api/client.js", () => ({ api: vi.fn() }));
vi.mock("../App.jsx", () => ({ useAdmin: () => ({ username: "admin", logout: () => {} }) }));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

const DONE = { charter_published: true, swarm_created: true, hub_appointed: true, lrs_added: false,
               member_approved: true };
const EMPTY = Object.fromEntries(Object.keys(DONE).map((k) => [k, false]));

function mockStatus(over = {}) {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockResolvedValue({
    as_of: "2026-11-14T12:00:00+00:00", members_pending: 0, charter_overdue: 0, directive_declines: 0,
    lrs_failing: 0, setup: DONE, ...over,
  });
}

function renderAt(url) {
  return render(React.createElement(MemoryRouter, { initialEntries: [url] },
    React.createElement(Shell, null, React.createElement("p", null, "page"))));
}

const nav = () => screen.getByRole("navigation", { name: "Main" });
const header = (name) => within(nav()).getByRole("button", { name: new RegExp(`^.?${name}`) });

describe("Shell nav (#379)", () => {
  it("shows Overview first, then the four groups in order", async () => {
    mockStatus();
    renderAt("/activity");
    await waitFor(() => expect(api).toHaveBeenCalled());
    const links = within(nav()).getAllByRole("link").map((a) => a.textContent);
    expect(links.slice(0, 1)).toEqual(["Overview"]);
    expect(within(nav()).getAllByRole("link", { hidden: true }).map((a) => a.textContent)).toEqual([
      "Overview", "Timeline", "Conversation", "Members", "Swarms", "Directives",
      "Health", "Volume", "Outcomes", "Lessons & policies", "Integrity", "Geography",
      "Charter", "Audit log", "Retention", "Export",
      "Settings", "MCP access", "LRS endpoints", "Datasets",
    ]);
    expect(within(nav()).getByRole("link", { name: "MCP access", hidden: true }).getAttribute("href")).toBe("/access");
  });

  it("polls without marking admin activity", async () => {
    mockStatus();
    renderAt("/activity");
    await waitFor(() => expect(api).toHaveBeenCalledWith("/admin/nav-status",
      expect.objectContaining({ activity: false })));
  });

  it("collapses Govern and Configure on a set-up fleet, with badge totals and screen-reader text", async () => {
    mockStatus({ charter_overdue: 3, lrs_failing: 2 });
    renderAt("/activity");
    await waitFor(() => expect(within(header("Govern")).getByText("3 members overdue on the charter")).toBeDefined());
    const govern = header("Govern");
    expect(govern.getAttribute("aria-expanded")).toBe("false");
    expect(document.getElementById(govern.getAttribute("aria-controls")).hidden).toBe(true);
    expect(within(govern).getByText("3")).toBeDefined();
    const configure = header("Configure");
    expect(within(configure).getByText("!")).toBeDefined();
    expect(within(configure).getByText("2 LRS endpoints failing or paused")).toBeDefined();
    expect(screen.queryByText("Get started")).toBeNull();

    fireEvent.click(govern);
    expect(govern.getAttribute("aria-expanded")).toBe("true");
    expect(within(govern).queryByText("3")).toBeNull();
    // The item carries the badge once the group is open.
    expect(within(nav()).getByRole("link", { name: /Charter/ }).textContent).toContain("3");
    expect(JSON.parse(window.localStorage.getItem("ltms.nav.groups"))).toEqual({ govern: true });
  });

  it("opens the group holding the current page", async () => {
    mockStatus();
    renderAt("/lrs");
    await waitFor(() => expect(api).toHaveBeenCalled());
    expect(header("Configure").getAttribute("aria-expanded")).toBe("true");
    expect(header("Govern").getAttribute("aria-expanded")).toBe("false");
  });

  it("restores a remembered choice", async () => {
    window.localStorage.setItem("ltms.nav.groups", JSON.stringify({ govern: true }));
    mockStatus();
    renderAt("/activity");
    await waitFor(() => expect(api).toHaveBeenCalled());
    expect(header("Govern").getAttribute("aria-expanded")).toBe("true");
  });

  it("works when localStorage throws", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    mockStatus();
    renderAt("/activity");
    await waitFor(() => expect(api).toHaveBeenCalled());
    fireEvent.click(header("Govern"));
    expect(header("Govern").getAttribute("aria-expanded")).toBe("true");
  });

  it("shows Get started on an empty fleet with Govern and Configure open, and it can be dismissed", async () => {
    mockStatus({ setup: EMPTY });
    renderAt("/activity");
    await screen.findByText("Get started");
    expect(header("Govern").getAttribute("aria-expanded")).toBe("true");
    expect(header("Configure").getAttribute("aria-expanded")).toBe("true");
    const list = screen.getByRole("region", { name: /Get started/ });
    expect(within(list).getByRole("link", { name: /Publish the charter/ }).getAttribute("href")).toBe("/charter");
    expect(within(list).getByText("0 of 5 done")).toBeDefined();
    fireEvent.click(within(list).getByRole("button", { name: "Dismiss checklist" }));
    expect(screen.queryByText("Get started")).toBeNull();
    expect(window.localStorage.getItem("ltms.nav.getStartedDismissed")).toBe("1");
  });

  it("ticks checklist items from server state", async () => {
    mockStatus({ setup: { ...EMPTY, charter_published: true } });
    renderAt("/activity");
    const list = await screen.findByRole("region", { name: /Get started/ });
    expect(within(list).getByRole("link", { name: /Publish the charter: done/ })).toBeDefined();
    expect(within(list).getByRole("link", { name: /Create a swarm: not done/ })).toBeDefined();
  });

  it("remembers when Directives was last looked at", async () => {
    mockStatus({ directive_declines: 4 });
    renderAt("/directives");
    await waitFor(() => expect(window.localStorage.getItem("ltms.nav.directivesSeen"))
      .toBe("2026-11-14T12:00:00+00:00"));
    expect(screen.queryByText(/directive declines since you last looked/)).toBeNull();
  });
});
