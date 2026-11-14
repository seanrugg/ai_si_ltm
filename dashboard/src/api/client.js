// Thin fetch wrapper for the admin API.
// - Cookie auth (same origin; nginx serves the dashboard and proxies /api).
// - Every non-GET request carries the CSRF header the server requires.
// - A 401 anywhere calls the unauthorized handler (the re-login overlay, #378).
// - Requests the admin started (shortly after a click, key press or scroll) carry
//   X-LTMS-Activity: user, which extends the idle timeout; background polling
//   doesn't. Pass `activity: true/false` to override.
// - The session's remaining time comes back in X-LTMS-Session-* headers.
import { isUserInitiated, noteActivitySent, noteSessionHeaders } from "../lib/adminSession.js";

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

export async function api(path, { method = "GET", params, body, headers: extra, activity } = {}) {
  const headers = { Accept: "application/json", ...(extra || {}) };
  if (method !== "GET") headers["X-LTMS-Admin"] = "1";
  if (activity ?? isUserInitiated()) {
    headers["X-LTMS-Activity"] = "user";
    noteActivitySent();
  }
  // FormData (multipart) and Blob (raw bytes, e.g. an upload chunk) are sent as they are.
  const form = (typeof FormData !== "undefined" && body instanceof FormData)
    || (typeof Blob !== "undefined" && body instanceof Blob);
  if (body !== undefined && !form) headers["Content-Type"] = "application/json";
  const res = await fetch(`/api${path}${query(params)}`, {
    method,
    headers,
    credentials: "same-origin",
    // A FormData body is sent as multipart; the browser sets the boundary.
    body: body === undefined ? undefined : form ? body : JSON.stringify(body),
  });
  noteSessionHeaders(res.headers);
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* empty body */
  }
  if (!res.ok) {
    const err = data?.error || {};
    const error = new ApiError(res.status, err.code || "http_error", err.message || `Request failed (${res.status})`);
    if (res.status === 401 && !path.startsWith("/auth/admin/login")) onUnauthorized(error);
    throw error;
  }
  return data;
}
