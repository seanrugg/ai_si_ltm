import { describe, expect, it } from "vitest";
import { describeChange, membershipChange } from "./membership.js";

const inA = (role) => ({ swarm: "swarm-a", role });

describe("membershipChange", () => {
  it("same swarm and role is none", () => {
    expect(membershipChange(inA("member"), inA("member"))).toEqual({ kind: "none", hubWarning: false });
    expect(membershipChange(null, { swarm: null, role: "member" }).kind).toBe("none");
  });

  it("free agent to a swarm is join; swarm to free agent is leave", () => {
    expect(membershipChange(null, inA("observer")).kind).toBe("join");
    expect(membershipChange(inA("member"), { swarm: null, role: "member" }).kind).toBe("leave");
  });

  it("different swarm is move; same swarm, different role is role", () => {
    expect(membershipChange(inA("member"), { swarm: "swarm-b", role: "member" }).kind).toBe("move");
    expect(membershipChange(inA("member"), inA("observer")).kind).toBe("role");
  });

  it("warns when a hub is moved, demoted or released, not for none", () => {
    expect(membershipChange(inA("hub"), { swarm: "swarm-b", role: "member" }).hubWarning).toBe(true);
    expect(membershipChange(inA("hub"), inA("member")).hubWarning).toBe(true);
    expect(membershipChange(inA("hub"), { swarm: null, role: "member" }).hubWarning).toBe(true);
    expect(membershipChange(inA("hub"), inA("hub")).hubWarning).toBe(false);
  });
});

describe("describeChange", () => {
  it("says what Apply will do", () => {
    expect(describeChange(inA("member"), { swarm: "swarm-b", role: "member" })).toBe("Moves from swarm-a to swarm-b as member");
    expect(describeChange(inA("member"), inA("observer"))).toBe("Changes role to observer");
    expect(describeChange(null, inA("member"))).toBe("Joins swarm-a as member");
    expect(describeChange(inA("member"), { swarm: null, role: "member" })).toBe("Leaves swarm-a and becomes a free agent");
    expect(describeChange(inA("member"), inA("member"))).toBe("");
  });
});
