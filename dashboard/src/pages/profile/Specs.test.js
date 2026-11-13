import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { SpecsRollup, SpecsView } from "./Specs.jsx";
import { patchFor, rollupRow } from "../../lib/specs.js";
import { tabsFor } from "../../lib/profile.js";
import { api } from "../../api/client.js";

vi.mock("../../api/client.js", () => ({ api: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

const AT = "2026-11-13T02:00:00Z";
const SPECS = {
  hardware: { value: { board: "Jetson Orin Nano", ram_mib: 8192, sensors: ["camera"] }, source: "mixed",
    admin_fields: ["ram_mib"], declared: { board: "Jetson Orin Nano", ram_mib: 7619 }, reported_at: AT },
  models: { value: [{ name: "qwen2.5:1.5b", quant: "q4_K_M", runtime: "ollama", where: "onboard" }], source: "declared",
    admin_fields: [], reported_at: AT },
  software: { value: null, source: null, admin_fields: [], reported_at: null },
  notes: { value: null, source: null, admin_fields: [], reported_at: null },
};
const HISTORY = { total: 1, events: [{ ts: AT, actor: "admin:sean", source: "admin", sections: ["hardware"] }] };

describe("Specs tab (#333)", () => {
  it("is a member-only tab after Overview", () => {
    expect(tabsFor("member").map((t) => t.key).slice(0, 2)).toEqual(["overview", "specs"]);
    expect(tabsFor("swarm").map((t) => t.key)).not.toContain("specs");
  });

  it("shows each value's source, models as a table, and Not reported", () => {
    render(React.createElement(SpecsView, { specs: SPECS, history: HISTORY, onSave: () => {} }));
    const ram = screen.getByText("8192").closest("tr");
    expect(ram.textContent).toContain("set by admin");
    expect(screen.getByText("Jetson Orin Nano").closest("tr").textContent).toContain("reported by device");
    expect(screen.getByRole("table", { name: "Language models" })).toBeTruthy();
    expect(screen.getByText("q4_K_M")).toBeTruthy();
    expect(screen.getAllByText(/^Not reported\./).length).toBe(2);
    expect(screen.getByText("History (1)")).toBeTruthy();
  });

  it("edits with PATCH bodies and reverts an override", () => {
    const onSave = vi.fn();
    render(React.createElement(SpecsView, { specs: SPECS, history: HISTORY, onSave }));
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByRole("button", { name: "Revert to reported" }));
    expect(onSave).toHaveBeenLastCalledWith({ hardware: { ram_mib: null } });
    const form = screen.getByRole("form", { name: "Override hardware" });
    fireEvent.change(form.querySelectorAll("input")[0], { target: { value: "Orin NX" } });
    fireEvent.submit(form);
    expect(onSave).toHaveBeenLastCalledWith({ hardware: { board: "Orin NX" } });
    expect(patchFor("hardware", "sensors", "camera, imu")).toEqual({ hardware: { sensors: ["camera", "imu"] } });
    expect(patchFor("hardware", "cores", "6")).toEqual({ hardware: { cores: 6 } });
  });

  it("rolls a swarm up, one line per member", async () => {
    expect(rollupRow("hub", SPECS)).toEqual({ name: "hub", board: "Jetson Orin Nano", ram: "8.0 GiB",
      model: "qwen2.5:1.5b q4_K_M", where: "onboard" });
    api.mockResolvedValue({ specs: SPECS });
    render(React.createElement(SpecsRollup, { swarm: { members: [{ name: "hub", role: "hub" }] },
      members: [{ name: "hub", id: "1" }] }));
    await waitFor(() => expect(screen.getByRole("table", { name: "Specs at a glance" })).toBeTruthy());
    expect(api).toHaveBeenCalledWith("/admin/members/1/specs");
    expect(screen.getByText("8.0 GiB")).toBeTruthy();
  });
});
