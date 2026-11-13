import { describe, expect, it } from "vitest";
import { pingState } from "./ping.js";
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
});
