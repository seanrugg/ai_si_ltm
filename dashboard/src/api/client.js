// Thin fetch wrapper for the admin API.
// - Cookie auth (same origin; nginx serves the dashboard and proxies /api).
// - Every non-GET request carries the CSRF header the server requires.
// - A 401 anywhere sends the app back to the login screen.

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn;
}

function query(params) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== "") q.set(k, v);
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

export async function api(path, { method = "GET", params, body } = {}) {
  const headers = { Accept: "application/json" };
  if (method !== "GET") headers["X-LTMS-Admin"] = "1";
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`/api${path}${query(params)}`, {
    method,
    headers,
    credentials: "same-origin",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* empty body */
  }
  if (!res.ok) {
    const err = data?.error || {};
    if (res.status === 401 && !path.startsWith("/auth/admin/login")) onUnauthorized();
    throw new ApiError(res.status, err.code || "http_error", err.message || `Request failed (${res.status})`);
  }
  return data;
}
