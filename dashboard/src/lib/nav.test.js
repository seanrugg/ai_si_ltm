import { afterEach, describe, expect, it, vi } from "vitest";
import {
  GROUPS, OVERVIEW, groupBadge, groupOf, groupOpen, itemBadge, loadGroupChoices, saveGroupChoices, setupDone,
} from "./nav.js";

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("nav groups (#379)", () => {
  it("match the agreed order and grouping", () => {
    expect(OVERVIEW).toEqual({ to: "/activity", label: "Overview" });
    expect(GROUPS.map((g) => [g.label, g.collapsible, g.items.map((i) => i.label)])).toEqual([
      ["Operate", false, ["Timeline", "Conversation", "Members", "Swarms", "Directives"]],
      ["Analyze", false, ["Health", "Volume", "Outcomes", "Lessons & policies", "Integrity", "Geography"]],
      ["Govern", true, ["Charter", "Audit log", "Retention", "Export"]],
      ["Configure", true, ["Settings", "MCP access", "LRS endpoints", "Datasets", "System status"]],
    ]);
  });

  it("keeps the old URLs under the new labels", () => {
    const urls = Object.fromEntries(GROUPS.flatMap((g) => g.items.map((i) => [i.label, i.to])));
    expect(urls["MCP access"]).toBe("/access");
    expect(urls["LRS endpoints"]).toBe("/lrs");
    expect(urls["Lessons & policies"]).toBe("/lessons");
    expect(urls.Retention).toBe("/retention");
  });

  it("finds the group of a route, including sub-pages", () => {
    expect(groupOf("/charter")).toBe("govern");
    expect(groupOf("/members/alice/profile")).toBe("operate");
    expect(groupOf("/lrs")).toBe("configure");
    expect(groupOf("/activity")).toBeNull();
    expect(groupOf("/record/a/1")).toBeNull();
  });
});

describe("badges", () => {
  const status = { members_pending: 2, charter_overdue: 3, directive_declines: 0, lrs_failing: 1 };
  it("say what they count", () => {
    const members = GROUPS[0].items.find((i) => i.to === "/members");
    expect(itemBadge(members, status)).toEqual({ count: 2, warn: false, text: "2 members waiting for approval" });
    const directives = GROUPS[0].items.find((i) => i.to === "/directives");
    expect(itemBadge(directives, status)).toBeNull();
  });

  it("total for a group, or a warning", () => {
    const govern = GROUPS.find((g) => g.key === "govern");
    expect(groupBadge(govern, status)).toEqual({ count: 3, warn: false, text: "3 members overdue on the charter" });
    const configure = GROUPS.find((g) => g.key === "configure");
    expect(groupBadge(configure, status)).toMatchObject({ warn: true, text: "1 LRS endpoint failing or paused" });
    expect(groupBadge(govern, { charter_overdue: 0 })).toBeNull();
  });
});

describe("collapse defaults", () => {
  it("open while the fleet isn't set up, collapsed once it is; a hand choice wins", () => {
    expect(groupOpen("govern", {}, false)).toBe(true);
    expect(groupOpen("govern", {}, true)).toBe(false);
    expect(groupOpen("govern", { govern: true }, true)).toBe(true);
    expect(groupOpen("govern", { govern: false }, false)).toBe(false);
  });

  it("needs every required checklist item, not the optional LRS", () => {
    const all = { charter_published: true, swarm_created: true, hub_appointed: true, member_approved: true };
    expect(setupDone(null)).toBeNull();
    expect(setupDone({ ...all, lrs_added: false })).toBe(true);
    expect(setupDone({ ...all, hub_appointed: false, lrs_added: true })).toBe(false);
  });

  it("restores choices, and survives storage that throws", () => {
    saveGroupChoices({ govern: true });
    expect(loadGroupChoices()).toEqual({ govern: true });
    window.localStorage.setItem("ltms.nav.groups", "not json");
    expect(loadGroupChoices()).toEqual({});
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    expect(() => saveGroupChoices({ configure: false })).not.toThrow();
    expect(loadGroupChoices()).toEqual({});
  });
});
