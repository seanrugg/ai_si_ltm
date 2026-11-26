// Themed copy (#509). A theme may give the dashboard's ambient text a voice:
// the sidebar tagline, the sign-in subtitle, loading and empty states.
//
// Rules, so a theme never costs an operator anything:
// - Action labels, nav labels, field labels, errors and data meanings stay
//   plain in every theme. A voice only decorates or adds; it never renames
//   what a control does.
// - Decorative lines are aria-hidden where they sit beside plain text, so
//   screen readers hear the same page in every theme.
// - Villain is an homage to MF DOOM. Its lines are original; song and album
//   titles appear only as nods. Never paste lyrics in here.
import { useSyncExternalStore } from "react";
import { currentTheme, onThemeChange } from "./theme.js";

export const VOICES = {
  villain: {
    tagline: "ALL CAPS fleet memory",
    loginLead: "The villain keeps the fleet's memory. Mask on to sign in.",
    signingIn: "Masking up…",
    signedInAs: "Behind the mask: {name}",
    signOut: "Sign out (mask off)",
    loading: [
      "Operation: Doomsday in progress",
      "Squeezing the accordion",
      "Feeding it through the meat grinder",
      "Digging in the crates",
      "Flipping the sample",
      "Warming up the metal fingers",
      "Counting rhymes like dimes",
      "Stacking the money folder",
      "Simmering the next course",
      "Cueing the vaudeville villain",
    ],
    empty: [
      "Quiet in the lair. For now.",
      "Strange ways: nothing here yet.",
      "Empty plate. The next course is coming.",
      "No beef yet. Just the beat.",
      "The villain is patient. Records will come.",
      "Shadows of tomorrow; nothing today.",
      "One beer, zero records.",
    ],
  },
};

/** Fill "{name}" style slots. */
function fill(text, vars) {
  return text.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
}

/** The theme's line for `key`, or `fallback` when the theme has no voice for it. */
export function say(theme, key, fallback, vars = {}) {
  const v = VOICES[theme]?.[key];
  return typeof v === "string" ? fill(v, vars) : fallback == null ? fallback : fill(fallback, vars);
}

/** One line from the theme's `key` pool, chosen by `seed` in [0, 1); null if none. */
export function pick(theme, key, seed) {
  const pool = VOICES[theme]?.[key];
  if (!Array.isArray(pool) || pool.length === 0) return null;
  return pool[Math.min(pool.length - 1, Math.floor(seed * pool.length))];
}

/** The current theme, re-rendering the caller when the admin switches themes. */
export function useThemeName() {
  return useSyncExternalStore(onThemeChange, currentTheme, () => "classic");
}

/** { theme, say(key, fallback, vars), pick(key, seed) } bound to the current theme. */
export function useVoice() {
  const theme = useThemeName();
  return {
    theme,
    say: (key, fallback, vars) => say(theme, key, fallback, vars),
    pick: (key, seed) => pick(theme, key, seed),
  };
}
