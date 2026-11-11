import React from "react";
import ReactDOM from "react-dom/client";
import "@fontsource/atkinson-hyperlegible/400.css";
import "@fontsource/atkinson-hyperlegible/700.css";
import "@fontsource/jetbrains-mono/400.css";
// Holo theme fonts (#198), bundled: never loaded from a CDN.
import "@fontsource/bebas-neue/400.css";
import "@fontsource/dm-sans/400.css";
import "@fontsource/dm-sans/700.css";
import "@fontsource/dm-mono/400.css";
import "./index.css";
import App from "./App.jsx";
import { getTheme, setTheme } from "./lib/theme.js";

// Before first paint, so the page never flashes the wrong theme.
setTheme(getTheme());

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
