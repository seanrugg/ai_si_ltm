import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import LocationForm, { locationBody } from "./LocationForm.jsx";
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
});
