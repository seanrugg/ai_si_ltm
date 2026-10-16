import { describe, expect, it } from "vitest";
import { GENESIS, hashColors, relativeTime, shortHash } from "./format.js";

describe("format", () => {
  it("shortens hashes", () => {
    expect(shortHash("abcdef0123456789", 6)).toBe("abcdef…");
    expect(shortHash("")).toBe("");
  });
  it("derives 8 stable colors from a hash", () => {
    const h = "ab".repeat(32);
    expect(hashColors(h)).toHaveLength(8);
    expect(hashColors(h)).toEqual(hashColors(h));
    expect(hashColors(GENESIS)[0]).toBe("hsl(0 62% 52%)");
    expect(hashColors("short")).toEqual([]);
  });
  it("formats relative time", () => {
    const now = Date.parse("2026-10-15T12:00:00Z");
    expect(relativeTime("2026-10-15T11:59:00Z", now)).toBe("1 minute ago");
    expect(relativeTime("2026-10-14T12:00:00Z", now)).toBe("yesterday");
  });
});
