// Admin session timing on the client (issue #378).
//
// The server owns the idle timeout: it counts from the last *admin* activity and
// reports what's left in response headers. This module only:
// - remembers when the admin last did something on the page (click, key, scroll),
//   so requests made right after it are marked as admin-initiated;
// - decides when a heartbeat is due (local activity, nothing sent for a minute);
// - keeps the latest remaining times the server reported, as local deadlines.
// Background polling made without recent admin activity isn't marked, so an
// unattended dashboard still times out.

export const ACTIVITY_WINDOW_MS = 5000;
export const HEARTBEAT_MS = 60_000;

let lastLocal = 0;
let lastSent = 0;
let state = { idleDeadline: null, maxDeadline: null, warnMs: 120_000 };
const listeners = new Set();

export function noteLocalActivity(now = Date.now()) {
  lastLocal = now;
}

/** True when the admin did something on the page in the last few seconds. */
export function isUserInitiated(now = Date.now()) {
  return lastLocal > 0 && now - lastLocal <= ACTIVITY_WINDOW_MS;
}

export function noteActivitySent(now = Date.now()) {
  lastSent = now;
}

/** A heartbeat is due when there was local activity since the last activity
 * sent to the server, and at least a minute has passed since then. */
export function heartbeatDue(now = Date.now()) {
  return lastLocal > lastSent && now - lastSent >= HEARTBEAT_MS;
}

export function getSession() {
  return state;
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function emit() {
  for (const fn of listeners) fn(state);
}

/** Record the remaining times the server reported (seconds). */
export function noteSession({ idle, max, warn }, now = Date.now()) {
  if (!Number.isFinite(idle) || !Number.isFinite(max)) return;
  state = {
    idleDeadline: now + idle * 1000,
    maxDeadline: now + max * 1000,
    warnMs: Number.isFinite(warn) ? warn * 1000 : state.warnMs,
  };
  emit();
}

/** Read the X-LTMS-Session-* headers from a fetch Response, if present. */
export function noteSessionHeaders(headers, now = Date.now()) {
  if (!headers || typeof headers.get !== "function") return;
  const idle = headers.get("X-LTMS-Session-Idle-Remaining");
  const max = headers.get("X-LTMS-Session-Max-Remaining");
  if (idle === null || max === null) return;
  noteSession({ idle: Number(idle), max: Number(max), warn: Number(headers.get("X-LTMS-Session-Warn")) }, now);
}

/** What to show for the session at `now`:
 * { kind: "ok" | "idle-warning" | "max-warning" | "expired", remainingMs }. */
export function sessionStatus(now = Date.now(), s = state) {
  if (s.idleDeadline === null || s.maxDeadline === null) return { kind: "ok", remainingMs: null };
  const idleLeft = s.idleDeadline - now;
  const maxLeft = s.maxDeadline - now;
  if (idleLeft <= 0 || maxLeft <= 0) return { kind: "expired", remainingMs: 0 };
  if (maxLeft <= s.warnMs && maxLeft <= idleLeft) return { kind: "max-warning", remainingMs: maxLeft };
  if (idleLeft <= s.warnMs) return { kind: "idle-warning", remainingMs: idleLeft };
  return { kind: "ok", remainingMs: null };
}

export function formatCountdown(ms) {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** For tests. */
export function resetSession() {
  lastLocal = 0;
  lastSent = 0;
  state = { idleDeadline: null, maxDeadline: null, warnMs: 120_000 };
  listeners.clear();
}
