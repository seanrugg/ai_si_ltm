import { afterEach, describe, expect, it, vi } from "vitest";
import { HOLO, THEMES, contrast, getTheme, setTheme } from "./theme.js";

afterEach(() => {
  vi.restoreAllMocks();
  try { window.localStorage.clear(); } catch { /* ignore */ }
});

describe("Holo contrast", () => {
  it("text on every Holo surface meets 4.5:1", () => {
    for (const text of ["paper", "paper-muted", "paper-faint"]) {
      for (const surface of ["ink", "ink-raised"]) {
        expect(contrast(HOLO[text], HOLO[surface]), `${text} on ${surface}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });
  it("the muted blue the issue warns about would fail", () => {
    expect(contrast("#4e6a8a", HOLO.ink)).toBeLessThan(4.5);
  });
});

describe("setTheme / getTheme", () => {
  it("sets data-theme and remembers it", () => {
    expect(getTheme()).toBe("classic");
    expect(setTheme("holo")).toBe("holo");
    expect(document.documentElement.getAttribute("data-theme")).toBe("holo");
    expect(getTheme()).toBe("holo");
    expect(setTheme("bogus")).toBe("classic");
    expect(THEMES.map((t) => t.key)).toEqual(["classic", "holo"]);
  });
  it("still works when localStorage is unavailable", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    expect(setTheme("holo")).toBe("holo");
    expect(document.documentElement.getAttribute("data-theme")).toBe("holo");
    expect(getTheme()).toBe("classic");
  });
});
