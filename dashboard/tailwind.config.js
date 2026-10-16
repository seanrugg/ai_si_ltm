/** Design tokens. Ink-blue night console with amber "status LED" accent:
 *  the dashboard is the window into a house full of small robots. */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: "#0F1828", raised: "#162236", line: "#26344F", hover: "#1C2A42" },
        paper: { DEFAULT: "#E7EAF0", muted: "#93A0B8", faint: "#5E6B85" },
        led: { DEFAULT: "#F2B544", dim: "#8A6A2B" },
        ok: "#5BC28A",
        warn: "#E8A23A",
        bad: "#F0605D",
      },
      fontFamily: {
        sans: ['"Atkinson Hyperlegible"', "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "monospace"],
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
