import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import React from "react";
import { CATEGORIES, KINDS, NEUTRAL, RELAY, ROLES, categoryOf, isSensed, kindOf, roleOf } from "./visual.js";
import { Legend, MemberMark } from "../components/visual.jsx";

afterEach(() => cleanup());

describe("categoryOf", () => {
  const expected = {
    perception: "sense", thought: "act", decision: "act", action: "act", event: "act",
    communication: "comm", lesson: "learn", policy: "learn", system: null,
  };
  it("maps all nine record types, with and without telemetry", () => {
    for (const [type, cat] of Object.entries(expected)) {
      expect(categoryOf(type, [])).toBe(cat);
      expect(categoryOf(type, ["telemetry"])).toBe(cat);
      expect(categoryOf(type)).toBe(cat);
    }
    expect(categoryOf("bogus")).toBeNull();
  });
  it("isSensed: only a perception tagged telemetry", () => {
    expect(isSensed("perception", ["telemetry"])).toBe(true);
    expect(isSensed("perception", [])).toBe(false);
    expect(isSensed("perception", null)).toBe(false);
    expect(isSensed("event", ["telemetry"])).toBe(false);
  });
});

describe("roleOf and kindOf", () => {
  it("roles default to free", () => {
    expect(roleOf({ role: "hub" })).toBe("hub");
    expect(roleOf({ membership: { role: "observer" } })).toBe("observer");
    expect(roleOf({})).toBe("free");
    expect(roleOf(null)).toBe("free");
  });
  it("kinds", () => {
    expect(kindOf({ kind: "robot" })).toBe("physical");
    expect(kindOf({ kind: "robot", cognition: { value: "onboard" } })).toBe("physical");
    expect(kindOf({ kind: "robot", cognition: { value: "offboard", source: "declared" } })).toBe("hybrid");
    expect(kindOf({ kind: "agent" })).toBe("agent");
    expect(kindOf({ kind: "assistant" })).toBe("agent");
    expect(kindOf({ kind: "relay" })).toBe("hub");
    expect(kindOf({ kind: "robot", role: "hub" })).toBe("hub");
    expect(kindOf({ kind: "agent", role: "hub" })).toBe("hub");
  });
});

describe("palette", () => {
  it("every category has a distinct color and a distinct dash", () => {
    expect(new Set(CATEGORIES.map((c) => c.color)).size).toBe(CATEGORIES.length);
    expect(new Set(CATEGORIES.map((c) => c.dash)).size).toBe(CATEGORIES.length);
    expect(CATEGORIES.map((c) => c.key)).toEqual(["sense", "act", "comm", "learn"]);
  });
  it("roles, categories and the neutrals don't share colors", () => {
    const colors = [...Object.values(ROLES).map((r) => r.color), ...CATEGORIES.map((c) => c.color), NEUTRAL, RELAY];
    expect(new Set(colors).size).toBe(colors.length);
  });
});

describe("components", () => {
  it("MemberMark names role and kind for screen readers", () => {
    render(React.createElement(MemberMark, { member: { kind: "robot", role: "member", cognition: { value: "offboard" } } }));
    expect(screen.getByText("Member · Hybrid")).toBeDefined();
  });
  it("Legend lists every role, kind and category", () => {
    render(React.createElement(Legend));
    const legend = screen.getByRole("group", { name: "Legend" });
    for (const label of [...Object.values(ROLES).map((r) => r.label), ...Object.values(KINDS).map((k) => k.label),
      ...CATEGORIES.map((c) => c.label)]) {
      expect(within(legend).getAllByText(label).length).toBeGreaterThan(0);
    }
  });
});
