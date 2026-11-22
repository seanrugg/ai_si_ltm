import { describe, expect, it } from "vitest";
import { originCrumbs, originOf, recordCrumbs, safeOrigin, timelinePath, withOrigin } from "./origin.js";

const FILTERED = "/timeline?agent=v3-c7r&type=event%2Caction&outcome=success&q=cliff&swarm=echo-base";

describe("origin (#393)", () => {
  it("a list page passes itself on, with its filters", () => {
    expect(originOf("/timeline", "?agent=v3-c7r&q=cliff")).toBe("/timeline?agent=v3-c7r&q=cliff");
    expect(withOrigin("/decision-trace/v3-c7r/5", FILTERED))
      .toBe(`/decision-trace/v3-c7r/5?from=${encodeURIComponent(FILTERED)}`);
  });

  it("a record or trace page passes on the origin it was given, not itself", () => {
    const search = `?from=${encodeURIComponent(FILTERED)}`;
    expect(originOf("/record/v3-c7r/5", search)).toBe(FILTERED);
    expect(originOf("/decision-trace/v3-c7r/5", search)).toBe(FILTERED);
    expect(originOf("/record/v3-c7r/5", "")).toBeNull();
  });

  it("only in-app paths are trusted", () => {
    for (const bad of ["//evil.example/x", "https://evil.example", "javascript:alert(1)", "/\\evil", "/record/a/1",
      "/x\ny", "/", null, undefined, 5]) {
      expect(safeOrigin(bad)).toBeNull();
    }
    expect(withOrigin("/record/a/1", null)).toBe("/record/a/1");
  });

  it("a filtered timeline: one crumb back to the same filters", () => {
    expect(originCrumbs(FILTERED, "v3-c7r")).toEqual([{ label: "Timeline (v3-c7r)", to: FILTERED }]);
    // Old home-page timeline links (/?agent=…) go to /timeline with the same query.
    expect(originCrumbs("/?agent=v3-c7r&type=event", "v3-c7r"))
      .toEqual([{ label: "Timeline (v3-c7r)", to: "/timeline?agent=v3-c7r&type=event" }]);
  });

  it("a member profile: Members / name / tab, back to the same tab and range", () => {
    expect(originCrumbs("/members/v3-c7r/profile?tab=chronology&range=24h", "v3-c7r")).toEqual([
      { label: "Members", to: "/members" },
      { label: "v3-c7r", to: "/members/v3-c7r/profile" },
      { label: "Chronology", to: "/members/v3-c7r/profile?tab=chronology&range=24h" },
    ]);
    expect(originCrumbs("/members/v3-c7r/profile?tab=activity", "v3-c7r")[2].label).toBe("Chronology"); // old key
    expect(originCrumbs("/swarms/echo-base/profile?tab=manage", "v3-c7r").map((c) => c.label))
      .toEqual(["Swarms", "echo-base", "Manage"]);
  });

  it("other pages use their nav label", () => {
    expect(originCrumbs("/conversation?swarm=echo-base", "a")).toEqual([{ label: "Conversation", to: "/conversation?swarm=echo-base" }]);
    expect(originCrumbs("/integrity", "a")).toEqual([{ label: "Integrity", to: "/integrity" }]);
  });

  it("no origin (a pasted link) falls back to the member's Timeline", () => {
    expect(originCrumbs(null, "v3-c7r")).toEqual([{ label: "Timeline (v3-c7r)", to: "/timeline?agent=v3-c7r" }]);
    expect(originCrumbs("/somewhere/unknown", "v3-c7r")[0].to).toBe("/timeline?agent=v3-c7r");
  });

  it("trace: origin / Record #n (keeping the origin) / Trace", () => {
    const crumbs = recordCrumbs({ agent: "v3-c7r", seq: "5", from: FILTERED, page: "trace" });
    expect(crumbs.map((c) => c.label)).toEqual(["Timeline (v3-c7r)", "Record #5", "Trace"]);
    expect(crumbs[1].to).toBe(`/record/v3-c7r/5?from=${encodeURIComponent(FILTERED)}`);
    expect(crumbs[2].to).toBeUndefined();
    expect(recordCrumbs({ agent: "v3-c7r", seq: "5", from: null, page: "record" }).map((c) => c.label))
      .toEqual(["Timeline (v3-c7r)", "Record #5"]);
    expect(timelinePath("v3-c7r", 5)).toBe("/timeline?agent=v3-c7r&focus=5");
  });
});
