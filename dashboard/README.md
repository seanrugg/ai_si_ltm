# LTMS dashboard

React 18 + Vite 5 + Tailwind 3. Admin-only; talks to `/api/admin/*` with the
`ltms_admin` cookie.

```bash
cd dashboard
npm ci
npm test            # vitest (pure helpers in src/lib)
npm run build       # → dist/
npm run dev         # http://localhost:5173, proxies /api to localhost:8080
```

Run a backend with demo data for development (from the repo root):

```bash
LTMS_DATABASE_URL=sqlite+aiosqlite:///./dev.db LTMS_ADMIN_PASSWORD=dev-password-123 \
  LTMS_COOKIE_SECURE=false uvicorn server.main:app --port 8080
python -m scripts.seed_demo --admin-password dev-password-123
```

Or serve the built dashboard from the backend itself (one process, handy on the LAN):
add `LTMS_DASHBOARD_DIR=dashboard/dist` to the uvicorn command and open http://localhost:8080.

## Structure

| Path | What |
|---|---|
| `src/App.jsx` | Router (hash routes, so any static host works) and admin session context |
| `src/api/client.js` | `api(path, {method, params, body})`. Adds the CSRF header and handles 401 |
| `src/api/useApi.js` | `useApi(path, params)` returns `{data, error, loading, reload}` |
| `src/components/ui.jsx` | `PageHeader`, `StatusBadge`, `HashStrip`, `Loading`, `ErrorNote`, `EmptyState` |
| `src/components/RecordRow.jsx` | Expandable record row + `RecordDetail` |
| `src/pages/` | One file per route. `ComingSoon.jsx` marks pages not built yet |

## Adding a page

1. Copy `src/pages/Integrity.jsx` (it's the simplest complete page).
2. Fetch with `useApi("/admin/<endpoint>", params)`; render `Loading`, `ErrorNote`, and an `EmptyState` that tells the admin what to do.
3. Swap the `ComingSoon` route in `src/App.jsx` for your page.
4. `npm test && npm run build` must pass.

Design tokens are in `tailwind.config.js`: `ink` backgrounds, `paper` text,
`led` (amber) for the one primary action per screen, `ok/warn/bad` for status
only. Use the shared components instead of new styles where you can.
