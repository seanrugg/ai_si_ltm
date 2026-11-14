import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { AdminSessions, formatValue, groupSettings, validateSessionForm } from "./Settings.jsx";
import { api } from "../api/client.js";
import { useApi } from "../api/useApi.js";

vi.mock("../api/client.js", () => ({ api: vi.fn() }));
vi.mock("../api/useApi.js", () => ({ useApi: vi.fn() }));
afterEach(() => cleanup());

describe("settings page helpers", () => {
  it("formats values with units", () => {
    expect(formatValue("token_ttl_seconds", 300)).toBe("5 min");
    expect(formatValue("admin_session_ttl_seconds", 1800)).toBe("30 min");
    expect(formatValue("retention_audit_days", 90)).toBe("90 days");
    expect(formatValue("retention_payload_rules", [{ type: "event", days: 90 }, { tag: "telemetry", days: 30 }]))
      .toBe("event: 90 days · any type tagged telemetry: 30 days");
    expect(formatValue("retention_payload_rules", [])).toBe("Never");
    expect(formatValue("ingest_rate", 10)).toBe("10 per second");
    expect(formatValue("enroll_rate_per_ip", 5)).toBe("5 per hour");
    expect(formatValue("cookie_secure", true)).toBe("On");
    expect(formatValue("dashboard_dir", "")).toBe("Not set");
  });
  it("puts every key in exactly one group, unknown keys in Other", () => {
    const settings = { db_host: "x", token_ttl_seconds: 1, max_batch: 1, retention_audit_days: 1, brand_new: 1 };
    const groups = groupSettings(settings);
    const all = groups.flatMap((g) => g.keys);
    expect(all.sort()).toEqual(Object.keys(settings).sort());
    expect(groups.find((g) => g.title === "Other").keys).toEqual(["brand_new"]);
  });
});

describe("admin sessions settings (#378)", () => {
  const LIMITS = { idle_minutes_min: 15, idle_minutes_max: 480, max_hours_min: 1, max_hours_max: 24,
                   warn_minutes_min: 1, warn_minutes_max: 10 };
  const ok = { idle_minutes: "60", max_hours: "12", warn_minutes: "2" };

  it("validates ranges and maximum >= idle", () => {
    expect(validateSessionForm(ok, LIMITS)).toBeNull();
    expect(validateSessionForm({ ...ok, idle_minutes: "14" }, LIMITS)).toMatch(/15–480/);
    expect(validateSessionForm({ ...ok, idle_minutes: "481" }, LIMITS)).toMatch(/15–480/);
    expect(validateSessionForm({ ...ok, max_hours: "25" }, LIMITS)).toMatch(/1–24/);
    expect(validateSessionForm({ ...ok, idle_minutes: "180", max_hours: "2" }, LIMITS)).toMatch(/at least/);
    expect(validateSessionForm({ ...ok, warn_minutes: "11" }, LIMITS)).toMatch(/1–10/);
    expect(validateSessionForm({ ...ok, idle_minutes: "1.5" }, LIMITS)).toMatch(/whole/);
    expect(validateSessionForm({ ...ok, idle_minutes: "45" }, { ...LIMITS, idle_minutes_max: 30 })).toMatch(/15–30/);
  });

  it("saves the form", async () => {
    const view = { idle_minutes: 60, max_hours: 12, warn_minutes: 2, source: "environment", limits: LIMITS };
    useApi.mockReturnValue({ data: view, error: null, loading: false, reload: () => {} });
    api.mockResolvedValue({ ...view, idle_minutes: 90, source: "dashboard" });
    render(React.createElement(AdminSessions));
    fireEvent.change(screen.getByLabelText(/Sign out after inactivity/), { target: { value: "90" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toMatch(/Saved/));
    expect(api).toHaveBeenCalledWith("/admin/sessions/settings", { method: "PUT",
      body: { idle_minutes: 90, max_hours: 12, warn_minutes: 2 } });
  });

  it("shows a validation error without saving", () => {
    const view = { idle_minutes: 60, max_hours: 12, warn_minutes: 2, source: "environment", limits: LIMITS };
    useApi.mockReturnValue({ data: view, error: null, loading: false, reload: () => {} });
    api.mockReset();
    render(React.createElement(AdminSessions));
    fireEvent.change(screen.getByLabelText(/Maximum session length/), { target: { value: "0" } });
    fireEvent.submit(screen.getByRole("form", { name: "Admin sessions" }));
    expect(screen.getByRole("alert").textContent).toMatch(/1–24/);
    expect(api).not.toHaveBeenCalled();
  });
});
