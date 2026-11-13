// Swarm profile Manage tab (#331): the controls moved off the Swarms list.
import { describe, expect, it, vi, afterEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import Manage from "./Manage.jsx";
import { api } from "../../api/client.js";

const swarms = [{
  id: "a".repeat(64), name: "echo-base", status: "active", home: true,
  members: [{ name: "jetson-hub", role: "member" }, { name: "vector", role: "observer" }],
}];
const members = [
  { name: "jetson-hub", status: "active", public_key: "b".repeat(64) },
  { name: "vector", status: "active", public_key: "c".repeat(64) },
];

vi.mock("../../api/useApi.js", () => ({
  useApi: vi.fn(() => ({ data: { members: [], swarms: {} }, error: null, loading: false, reload: () => {} })),
}));
vi.mock("../../api/client.js", () => ({ api: vi.fn() }));

afterEach(() => cleanup());

const el = () => React.createElement(MemoryRouter, null,
  React.createElement(Manage, { name: "echo-base", swarms, members, onReload: () => {} }));
function page() {
  return renderToStaticMarkup(el());
}

describe("Manage tab", () => {
  it("offers only swarm members (not observers) as the hub, with a password-type key field", () => {
    const html = page();
    expect(html).toContain('<option value="jetson-hub">jetson-hub</option>');
    expect(html).not.toContain('<option value="vector">');
    const input = html.match(/<input[^>]*name="swarm-root-key"[^>]*>/)[0];
    expect(input).toContain('type="password"');
    expect(input).toContain('autoComplete="new-password"');
    expect(html).toContain('aria-label="If the key is pasted wrong"'); // #284
  });

  it("groups revoke, wipe and remove under a danger zone, and points membership to the Members tab", () => {
    const html = page();
    expect(html).toContain("Danger zone");
    expect(html).toContain('href="/swarms/echo-base/profile?tab=members"');
  });
});

describe("wipe swarm memory", () => {
  it("shows the wipe panel with the active-member count", () => {
    render(el());
    expect(screen.getByRole("button", { name: /^Wipe swarm memory$/ })).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: /^Wipe swarm memory$/ }));
    expect(screen.getByText(/Clean slate\. This can't be undone\./)).toBeDefined();
    expect(screen.getByText(/Wipes memory for 2 active members/)).toBeDefined();
    expect(screen.getAllByLabelText(/About wiping swarm memory/)).not.toHaveLength(0);
    expect(screen.getAllByLabelText(/Wipe vs delete/)).not.toHaveLength(0);
  });

  it("enables the wipe button only when the confirm input matches the swarm name", () => {
    render(el());
    fireEvent.click(screen.getByRole("button", { name: /^Wipe swarm memory$/ }));
    const wipeButton = screen.getByRole("button", { name: /Wipe 2 active members/ });
    expect(wipeButton.disabled).to.equal(true);
    fireEvent.change(screen.getByLabelText(/Confirm by typing echo-base/), { target: { value: "echo-base" } });
    expect(wipeButton.disabled).to.equal(false);
  });

  it("wipes with the typed name, says so in text, and asks for the name again next time", async () => {
    vi.mocked(api).mockResolvedValue({ swarm: "echo-base", wiped: [{ name: "jetson-hub" }, { name: "vector" }] });
    render(el());
    fireEvent.click(screen.getByRole("button", { name: /^Wipe swarm memory$/ }));
    fireEvent.change(screen.getByLabelText(/Confirm by typing echo-base/), { target: { value: "echo-base" } });
    fireEvent.click(screen.getByRole("button", { name: /Wipe 2 active members/ }));
    expect(await screen.findByText("Memory wiped for 2 members of echo-base.")).toBeDefined();
    expect(api).toHaveBeenCalledWith("/admin/swarms/echo-base/wipe", { method: "POST", body: { confirm: "echo-base" } });

    fireEvent.click(screen.getByRole("button", { name: /^Wipe swarm memory$/ }));
    expect(screen.getByLabelText(/Confirm by typing echo-base/).value).toBe("");
    expect(screen.getByRole("button", { name: /Wipe 2 active members/ }).disabled).toBe(true);
  });
});

describe("swarm home location (#285)", () => {
  it("suggests a city from a member's network estimate, never a declared one", async () => {
    const { networkSuggestion } = await import("./Manage.jsx");
    const swarm = { name: "echo-base", members: [{ name: "a" }, { name: "b" }] };
    const locations = { members: [
      { name: "a", all: [{ source: "declared", label: "Lab", inherited_from: "swarm echo-base" }] },
      { name: "b", all: [{ source: "network", label: "Stafford, VA", lat: 38.4, lon: -77.4 }] },
      { name: "z", all: [{ source: "network", label: "Elsewhere" }] },
    ] };
    expect(networkSuggestion(swarm, locations)).toEqual({ label: "Stafford, VA", lat: 38.4, lon: -77.4 });
    expect(networkSuggestion({ name: "x", members: [] }, locations)).toBeNull();
  });

  it("prompts for a home location when the swarm has none", () => {
    const html = page();
    expect(html).toContain("Set a home location: every member without its own location will use it.");
  });
});
