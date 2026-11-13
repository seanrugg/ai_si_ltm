import { describe, expect, it, vi, afterEach } from "vitest";
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import Swarms, { KeyOnceDialog } from "./Swarms.jsx";
import { api } from "../api/client.js";

const swarms = [{
  id: "a".repeat(64), name: "echo-base", status: "active", home: true,
  members: [{ name: "jetson-hub", role: "member" }, { name: "vector", role: "observer" }],
}];
const members = { members: [
  { name: "jetson-hub", status: "active", public_key: "b".repeat(64) },
  { name: "vector", status: "active", public_key: "c".repeat(64) },
] };

vi.mock("../api/useApi.js", () => ({
  useApi: vi.fn((path) => ({
    data: path === "/admin/swarms" ? swarms : members, error: null, loading: false, reload: () => {},
  })),
}));
vi.mock("../api/client.js", () => ({ api: vi.fn() }));

afterEach(() => cleanup());

function page() {
  return renderToStaticMarkup(React.createElement(MemoryRouter, null, React.createElement(Swarms)));
}

describe("Swarms page", () => {
  it("defaults to adding a swarm with a browser-generated key, explained by a help tip", () => {
    const html = page();
    expect(html).toMatch(/<button[^>]*>Add swarm<\/button>/);
    expect(html).toContain('aria-label="About adding a swarm"');
    expect(html).toContain("Already have a key?");
  });

  it("offers only swarm members (not observers) as the hub, with a password-type key field", () => {
    const html = page();
    expect(html).toContain('<option value="jetson-hub">jetson-hub</option>');
    expect(html).not.toContain('<option value="vector">');
    const input = html.match(/<input[^>]*name="swarm-root-key"[^>]*>/)[0];
    expect(input).toContain('type="password"');
    expect(input).toContain('autoComplete="new-password"');
    expect(html).toContain('aria-label="If the key is pasted wrong"'); // #284
  });
});

describe("one-time key panel", () => {
  const html = renderToStaticMarkup(React.createElement(KeyOnceDialog, {
    swarmName: "echo-base", swarmId: "a".repeat(64), keyString: "ltms-swarm-key-v1:xyz:00000000", onDone: () => {},
  }));

  it("is a labeled modal dialog", () => {
    expect(html).toContain('role="dialog"');
    expect(html).toContain('aria-modal="true"');
    expect(html).toContain('aria-labelledby="key-once-h"');
    expect(html).toContain("cannot show it again");
  });

  it("explains both keys and warns to copy the key now", () => {
    expect(html).toContain('aria-label="About the private root key"');
    expect(html).toContain('aria-label="About the public root key"');
    expect(html).toContain("a".repeat(64));
    expect(html).toContain("Copy this key now and store it offline");
  });

  it("says to save the whole line, explains its parts, and what losing it means (#284)", () => {
    expect(html).toContain("Save the whole line, including");
    expect(html).toContain("ltms-swarm-key-v1:");
    expect(html).toContain('aria-label="What the parts of the key mean"');
    expect(html).toContain("Losing this key means revoking and re-creating the swarm");
  });

  it("can't be closed until the key is confirmed saved", () => {
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Done<\/button>/);
  });
});

describe("the root key never reaches storage", () => {
  it.each(["../lib/swarmKey.js", "./Swarms.jsx"])("%s uses no web storage or console", (file) => {
    const src = readFileSync(new URL(file, import.meta.url), "utf8");
    expect(src).not.toMatch(/localStorage|sessionStorage|indexedDB|console\./);
  });
});

describe("wipe swarm memory", () => {
  it("shows the wipe panel with the active-member count", () => {
    render(React.createElement(MemoryRouter, null, React.createElement(Swarms)));
    expect(screen.getByRole("button", { name: /^Wipe swarm memory$/ })).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: /^Wipe swarm memory$/ }));
    expect(screen.getByText(/Clean slate\. This can't be undone\./)).toBeDefined();
    expect(screen.getByText(/Wipes memory for 2 active members/)).toBeDefined();
    expect(screen.getAllByLabelText(/About wiping swarm memory/)).not.toHaveLength(0);
    expect(screen.getAllByLabelText(/Wipe vs delete/)).not.toHaveLength(0);
  });

  it("enables the wipe button only when the confirm input matches the swarm name", () => {
    render(React.createElement(MemoryRouter, null, React.createElement(Swarms)));
    fireEvent.click(screen.getByRole("button", { name: /^Wipe swarm memory$/ }));
    const wipeButton = screen.getByRole("button", { name: /Wipe 2 active members/ });
    expect(wipeButton.disabled).to.equal(true);
    fireEvent.change(screen.getByLabelText(/Confirm by typing echo-base/), { target: { value: "echo-base" } });
    expect(wipeButton.disabled).to.equal(false);
  });

  it("wipes with the typed name, says so in text, and asks for the name again next time", async () => {
    vi.mocked(api).mockResolvedValue({ swarm: "echo-base", wiped: [{ name: "jetson-hub" }, { name: "vector" }] });
    render(React.createElement(MemoryRouter, null, React.createElement(Swarms)));
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
    const { networkSuggestion } = await import("./Swarms.jsx");
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
