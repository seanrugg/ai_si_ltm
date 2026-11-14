import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import React from "react";
import HelpTip, { MARGIN, placeNote } from "./HelpTip.jsx";
import { PageHeader } from "./ui.jsx";
import * as help from "../lib/help.js";

const realWidth = window.innerWidth;
const realHeight = window.innerHeight;

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.innerWidth = realWidth;
  window.innerHeight = realHeight;
});

function renderTip() {
  render(React.createElement(HelpTip, { id: "t", label: "About pausing" }, "Pausing stops sending."));
  return screen.getByRole("button", { name: "About pausing" });
}

function rect({ top, left, width, height }) {
  return { top, left, width, height, right: left + width, bottom: top + height, x: left, y: top, toJSON() {} };
}

// Window of 1000x800; the note measures 320x120. Place the button at `button`.
function openAt(button) {
  window.innerWidth = 1000;
  window.innerHeight = 800;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function () {
    if (this.getAttribute("role") === "note") return rect({ top: 0, left: 0, width: 320, height: 120 });
    if (this.tagName === "BUTTON") return rect(button);
    return rect({ top: 0, left: 0, width: 0, height: 0 });
  });
  const b = renderTip();
  fireEvent.click(b);
  const note = screen.getByRole("note");
  return { b, note, top: parseFloat(note.style.top), left: parseFloat(note.style.left), width: parseFloat(note.style.width) };
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

  it("closes on a pointer press on another element", () => {
    const button = renderTip();
    fireEvent.click(button);
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("stays open when the page scroll bar is pressed (#377)", () => {
    const button = renderTip();
    fireEvent.click(button);
    fireEvent.pointerDown(document.documentElement);
    fireEvent.mouseDown(document.documentElement);
    expect(screen.getByRole("note")).toBeDefined();
  });

  it("stays open, and follows the button, when the page scrolls", () => {
    const button = renderTip();
    fireEvent.click(button);
    fireEvent.scroll(window);
    expect(screen.getByRole("note")).toBeDefined();
  });

  it("closes when focus moves to another control", () => {
    render(React.createElement("div", null,
      React.createElement(HelpTip, { id: "t", label: "About pausing" }, "Note"),
      React.createElement("button", { type: "button" }, "Other")));
    fireEvent.click(screen.getByRole("button", { name: "About pausing" }));
    act(() => screen.getByRole("button", { name: "Other" }).focus());
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("is rendered at the body with fixed positioning, so it never widens the page", () => {
    const button = renderTip();
    fireEvent.click(button);
    const note = screen.getByRole("note");
    expect(note.parentElement).toBe(document.body);
    expect(note.style.position).toBe("fixed");
    expect(note.style.maxHeight).toBe("60vh");
    expect(note.style.overflowY).toBe("auto");
  });

  it("opens right-aligned near the right edge and stays inside the window", () => {
    const { note, left, width } = openAt({ top: 100, left: 970, width: 20, height: 20 });
    expect(note.dataset.placement).toBe("bottom-end");
    expect(left).toBeGreaterThanOrEqual(MARGIN);
    expect(left + width).toBeLessThanOrEqual(1000 - MARGIN);
  });

  it("opens above the button near the bottom of the window", () => {
    const { note, top } = openAt({ top: 760, left: 100, width: 20, height: 20 });
    expect(note.dataset.placement).toBe("top-start");
    expect(top + 120).toBeLessThanOrEqual(760);
    expect(top).toBeGreaterThanOrEqual(MARGIN);
  });

  it("keeps the default placement in the middle of the window", () => {
    const { note, top, left } = openAt({ top: 300, left: 400, width: 20, height: 20 });
    expect(note.dataset.placement).toBe("bottom-start");
    expect(left).toBe(400);
    expect(top).toBe(324);
  });
});

describe("placeNote", () => {
  it("shrinks to the window minus margins on a narrow (zoomed) window", () => {
    const p = placeNote({ top: 10, bottom: 30, left: 200, right: 220 }, { width: 320, height: 100 }, { width: 240, height: 400 });
    expect(p.width).toBe(240 - 2 * MARGIN);
    expect(p.left).toBe(MARGIN);
  });

  it("clamps a tall note to the top margin when there is no room either way", () => {
    const p = placeNote({ top: 100, bottom: 120, left: 10, right: 30 }, { width: 300, height: 1000 }, { width: 800, height: 400 });
    expect(p.top).toBe(MARGIN);
  });
});

describe("PageHeader", () => {
  it("renders the help button inside the heading, next to the title", () => {
    render(React.createElement(PageHeader, {
      title: "Fleet charter",
      help: React.createElement(HelpTip, { id: "h", label: "About the charter" }, "Text"),
    }, React.createElement("button", { type: "button" }, "Action")));
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading.textContent.startsWith("Fleet charter")).toBe(true);
    expect(within(heading).getByRole("button", { name: "About the charter" })).toBeDefined();
    expect(within(heading).queryByRole("button", { name: "Action" })).toBeNull();
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
