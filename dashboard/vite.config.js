import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` proxies /api to a local server on :8080, so the admin cookie
// stays same-origin exactly as it is in production (nginx serves both).
export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8080" },
  },
  test: { environment: "node" },
});
