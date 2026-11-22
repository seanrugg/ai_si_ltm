import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import System, { gb, uptimeText } from "./System.jsx";

let sys;
vi.mock("../api/useApi.js", () => ({
  useApi: vi.fn(() => ({ data: sys, error: null, loading: false, reload: () => {} })),
}));

const iso = (s) => new Date(Date.now() - s * 1000).toISOString();

beforeEach(() => {
  sys = {
    generated_at: iso(0),
    server: { version: "0.9.0", commit: "abc123def456", started_at: iso(7200), uptime_seconds: 7200,
              database: "postgresql", data_dir: { free_bytes: 12.3e9, total_bytes: 50e9 },
              deployment: { mode: "docker", label: "Docker", settings_file: ".env next to compose.yaml",
                            apply: "docker compose up -d" } },
    database: { kind: "postgresql", managed: true, current: "0041", head: "0042", pending: true,
                warning: "migrations pending: database at 0041, code needs 0042" },
    jobs: { scheduler_running: true, errors: 1, jobs: [
      { name: "lrs_forward", label: "LRS sync and summary", last_run: iso(60), result: "error",
        message: "RuntimeError: POST <url> failed", next_run: iso(-60) },
      { name: "purge_payloads", label: "Data retention: payload purge", last_run: iso(3600), result: "ok",
        message: null, next_run: iso(-3600) },
      { name: "geoip_refresh", label: "City database refresh", last_run: null, result: null, message: null, next_run: iso(-9000) },
    ] },
    keys: { fleet_key: { present: true, fingerprint: "SHA256:AbCdEf", created_at: iso(86400),
                         backup_note: "Back up fleet-key.json together with the data directory." },
            swarms: 3, swarms_with_leader: 2 },
    integrations: {
      lrs: { label: "LRS endpoints", state: "warning", total: 2, enabled: 2, paused: 0, failing: 1 },
      mcp: { label: "MCP tokens", state: "ok", total: 3, active: 2, revoked: 1, expired: 0 },
      reference_sources: { label: "Reference sources", state: "ok", total: 4, imported: 2, pending_versions: 0 },
    },
    diagnostics: "FLTMS system status\nversion: 0.9.0 (abc123def456)\n",
  };
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const page = () => render(React.createElement(MemoryRouter, null, React.createElement(System)));
const section = (name) => screen.getByRole("heading", { name }).closest("section");

describe("System status page (#383)", () => {
  it("shows every section", () => {
    page();
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent))
      .toEqual(["Server", "Database", "Background jobs", "Keys", "Integrations", "Diagnostics"]);
    expect(within(section("Server")).getByText("12.3 GB of 50.0 GB")).toBeTruthy();
    expect(within(section("Server")).getByText("(abc123def456)")).toBeTruthy();
    expect(within(section("Server")).getByText("Docker")).toBeTruthy();
    expect(section("Server").textContent).toContain(".env next to compose.yaml; then docker compose up -d.");
  });

  it("warns when migrations are pending, and not otherwise", () => {
    page();
    expect(within(section("Database")).getByRole("alert").textContent).toMatch(/migrations pending/);
    cleanup();
    sys = { ...sys, database: { ...sys.database, current: "0042", pending: false, warning: null } };
    page();
    expect(within(section("Database")).queryByRole("alert")).toBeNull();
    expect(within(section("Database")).getByText("Up to date")).toBeTruthy();
  });

  it("shows a job's error, an OK run and one not run yet", () => {
    page();
    const row = (name) => within(section("Background jobs")).getByRole("rowheader", { name }).closest("tr");
    expect(row("LRS sync and summary").textContent).toMatch(/Error: RuntimeError: POST <url> failed/);
    expect(within(row("Data retention: payload purge")).getByText("OK")).toBeTruthy();
    expect(within(row("City database refresh")).getByText("Not run yet")).toBeTruthy();
  });

  it("shows the fleet key fingerprint and the backup reminder", () => {
    page();
    const keys = section("Keys");
    expect(within(keys).getByText("SHA256:AbCdEf")).toBeTruthy();
    expect(within(keys).getByText(/fleet-key\.json/)).toBeTruthy();
    expect(within(keys).getByText("2 of 3")).toBeTruthy();
  });

  it("links each integration to its page", () => {
    page();
    const links = within(section("Integrations")).getAllByRole("link").map((a) => [a.textContent, a.getAttribute("href")]);
    expect(links).toEqual([["LRS endpoints", "/lrs"], ["MCP tokens", "/access"], ["Reference sources", "/datasets"]]);
  });

  it("Copy diagnostics copies the server's text", async () => {
    const writeText = vi.fn().mockResolvedValue();
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    page();
    fireEvent.click(screen.getByRole("button", { name: "Copy diagnostics" }));
    expect(writeText).toHaveBeenCalledWith(sys.diagnostics);
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("Copied to the clipboard."));
  });

  it("formats uptime and sizes", () => {
    expect(uptimeText(40)).toBe("40 s");
    expect(uptimeText(3720)).toBe("1 h 2 min");
    expect(uptimeText(3 * 86400 + 4 * 3600)).toBe("3 d 4 h");
    expect(uptimeText(null)).toBe("unknown");
    expect(gb(null)).toBe("unknown");
  });
});
