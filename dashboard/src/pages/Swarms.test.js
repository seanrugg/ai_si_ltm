import { describe, expect, it, vi, afterEach } from "vitest";
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { cleanup } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import Swarms, { KeyOnceDialog } from "./Swarms.jsx";

const swarms = [{
  id: "a".repeat(64), name: "echo-base", status: "active", home: true,
  members: [{ name: "jetson-hub", role: "member" }, { name: "vector", role: "observer" }],
  metrics: { hubs: 0, members: 1, observers: 1, records_24h: 12, records_7d: 80, last_record_at: null, hub_last_seen_at: null },
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

  it("lists one row per swarm with its metrics and the three links (#331)", () => {
    const html = page();
    expect(html).toContain("<caption");
    expect(html).toContain(">Swarms</caption>");
    expect(html).toContain('href="/swarms/echo-base/profile?tab=manage"');
    expect(html).toContain('href="/?swarm=echo-base"');
    expect(html).toContain('href="/volume?swarm=echo-base"');
    expect(html).toContain("12 / 80");
    expect(html).toContain("1 member · 1 observer");
    // Management moved to the Manage tab: no wipe or root-key fields on the list.
    expect(html).not.toContain("Wipe swarm memory");
    expect(html).not.toContain('name="swarm-root-key"');
  });

  it("sorts revoked swarms last, then by name or last activity", async () => {
    const { sortSwarms } = await import("./Swarms.jsx");
    const rows = [
      { name: "b", status: "active", metrics: { last_record_at: "2026-11-12T01:00:00Z" } },
      { name: "a", status: "revoked", metrics: { last_record_at: "2026-11-13T01:00:00Z" } },
      { name: "c", status: "active", metrics: { last_record_at: "2026-11-13T00:00:00Z" } },
    ];
    expect(sortSwarms(rows).map((r) => r.name)).toEqual(["b", "c", "a"]);
    expect(sortSwarms(rows, "activity").map((r) => r.name)).toEqual(["c", "b", "a"]);
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
  it.each(["../lib/swarmKey.js", "./Swarms.jsx", "./profile/Manage.jsx", "../components/GuidedSetup.jsx"])("%s uses no web storage or console", (file) => {
    const src = readFileSync(new URL(file, import.meta.url), "utf8");
    expect(src).not.toMatch(/localStorage|sessionStorage|indexedDB|console\./);
  });
});

