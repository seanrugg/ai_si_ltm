import { describe, expect, it } from "vitest";
import { modeOf, offReason, pingHelp, pingState } from "./ping.js";
import { PING_ABOUT } from "./help.js";

describe("ping state (#356)", () => {
  const req = "2026-11-13T04:00:00Z";
  it("reads each stage", () => {
    expect(pingState(null).state).toBe("none");
    expect(pingState({ requested_at: req }, "2026-11-13T04:00:20Z").text)
      .toBe("Waiting for the member or its hub to pick it up (20 s).");
    expect(pingState({ requested_at: req, delivered_at: "2026-11-13T04:00:30Z" }, "2026-11-13T04:02:00Z").text)
      .toBe("Delivered 30 s after the request; no answer yet (2 min).");
    expect(pingState({ requested_at: req, delivered_at: "2026-11-13T04:00:30Z", answered_at: "2026-11-13T04:00:41Z",
      answer: { summary: "heartbeat (ping)" } }).text).toBe("Answered in 41 s: heartbeat (ping).");
  });
  it("explains the timing", () => {
    expect(PING_ABOUT).toMatch(/every 30 s/);
  });
  it("says how each answer mode answers (#384)", () => {
    const m = (mode, source = "declared", reason = null) => ({ mode: { mode, source, reason } });
    expect(pingHelp(m("polling"))).toMatch(/usually answers within a minute or two.*down or offline/);
    expect(pingHelp(m("on_use"))).toMatch(/only runs when someone uses it.*doesn't mean it's broken/);
    expect(pingHelp(m("none", "admin", "an admin submits for it"))).toMatch(/can't answer pings: an admin submits for it/);
    expect(pingHelp(m("none", "admin", "x"))).toMatch(/An admin set this/);
    expect(pingHelp(null)).toMatch(/doesn't know how this member checks in/);
    expect(pingHelp(m("polling", "inferred"))).toMatch(/Inferred from its regular heartbeats/);
    expect(pingHelp(m("on_use", "inferred"))).toMatch(/chat assistant without heartbeats/);
    expect(modeOf(m("bogus"))).toBe("unknown");
  });
  it("turns ping off for none, with the reason as text", () => {
    expect(offReason({ mode: { mode: "none", reason: "held by the fleet" } })).toMatch(/^Ping is off: .*held by the fleet/);
    expect(offReason({ mode: { mode: "none" } })).toMatch(/its spec sheet says so/);
    expect(offReason({ mode: { mode: "on_use" } })).toBeNull();
  });
  it("status text follows the answer mode and who picked it up", () => {
    const onUse = { mode: { mode: "on_use" } };
    expect(pingState({ requested_at: req, ...onUse }, "2026-11-13T04:49:00Z").text)
      .toBe("Waiting: answers when next used (49 min).");
    expect(pingState({ requested_at: req, delivered_at: "2026-11-13T04:00:30Z", delivered_via: "hub" }, "2026-11-13T04:02:00Z").text)
      .toBe("Passed to the hub 30 s after the request; the member answers with its next record (2 min).");
    expect(pingState({ requested_at: req, delivered_at: "2026-11-13T04:00:30Z", delivered_via: "member" }, "2026-11-13T04:02:00Z").text)
      .toBe("Picked up by the member 30 s after the request; no answer yet (2 min).");
    expect(pingState({ requested_at: req, delivered_at: "2026-11-13T04:00:30Z", delivered_via: "member", ...onUse },
      "2026-11-13T04:02:00Z").text).toMatch(/when next used/);
  });
});
