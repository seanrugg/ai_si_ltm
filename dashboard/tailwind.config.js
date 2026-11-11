/** Design tokens. Ink-blue night console with amber "status LED" accent:
 *  the dashboard is the window into a house full of small robots. */
const v = (name) => `rgb(var(--c-${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      // Colors are CSS variables (src/index.css) so themes can swap them (#198).
      // Classic's values are the defaults; role, kind and category colors
      // (lib/visual.js) never change with the theme.
      colors: {
        ink: { DEFAULT: v("ink"), raised: v("ink-raised"), line: v("ink-line"), hover: v("ink-hover") },
        paper: { DEFAULT: v("paper"), muted: v("paper-muted"), faint: v("paper-faint") },
        led: { DEFAULT: v("led"), dim: v("led-dim") },
        ok: v("ok"),
        warn: v("warn"),
        bad: v("bad"),
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
        display: ["var(--font-display)", "system-ui", "sans-serif"],
      },
      fontSize: {
        xs: ["0.78rem", { lineHeight: "1.15rem" }],
        sm: ["0.875rem", { lineHeight: "1.35rem" }],
        base: ["1rem", { lineHeight: "1.55rem" }],
        lg: ["1.2rem", { lineHeight: "1.7rem" }],
        xl: ["1.5rem", { lineHeight: "2rem" }],
      },
    },
  },
  plugins: [],
};
