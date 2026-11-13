import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import TypePicker from "./TypePicker.jsx";
import { ALL_TYPES, DEFAULT_TYPES } from "../lib/typeFilter.js";

afterEach(() => cleanup());

const ALL_SERIALIZED = ALL_TYPES.join(",");
const DEFAULT_SERIALIZED = DEFAULT_TYPES.join(",");

// The picker is controlled: the parent (Timeline) puts the serialized value
// back through the URL, so after each action we re-render with it.
function renderPicker(value = "", onChange = vi.fn()) {
  render(React.createElement(TypePicker, { value, onChange }));
  return onChange;
}

const button = (name) => screen.getByRole("button", { name });
const checkbox = (name) => screen.getByRole("checkbox", { name });

describe("TypePicker", () => {
  it("shows the default summary and its aria state on the button", () => {
    renderPicker();
    const open = button("All except sensor data");
    expect(open.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("group", { name: "Record types" })).toBeNull();
  });

  it("opens a panel with one fieldset per group", () => {
    renderPicker();
    const open = button("All except sensor data");
    fireEvent.click(open);
    const panel = screen.getByRole("group", { name: "Record types" });
    expect(open.getAttribute("aria-expanded")).toBe("true");
    expect(open.getAttribute("aria-controls")).toBe(panel.id);
    // Each group is a fieldset (implicit role "group") named by its legend.
    for (const name of ["Sensing", "Deciding & acting", "Communicating", "Learning & rules"]) {
      expect(screen.getByRole("group", { name })).toBeDefined();
    }
    expect(checkbox("sensor data").checked).toBe(false);
    expect(checkbox("perceptions").checked).toBe(true);
  });

  it("toggles a type and applies the selection at once", () => {
    const onChange = renderPicker("", vi.fn());
    fireEvent.click(button("All except sensor data"));
    fireEvent.click(checkbox("sensor data"));
    // Self-reports (heartbeat, health) stay hidden until chosen on their own (#348).
    expect(onChange).toHaveBeenLastCalledWith(ALL_TYPES.filter((t) => t !== "self_report").join(","));
  });

  it("clears a fully selected group from its checkbox", () => {
    const onChange = renderPicker("sensed,perception,self_report,thought", vi.fn());
    fireEvent.click(button("4 types"));
    fireEvent.click(checkbox("Sensing"));
    expect(onChange).toHaveBeenLastCalledWith("thought");
  });

  it("selects an unselected group from its checkbox", () => {
    const onChange = renderPicker("thought", vi.fn());
    fireEvent.click(button("thoughts"));
    fireEvent.click(checkbox("Sensing"));
    expect(onChange).toHaveBeenLastCalledWith("sensed,perception,self_report,thought");
  });

  it("shows a mixed group checkbox when partly selected", () => {
    renderPicker("sensed", vi.fn());
    fireEvent.click(button("sensor data"));
    const group = checkbox("Sensing");
    expect(group.getAttribute("aria-checked")).toBe("mixed");
    expect(group.indeterminate).toBe(true);
  });

  it("All, None and Default apply their selections", () => {
    let onChange = renderPicker("decision", vi.fn());
    fireEvent.click(button("decisions"));
    fireEvent.click(button("All"));
    expect(onChange).toHaveBeenLastCalledWith(ALL_SERIALIZED);

    cleanup();
    onChange = renderPicker(ALL_SERIALIZED, vi.fn());
    fireEvent.click(button("All types"));
    fireEvent.click(button("None"));
    expect(onChange).toHaveBeenLastCalledWith("none"); // not "", which means the default (#205)

    cleanup();
    onChange = renderPicker(DEFAULT_SERIALIZED, vi.fn());
    fireEvent.click(button("All except sensor data"));
    fireEvent.click(button("Default"));
    expect(onChange).toHaveBeenLastCalledWith("");
  });

  it("keeps other types when a group is cleared", () => {
    const onChange = renderPicker(DEFAULT_SERIALIZED, vi.fn());
    fireEvent.click(button("All except sensor data"));
    fireEvent.click(checkbox("Communicating"));
    expect(onChange).toHaveBeenLastCalledWith("perception,thought,decision,action,event,lesson,policy,system");
  });

  it("closes on Escape and returns focus to the button", () => {
    renderPicker();
    const open = button("All except sensor data");
    fireEvent.click(open);
    document.body.focus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("group", { name: "Record types" })).toBeNull();
    expect(document.activeElement).toBe(open);
  });

  it("closes on a click outside", () => {
    renderPicker();
    fireEvent.click(button("All except sensor data"));
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("group", { name: "Record types" })).toBeNull();
  });
});
