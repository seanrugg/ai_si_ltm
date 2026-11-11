// Dashboard themes (issue #198). Themes change surfaces, text and type, never
// meanings: role, kind and data-category colors live in lib/visual.js.
export const THEMES = [
  { key: "classic", label: "Classic" },
  { key: "holo", label: "Holo" },
];
export const DEFAULT_THEME = "classic";
const KEY = "ltms.theme";

// Holo's palette, for the contrast test (it mirrors src/index.css).
export const HOLO = {
  ink: "#060a0f", "ink-raised": "#0c1420", "ink-line": "#1a2d46", "ink-hover": "#111b2c",
  paper: "#e2eaf5", "paper-muted": "#8aa4c2", "paper-faint": "#6c86a6",
  led: "#00e5ff", "led-dim": "#0a7f91", ok: "#00ff9d", warn: "#ffb547", bad: "#ff6b6b",
};

function valid(name) {
  return THEMES.some((t) => t.key === name) ? name : DEFAULT_THEME;
}

/** The stored theme, or Classic when there is none or storage is unavailable. */
export function getTheme() {
  try {
    return valid(window.localStorage.getItem(KEY));
  } catch {
    return DEFAULT_THEME;
  }
}

/** Apply a theme to <html data-theme> and remember it (best effort). */
export function setTheme(name) {
  const theme = valid(name);
  document.documentElement.setAttribute("data-theme", theme);
  try {
    window.localStorage.setItem(KEY, theme);
  } catch {
    // storage unavailable (private mode, blocked): the theme still applies for this visit
  }
  return theme;
}

/** WCAG relative luminance of "#rrggbb". */
export function luminance(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}
