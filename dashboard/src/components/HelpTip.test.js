import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import HelpTip from "./HelpTip.jsx";
import * as help from "../lib/help.js";

afterEach(() => cleanup());

function renderTip() {
  render(React.createElement(HelpTip, { id: "t", label: "About pausing" }, "Pausing stops sending."));
  return screen.getByRole("button", { name: "About pausing" });
}

describe("HelpTip", () => {
  it("toggles aria-expanded and shows the note", () => {
    const button = renderTip();
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("note")).toBeNull();
    fireEvent.click(button);
    expect(button.getAttribute("aria-expanded")).toBe("true");
    const note = screen.getByRole("note");
    expect(note.textContent).toBe("Pausing stops sending.");
    expect(button.getAttribute("aria-controls")).toBe(note.id);
    fireEvent.click(button);
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("closes on Escape and returns focus to the button", () => {
    const button = renderTip();
    fireEvent.click(button);
    document.body.focus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("note")).toBeNull();
    expect(document.activeElement).toBe(button);
  });

  it("closes on a click outside", () => {
    const button = renderTip();
    fireEvent.click(button);
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("note")).toBeNull();
  });
});

describe("help text", () => {
  it("every constant is under 250 characters", () => {
    for (const [key, text] of Object.entries(help)) {
      expect(typeof text, key).toBe("string");
      expect(text.length, key).toBeLessThan(250);
    }
  });
});
