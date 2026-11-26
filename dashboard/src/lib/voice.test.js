import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { VOICES, pick, say } from "./voice.js";
import { THEMES, setTheme } from "./theme.js";
import { EmptyState, Loading } from "../components/ui.jsx";

afterEach(() => {
  cleanup();
  setTheme("classic");
  try { window.localStorage.clear(); } catch { /* ignore */ }
});

describe("say / pick (#509)", () => {
  it("uses the theme's line, else the plain fallback", () => {
    expect(say("villain", "signOut", "Sign out")).toBe("Sign out (mask off)");
    expect(say("classic", "signOut", "Sign out")).toBe("Sign out");
    expect(say("holo", "tagline", null)).toBeNull();
  });
  it("fills slots in both the voice and the fallback", () => {
    expect(say("villain", "signedInAs", "Signed in as {name}", { name: "sean" })).toBe("Behind the mask: sean");
    expect(say("classic", "signedInAs", "Signed in as {name}", { name: "sean" })).toBe("Signed in as sean");
  });
  it("picks within the pool for any seed, and null without one", () => {
    const pool = VOICES.villain.loading;
    expect(pick("villain", "loading", 0)).toBe(pool[0]);
    expect(pick("villain", "loading", 0.999999)).toBe(pool[pool.length - 1]);
    expect(pick("classic", "loading", 0.5)).toBeNull();
  });
  it("every voice belongs to a real theme and has no empty lines", () => {
    const keys = THEMES.map((t) => t.key);
    for (const [theme, voice] of Object.entries(VOICES)) {
      expect(keys).toContain(theme);
      for (const v of Object.values(voice)) {
        for (const line of [].concat(v)) expect(line.trim().length).toBeGreaterThan(0);
      }
    }
  });
  it("action lines still lead with the plain action", () => {
    expect(VOICES.villain.signOut.startsWith("Sign out")).toBe(true);
  });
});

describe("themed Loading and EmptyState (#509)", () => {
  it("adds an aria-hidden flourish in Villain, and keeps the status text plain", () => {
    setTheme("villain");
    render(React.createElement(Loading, { what: "Loading members" }));
    const status = screen.getByRole("status");
    expect(status.textContent).toMatch(/^Loading members…/);
    const flourish = status.querySelector("[aria-hidden='true']");
    expect(VOICES.villain.loading).toContain(flourish.textContent);
  });
  it("has no flourish in Classic", () => {
    render(React.createElement(EmptyState, { title: "No members yet" }));
    expect(screen.getByText("No members yet")).toBeTruthy();
    expect(document.querySelector(".voice-line")).toBeNull();
  });
  it("re-renders when the theme changes", () => {
    render(React.createElement(EmptyState, { title: "No members yet" }));
    expect(document.querySelector(".voice-line")).toBeNull();
    act(() => { setTheme("villain"); });
    expect(VOICES.villain.empty).toContain(document.querySelector(".voice-line").textContent);
  });
});
