import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import LocationForm, { accuracyText, locationBody } from "./LocationForm.jsx";
import { api } from "../api/client.js";

vi.mock("../api/client.js", () => ({ api: vi.fn() }));
afterEach(() => cleanup());

describe("LocationForm", () => {
  it("Save sends the trimmed body; Clear sends DELETE", async () => {
    vi.mocked(api).mockReset();
    vi.mocked(api).mockResolvedValue({});
    const onDone = vi.fn();
    render(React.createElement(LocationForm, { subjectType: "member", name: "z3-r0", onDone,
      current: { label: "Lab", room: "B12", lat: null, lon: null } }));
    expect(screen.getByText("Lab, B12")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Label"), { target: { value: "  Workshop " } });
    fireEvent.change(screen.getByLabelText("Room"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Latitude"), { target: { value: "38.5" } });
    fireEvent.change(screen.getByLabelText("Longitude"), { target: { value: "-77.25" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(vi.mocked(api)).toHaveBeenCalledWith("/admin/locations/member/z3-r0",
      { method: "PUT", body: { label: "Workshop", room: null, lat: 38.5, lon: -77.25 } });

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    await waitFor(() => expect(vi.mocked(api)).toHaveBeenLastCalledWith("/admin/locations/member/z3-r0",
      { method: "DELETE", body: undefined }));
  });

  it("shows the server's message on a 422", async () => {
    vi.mocked(api).mockReset();
    vi.mocked(api).mockRejectedValue(new Error("give both lat and lon, or neither"));
    render(React.createElement(LocationForm, { subjectType: "swarm", name: "echo-base", current: null }));
    expect(screen.getByText("not set")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Label"), { target: { value: "Site" } });
    fireEvent.change(screen.getByLabelText("Latitude"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("give both lat and lon, or neither")).toBeDefined();
  });

  it("shows an inherited location as coming from the swarm (#285)", () => {
    render(React.createElement(LocationForm, { subjectType: "member", name: "z3-r0",
      current: null, inherited: { label: "Home", room: null, inherited_from: "swarm echo-base" } }));
    expect(screen.getByText("Home (from swarm echo-base)")).toBeDefined();
  });

  it("pre-fills a suggestion but saves only when the admin presses Save (#285)", async () => {
    vi.mocked(api).mockReset();
    vi.mocked(api).mockResolvedValue({});
    render(React.createElement(LocationForm, { subjectType: "swarm", name: "echo-base", current: null,
      suggestion: { label: "Stafford, VA", lat: 38.4, lon: -77.4 }, prompt: "Set a home location" }));
    expect(screen.getByText("Set a home location")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByLabelText("Label").value).toBe("Stafford, VA");
    expect(screen.getByText(/check it before saving/)).toBeDefined();
    expect(vi.mocked(api)).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(vi.mocked(api)).toHaveBeenCalledWith("/admin/locations/swarm/echo-base",
      { method: "PUT", body: { label: "Stafford, VA", room: null, lat: 38.4, lon: -77.4 } }));
  });

  it("locationBody turns blanks into nulls", () => {
    expect(locationBody({ label: " A ", room: " ", lat: "", lon: "" })).toEqual({ label: "A", room: null, lat: null, lon: null });
  });

  // #399: estimates on the Members row.
  const NET = { source: "network", label: "Example City, Example Country", lat: 1, lon: 2, accuracy_km: 20,
    ip: "203.0.113.7", ip_seen_at: "2026-11-14T10:00:00Z" };

  it("shows a network estimate with its address, the hub, and accuracy on hover", () => {
    const { container } = render(React.createElement(LocationForm, { subjectType: "member", name: "z3-r0",
      current: null, estimate: { ...NET, via_hub: "hub-1" } }));
    expect(screen.getByText("Example City, Example Country").getAttribute("title")).toBe("±20 km");
    const ip = screen.getByText("203.0.113.7");
    expect(ip.className).toContain("hash");
    expect(ip.getAttribute("title")).toMatch(/^last seen /);
    expect(container.textContent).toContain("(network estimate · 203.0.113.7, via hub-1)");
    expect(container.textContent).not.toContain("declared");
    expect(container.textContent).not.toContain("not set");
  });

  it("says when the address is no longer kept, and shows no address for gps", () => {
    const { container, unmount } = render(React.createElement(LocationForm, { subjectType: "member", name: "z3-r0",
      current: null, estimate: { ...NET, ip: undefined, ip_seen_at: undefined } }));
    expect(container.textContent).toContain("(network estimate · address no longer kept)");
    unmount();
    const gps = render(React.createElement(LocationForm, { subjectType: "member", name: "z3-r0", current: null,
      estimate: { source: "gps", label: "GPS fix", lat: 1, lon: 2, accuracy_km: 0.008 } }));
    expect(gps.container.textContent).toContain("GPS fix (gps)");
    expect(screen.getByText("GPS fix").getAttribute("title")).toBe("±8 m");
  });

  it("a declared or inherited location wins over an estimate, with no address", () => {
    const { container, unmount } = render(React.createElement(LocationForm, { subjectType: "member", name: "z3-r0",
      current: { label: "Lab", room: null }, estimate: NET }));
    expect(screen.getByText("Lab")).toBeDefined();
    expect(container.textContent).not.toContain("203.0.113.7");
    unmount();
    const inh = render(React.createElement(LocationForm, { subjectType: "member", name: "z3-r0", current: null,
      inherited: { label: "Home", inherited_from: "swarm echo-base" }, estimate: NET }));
    expect(screen.getByText("Home (from swarm echo-base)")).toBeDefined();
    expect(inh.container.textContent).not.toContain("203.0.113.7");
  });

  it("credits DB-IP only with attribution, and only for a network estimate", () => {
    const geo = { attribution: true, credit_url: "https://db-ip.com" };
    const { unmount } = render(React.createElement(LocationForm, { subjectType: "member", name: "a", current: null,
      estimate: NET, geo }));
    expect(screen.getByText("IP geolocation by DB-IP")).toBeDefined();
    unmount();
    const off = render(React.createElement(LocationForm, { subjectType: "member", name: "a", current: null,
      estimate: NET, geo: { attribution: false } }));
    expect(screen.queryByText("IP geolocation by DB-IP")).toBeNull();
    off.unmount();
    render(React.createElement(LocationForm, { subjectType: "member", name: "a", current: { label: "Lab" },
      estimate: NET, geo }));
    expect(screen.queryByText("IP geolocation by DB-IP")).toBeNull();
  });

  it("with nothing at all says not set and why", () => {
    render(React.createElement(LocationForm, { subjectType: "member", name: "a", current: null,
      unplaced: "It has never connected to FLTMS directly." }));
    expect(screen.getByText("not set")).toBeDefined();
    expect(screen.getByText("It has never connected to FLTMS directly.")).toBeDefined();
  });

  it("accuracyText", () => {
    expect(accuracyText(20)).toBe("±20 km");
    expect(accuracyText(0.25)).toBe("±250 m");
    expect(accuracyText(null)).toBeNull();
  });
});
