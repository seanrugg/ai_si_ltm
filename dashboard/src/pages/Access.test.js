import { describe, expect, it } from "vitest";
import { settingsFormFrom } from "./Access.jsx";

describe("access settings form", () => {
  it("starts from the server's effective mode and scope, not a default", () => {
    const f = settingsFormFrom({ mode: "open", open_scope: { types: ["thought", "lesson"], swarms: null } });
    expect(f.mode).toBe("open");
    expect([...f.types].sort()).toEqual(["lesson", "thought"]);
    expect(f.swarms).toBe("");
  });
  it("lists named swarms and drops the free-agents null", () => {
    const f = settingsFormFrom({ mode: "token", open_scope: { types: [], swarms: ["echo-base", null] } });
    expect(f.swarms).toBe("echo-base");
  });
  it("falls back to off only when nothing has loaded", () => {
    expect(settingsFormFrom(undefined).mode).toBe("off");
  });
});
