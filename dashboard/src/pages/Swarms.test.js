import { describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import Swarms, { KeyOnceDialog } from "./Swarms.jsx";

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

function page() {
  return renderToStaticMarkup(React.createElement(MemoryRouter, null, React.createElement(Swarms)));
}

describe("Swarms page", () => {
  it("defaults to generating the root key in the browser", () => {
    const html = page();
    expect(html).toContain("Generate root key");
    expect(html).toContain("Already have a key?");
  });

  it("offers only swarm members (not observers) as the hub, with a password-type key field", () => {
    const html = page();
    expect(html).toContain('<option value="jetson-hub">jetson-hub</option>');
    expect(html).not.toContain('<option value="vector">');
    const input = html.match(/<input[^>]*name="swarm-root-key"[^>]*>/)[0];
    expect(input).toContain('type="password"');
    expect(input).toContain('autoComplete="new-password"');
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
