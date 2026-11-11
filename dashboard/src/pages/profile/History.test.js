import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import History from "./History.jsx";
import { HISTORY_ICONS, historyMarker } from "../../lib/profile.js";
import { NEUTRAL, ROLES, category } from "../../lib/visual.js";

afterEach(() => cleanup());
const opts = { ROLES, NEUTRAL, learnColor: category("learn").color };

describe("historyMarker", () => {
  it("colors by kind and picks an icon per action", () => {
    const cases = [
      [{ kind: "membership", action: "enroll_request" }, ROLES.free.color, "enrolled"],
      [{ kind: "membership", action: "swarm_join", detail: "as hub" }, ROLES.hub.color, "joined"],
      [{ kind: "membership", action: "swarm_join", detail: "as member" }, ROLES.member.color, "joined"],
      [{ kind: "membership", action: "swarm_leave" }, ROLES.free.color, "joined"],
      [{ kind: "learning", action: "first_lesson" }, category("learn").color, "lesson"],
      [{ kind: "admin", action: "network_change" }, NEUTRAL, "network"],
      [{ kind: "admin", action: "memory_wipe" }, NEUTRAL, "wipe"],
      [{ kind: "admin", action: "record_delete" }, NEUTRAL, "deletion"],
      [{ kind: "membership", action: "permissions_change" }, ROLES.free.color, "other"],
    ];
    for (const [event, color, icon] of cases) {
      const m = historyMarker(event, opts);
      expect([m.color, m.icon], event.action).toEqual([color, icon]);
      expect(m.iconPath).toBe(HISTORY_ICONS[icon]);
    }
  });
});

describe("History tab", () => {
  it("lists events newest first with title and detail", () => {
    render(React.createElement(History, { history: { events: [
      { ts: "2026-11-10T12:00:00Z", kind: "admin", action: "memory_wipe", title: "Memory wiped", detail: "wipe 1" },
      { ts: "2026-11-09T12:00:00Z", kind: "membership", action: "swarm_join", title: "Joined echo-base", detail: "as member" },
    ] } }));
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items[0]).toContain("Memory wiped");
    expect(items[1]).toContain("Joined echo-base");
  });
});
