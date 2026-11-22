import { describe, expect, it } from "vitest";
import {
  FREE_AGENTS, RECORD_TYPES, dateInDays, emptyTokenForm, previewText, tokenBody, tokenFormProblem, tokenScope,
  withPayloads,
} from "./tokenScope.js";

const form = (patch) => ({ ...emptyTokenForm(), name: "reader", ...patch });

// What the old comma-separated text fields sent (Access.jsx before #446).
function oldTextScope({ types, agents, swarms, payloads }) {
  const split = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);
  const scope = {};
  if (payloads) scope.payloads = true;
  for (const [key, value] of Object.entries({ types, agents, swarms })) {
    if (split(value).length) scope[key] = split(value);
  }
  return scope;
}

describe("token scope from the pickers (#446)", () => {
  it("sends the same scope the text fields did", () => {
    const picked = form({ types: ["thought", "event"], agents: ["z3-r0", "v3-c7r"], swarms: ["echo-base"], payloads: true });
    expect(tokenScope(picked)).toEqual(oldTextScope({
      types: "event, thought", agents: "z3-r0, v3-c7r", swarms: "echo-base", payloads: true }));
    expect(tokenScope(form({ types: ["lesson"] }))).toEqual(oldTextScope({ types: "lesson", agents: "", swarms: "", payloads: false }));
  });

  it("All types sends every type, in order", () => {
    expect(tokenScope(form({ types: [...RECORD_TYPES].reverse() })).types).toEqual(RECORD_TYPES);
  });

  it("Free agents is sent as null", () => {
    expect(tokenScope(form({ types: ["event"], swarms: ["echo-base", FREE_AGENTS] })).swarms).toEqual(["echo-base", null]);
  });

  it("the body keeps its shape", () => {
    expect(tokenBody(form({ name: " r ", types: ["event"], expires: "2026-11-28", owner: " ", purpose: "p" })))
      .toEqual({ name: "r", scope: { types: ["event"] }, expires_at: "2026-11-28", owner: null, purpose: "p" });
  });

  it("can't be sent without a record type (or a name)", () => {
    expect(tokenFormProblem(form({}))).toMatch(/at least one record type/);
    expect(tokenFormProblem(form({ types: ["event"] }))).toBeNull();
    expect(tokenFormProblem(form({ name: " ", types: ["event"] }))).toMatch(/name/);
  });

  it("ticking payloads sets a 7-day expiry unless one is set", () => {
    const now = new Date(2026, 10, 21, 23, 0);
    expect(withPayloads(form({}), true, now).expires).toBe("2026-11-28");
    expect(withPayloads(form({ expires: "2026-12-25" }), true, now).expires).toBe("2026-12-25");
    expect(withPayloads(form({ payloads: true, expires: "2026-11-28" }), false, now).expires).toBe("2026-11-28");
    expect(dateInDays(30, now)).toBe("2026-12-21");
  });
});

describe("preview text (#446)", () => {
  it("says what the scope sent reads", () => {
    expect(previewText({ types: ["perception"], agents: ["v3-c7r"], payloads: true }, "2026-11-28"))
      .toBe("Reads perception records of v3-c7r, with payloads, until 28 Nov 2026.");
    expect(previewText({ types: RECORD_TYPES }, ""))
      .toBe("Reads records of every type of every member, headers only, with no expiry.");
    expect(previewText({ types: ["event", "lesson", "thought"], swarms: ["echo-base", null] }, ""))
      .toBe("Reads event, lesson and thought records of every member in echo-base or free agents, headers only, with no expiry.");
    expect(previewText({ types: [] }, "")).toMatch(/Reads nothing yet/);
  });
});
