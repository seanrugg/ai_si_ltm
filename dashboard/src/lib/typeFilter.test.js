import { describe, expect, it } from "vitest";
import { ALL_TYPES, DEFAULT_TYPES, GROUPS, LABELS, parseTypes, serializeTypes, summary } from "./typeFilter.js";

describe("parseTypes", () => {
  it("treats an empty or missing param as the default: everything except sensed", () => {
    for (const param of ["", undefined, null, " , "]) {
      const set = parseTypes(param);
      expect(set.size).toBe(DEFAULT_TYPES.length);
      expect(set.has("sensed")).toBe(false);
      expect(set.has("perception")).toBe(true);
      expect(ALL_TYPES.filter((t) => t !== "sensed").every((t) => set.has(t))).toBe(true);
    }
  });

  it("parses a comma list, ignoring blanks and unknown names", () => {
    expect([...parseTypes("sensed, decision")].sort()).toEqual(["decision", "sensed"]);
    expect([...parseTypes("decision,bogus")].sort()).toEqual(["decision"]);
  });
});

describe("serializeTypes", () => {
  it("serializes the default (everything except sensed) to the empty string", () => {
    expect(serializeTypes(new Set(DEFAULT_TYPES))).toBe("");
    expect(serializeTypes(parseTypes(""))).toBe("");
  });

  it("serializes a set as the comma list in group order", () => {
    expect(serializeTypes(new Set(["decision", "sensed"]))).toBe("sensed,decision");
    expect(serializeTypes(new Set(ALL_TYPES))).toBe(ALL_TYPES.join(","));
  });

  it("round-trips with parseTypes", () => {
    for (const value of ["sensed", "decision", "sensed,decision", "thought,lesson,policy", ALL_TYPES.join(",")]) {
      expect(serializeTypes(parseTypes(value))).toBe(value);
    }
  });
});

describe("the empty selection (None)", () => {
  it("serializes to \"none\" and parses back to the empty set", () => {
    expect(serializeTypes(new Set())).toBe("none");
    expect(parseTypes("none").size).toBe(0);
    expect(serializeTypes(parseTypes("none"))).toBe("none");
  });

  it("keeps \"\" and a missing param meaning the default", () => {
    expect(parseTypes("").size).toBe(DEFAULT_TYPES.length);
    expect(parseTypes(undefined).size).toBe(DEFAULT_TYPES.length);
  });
});

describe("summary", () => {
  it("names the empty, default and full selections", () => {
    expect(summary(new Set())).toBe("No types");
    expect(summary(new Set(DEFAULT_TYPES))).toBe("All except sensor data");
    expect(summary(new Set(ALL_TYPES))).toBe("All types");
  });

  it("shows the single label, or a count for larger selections", () => {
    expect(summary(new Set(["decision"]))).toBe("decisions");
    expect(summary(new Set(["sensed"]))).toBe("sensor data");
    expect(summary(new Set(["sensed", "decision", "lesson"]))).toBe("3 types");
  });
});

describe("GROUPS and LABELS", () => {
  it("cover every filter type exactly once", () => {
    const types = GROUPS.flatMap((g) => g.types);
    expect(new Set(types).size).toBe(types.length);
    expect(Object.keys(LABELS).sort()).toEqual([...ALL_TYPES].sort());
  });
});
