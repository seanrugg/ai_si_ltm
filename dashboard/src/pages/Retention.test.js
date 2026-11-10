import { describe, expect, it } from "vitest";
import { couldExpireMore, toBody, validateRules } from "./Retention.jsx";

describe("validateRules", () => {
  it("accepts a type, a tag, or both", () => {
    expect(validateRules([{ type: "event", tag: "", days: 30 }, { type: "", tag: "telemetry", days: 7 },
      { type: "event", tag: "telemetry", days: 1 }])).toEqual([]);
  });

  it("needs a type or a tag", () => {
    expect(validateRules([{ type: "", tag: "  ", days: 30 }])).toEqual(["Rule 1 needs a type, a tag, or both."]);
  });

  it("needs whole days of at least 1", () => {
    expect(validateRules([{ type: "event", tag: "", days: 0 }])[0]).toMatch(/Rule 1: days/);
    expect(validateRules([{ type: "event", tag: "", days: "1.5" }])[0]).toMatch(/days/);
    expect(validateRules([{ type: "event", tag: "", days: "" }])[0]).toMatch(/days/);
  });

  it("refuses the keep tag", () => {
    expect(validateRules([{ type: "", tag: "keep", days: 30 }])[0]).toMatch(/keep/);
  });
});

describe("couldExpireMore", () => {
  const before = { payload_rules: [{ type: "event", tag: null, days: 90 }], audit_days: 365 };

  it("a new rule or shorter days is true", () => {
    expect(couldExpireMore(before, { ...before, payload_rules: [...before.payload_rules, { type: null, tag: "telemetry", days: 7 }] })).toBe(true);
    expect(couldExpireMore(before, { ...before, payload_rules: [{ type: "event", tag: null, days: 30 }] })).toBe(true);
  });

  it("removing a rule, longer days or keep-forever is false", () => {
    expect(couldExpireMore(before, { ...before, payload_rules: [] })).toBe(false);
    expect(couldExpireMore(before, { ...before, payload_rules: [{ type: "event", tag: null, days: 120 }] })).toBe(false);
    expect(couldExpireMore(before, { payload_rules: [], audit_days: null })).toBe(false);
  });

  it("a shorter or newly set audit limit is true", () => {
    expect(couldExpireMore(before, { ...before, audit_days: 90 })).toBe(true);
    expect(couldExpireMore({ ...before, audit_days: null }, { ...before, audit_days: 3650 })).toBe(true);
  });
});

describe("toBody", () => {
  it("sends [] for keep-all and null for audit keep-forever; blanks become null", () => {
    expect(toBody({ keepAll: true, rules: [{ type: "event", tag: "", days: 3 }], auditForever: true, auditDays: 5 }))
      .toEqual({ payload_rules: [], audit_days: null });
    expect(toBody({ keepAll: false, rules: [{ type: "", tag: " telemetry ", days: "7" }], auditForever: false, auditDays: "90" }))
      .toEqual({ payload_rules: [{ type: null, tag: "telemetry", days: 7 }], audit_days: 90 });
  });
});
