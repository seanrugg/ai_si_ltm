import { describe, expect, it } from "vitest";
import {
  isSurge,
  stack,
  trend,
  typeColor,
  TYPE_COLORS,
  TYPE_GROUPS,
  TYPE_ORDER,
} from "./activity.js";

describe("activity", () => {
  describe("TYPE_ORDER / TYPE_COLORS", () => {
    it("covers every type exactly once in group order", () => {
      const flat = TYPE_GROUPS.flatMap((g) => g.types);
      expect(TYPE_ORDER).toEqual(flat);
      expect(new Set(TYPE_ORDER).size).toBe(TYPE_ORDER.length);
    });
    it("has a color for every type in TYPE_ORDER", () => {
      for (const type of TYPE_ORDER) {
        expect(TYPE_COLORS[type]).toBeDefined();
      }
    });
  });

  describe("typeColor", () => {
    it("returns the Okabe-Ito color for a known type", () => {
      expect(typeColor("perception")).toBe("#56B4E9");
      expect(typeColor("action")).toBe("#E69F00");
      expect(typeColor("lesson")).toBe("#0072B2");
    });
    it("falls back to the neutral color for an unknown type", () => {
      expect(typeColor("nope")).toBe("#93A0B8");
      expect(typeColor(null)).toBe("#93A0B8");
      expect(typeColor(undefined)).toBe("#93A0B8");
    });
  });

  describe("trend", () => {
    it("reports growth", () => {
      expect(trend(140, 100)).toEqual({
        pct: 40,
        direction: "up",
        label: "up 40% vs previous window",
      });
    });
    it("reports decline", () => {
      expect(trend(80, 100)).toEqual({
        pct: -20,
        direction: "down",
        label: "down 20% vs previous window",
      });
    });
    it("treats small changes as flat", () => {
      expect(trend(105, 100)).toEqual({
        pct: 5,
        direction: "flat",
        label: "about the same",
      });
      expect(trend(95, 100)).toEqual({
        pct: -5,
        direction: "flat",
        label: "about the same",
      });
    });
    it("reports new activity when previous is zero", () => {
      expect(trend(5, 0)).toEqual({
        pct: null,
        direction: "new",
        label: "new activity",
      });
    });
    it("stays flat when both are zero", () => {
      expect(trend(0, 0)).toEqual({
        pct: null,
        direction: "flat",
        label: "about the same",
      });
    });
  });

  describe("isSurge", () => {
    it("is true when previous >= 5 and total at least doubles", () => {
      expect(isSurge(12, 6)).toBe(true);
      expect(isSurge(10, 5)).toBe(true);
      expect(isSurge(20, 5)).toBe(true);
    });
    it("is false below the previous threshold", () => {
      expect(isSurge(10, 4)).toBe(false);
      expect(isSurge(100, 1)).toBe(false);
    });
    it("is false when not doubled", () => {
      expect(isSurge(9, 5)).toBe(false);
    });
    it("is false when previous is zero", () => {
      expect(isSurge(5, 0)).toBe(false);
    });
  });

  describe("stack", () => {
    it("orders by TYPE_GROUP and skips zero counts", () => {
      const byType = { event: 3, action: 1, perception: 0, decision: 2 };
      const result = stack(byType);
      expect(result.map((s) => s.type)).toEqual(["decision", "action", "event"]);
      expect(result.map((s) => s.count)).toEqual([2, 1, 3]);
      expect(result.every((s) => s.count > 0)).toBe(true);
      expect(result[0].color).toBe(typeColor("decision"));
    });
    it("returns an empty array when everything is zero", () => {
      expect(stack({ perception: 0, action: 0 })).toEqual([]);
      expect(stack({})).toEqual([]);
    });
  });
});
