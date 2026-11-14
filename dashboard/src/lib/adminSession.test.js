import { afterEach, describe, expect, it } from "vitest";
import {
  formatCountdown, heartbeatDue, isUserInitiated, noteActivitySent, noteLocalActivity, noteSession,
  noteSessionHeaders, resetSession, sessionStatus,
} from "./adminSession.js";

afterEach(() => resetSession());

describe("admin session timing (#378)", () => {
  it("marks requests made just after local activity as admin-initiated", () => {
    expect(isUserInitiated(1_000_000)).toBe(false);
    noteLocalActivity(1_000_000);
    expect(isUserInitiated(1_003_000)).toBe(true);
    expect(isUserInitiated(1_010_000)).toBe(false);
  });

  it("throttles the heartbeat to once a minute, and only after local activity", () => {
    expect(heartbeatDue(100_000)).toBe(false);
    noteLocalActivity(100_000);
    expect(heartbeatDue(100_000)).toBe(true);
    noteActivitySent(100_000);
    noteLocalActivity(130_000);
    expect(heartbeatDue(130_000)).toBe(false);
    expect(heartbeatDue(160_000)).toBe(true);
  });

  it("warns at idle minus warn, and expires at the deadline", () => {
    noteSession({ idle: 3600, max: 12 * 3600, warn: 120 }, 0);
    expect(sessionStatus(0).kind).toBe("ok");
    expect(sessionStatus(3600_000 - 121_000).kind).toBe("ok");
    const s = sessionStatus(3600_000 - 120_000);
    expect(s.kind).toBe("idle-warning");
    expect(formatCountdown(s.remainingMs)).toBe("2:00");
    expect(sessionStatus(3600_000).kind).toBe("expired");
  });

  it("warns about the maximum length when it comes first", () => {
    noteSession({ idle: 3600, max: 100, warn: 120 }, 0);
    expect(sessionStatus(0).kind).toBe("max-warning");
  });

  it("reads the server's headers", () => {
    noteSessionHeaders(new Headers({ "X-LTMS-Session-Idle-Remaining": "60", "X-LTMS-Session-Max-Remaining": "600",
                                     "X-LTMS-Session-Warn": "120" }), 0);
    expect(sessionStatus(0).kind).toBe("idle-warning");
    noteSessionHeaders(new Headers(), 0); // no headers: unchanged
    expect(sessionStatus(0).kind).toBe("idle-warning");
  });
});
