# FLTMS dashboard

React 18 + Vite 5 + Tailwind 3. Admin-only; talks to `/api/admin/*` with the
`ltms_admin` cookie.

```bash
cd dashboard
npm ci
npm run lint        # eslint: undefined names, unused vars, hook rules
npm test            # vitest (helpers in src/lib and page helpers)
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
| `src/pages/` | One file per route |

## Adding a page

1. Copy `src/pages/Integrity.jsx` (it's the simplest complete page).
2. Fetch with `useApi("/admin/<endpoint>", params)`; render `Loading`, `ErrorNote`, and an `EmptyState` that tells the admin what to do.
3. Add the route in `src/App.jsx` (and a link in `src/components/Shell.jsx` if it belongs in the sidebar).
4. `npm run lint && npm test && npm run build` must pass.

Design tokens are in `tailwind.config.js`: `ink` backgrounds, `paper` text,
`led` (amber) for the one primary action per screen, `ok/warn/bad` for status
only. Use the shared components instead of new styles where you can.

## Repeated text

If the same explanation would print on every card or row of a page, it belongs
behind a `HelpTip` ("?") next to the control it explains, not in the layout.
Cards show what differs between them (names, values, status); text that is
identical everywhere costs space on every card and gets skimmed past anyway.

- One `HelpTip` per control, with a specific `label` ("About ingest limits"),
  so screen-reader users still reach it from each card. Put the text in
  `src/lib/help.js` so it is written once.
- A notice that must stay visible (a licence credit, a safety warning) is
  shown once for the whole list or page, not once per item.
- Warnings that change what an admin is about to do (revoke, wipe, delete) stay
  in the confirm panel where they apply; they are not moved into a tooltip.
