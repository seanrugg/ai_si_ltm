// Admin session timeout UI (issue #378).
//
// <SessionBoundary> wraps the signed-in app. It:
// - notes admin activity on the page (click, key press, wheel, scroll, touch) and
//   sends a heartbeat at most once a minute when nothing else was sent;
// - shows a non-modal warning before the idle timeout ("Stay signed in"), or
//   before the maximum session length (no extension possible);
// - on a 401, or when the session runs out, shows a sign-in dialog *over* the
//   page. The page stays mounted, so the route and unsaved form state (the
//   charter editor, a directive draft) survive signing in again. Nothing secret
//   is stored.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, setUnauthorizedHandler } from "../api/client.js";
import {
  formatCountdown, getSession, heartbeatDue, noteLocalActivity, sessionStatus, subscribe,
} from "../lib/adminSession.js";

const ACTIVITY_EVENTS = ["pointerdown", "keydown", "wheel", "touchstart", "scroll"];

function minutesText(ms) {
  const m = Math.max(1, Math.ceil(ms / 60_000));
  return `${m} minute${m === 1 ? "" : "s"}`;
}

export function SessionBanner({ status, onStay, busy }) {
  if (status.kind === "idle-warning") {
    return (
      <div role="alert"
           className="fixed bottom-4 left-1/2 z-40 flex w-[min(36rem,calc(100vw-1rem))] -translate-x-1/2 flex-wrap items-center gap-3 rounded-md border border-warn bg-ink p-3 text-sm text-paper shadow-lg">
        {/* Screen readers hear the minute count; the per-second countdown is visual only. */}
        <span className="sr-only">You&apos;ll be signed out in {minutesText(status.remainingMs)} because of inactivity.</span>
        <span aria-hidden="true">You&apos;ll be signed out in {formatCountdown(status.remainingMs)} because of inactivity.</span>
        <button type="button" className="btn btn-primary" onClick={onStay} disabled={busy}>Stay signed in</button>
      </div>
    );
  }
  if (status.kind === "max-warning") {
    return (
      <div role="alert"
           className="fixed bottom-4 left-1/2 z-40 w-[min(36rem,calc(100vw-1rem))] -translate-x-1/2 rounded-md border border-warn bg-ink p-3 text-sm text-paper shadow-lg">
        Your session ends in {minutesText(status.remainingMs)}; sign in again to continue.
      </div>
    );
  }
  return null;
}

export function ReloginDialog({ username, reason, onSignedIn }) {
  const [form, setForm] = useState({ username: username || "", password: "" });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const password = useRef(null);
  const user = useRef(null);

  useEffect(() => {
    (username ? password : user).current?.focus();
  }, [username]);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await api("/auth/admin/login", { method: "POST", body: form, activity: true });
      onSignedIn(res.username);
    } catch (err) {
      setError(err.status === 401 ? "That username and password don't match." : err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4">
      <form role="dialog" aria-modal="true" aria-labelledby="relogin-h" aria-describedby="relogin-d"
            onSubmit={submit} className="panel w-full max-w-sm p-6">
        <h2 id="relogin-h" className="text-lg font-bold">Signed out</h2>
        <p id="relogin-d" className="mb-4 mt-1 text-sm text-paper-muted">
          {reason || "Your session ended."} Sign in again to carry on; this page and anything you typed stay as they are.
        </p>
        <label className="mb-3 block text-sm">
          Username
          <input ref={user} className="field mt-1 w-full" autoComplete="username" required
                 value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
        </label>
        <label className="mb-4 block text-sm">
          Password
          <input ref={password} className="field mt-1 w-full" type="password" autoComplete="current-password" required
                 value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        </label>
        {error && <p className="mb-3 text-sm text-bad" role="alert">{error}</p>}
        <button className="btn btn-primary w-full justify-center" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}

function reasonText(err) {
  const msg = err?.message || "";
  if (msg.includes("inactivity")) return "You were signed out because of inactivity.";
  if (msg.includes("maximum")) return "Your session reached its maximum length.";
  return "Your session ended.";
}

export default function SessionBoundary({ username, onSignedIn, children }) {
  const [expired, setExpired] = useState(null); // null, or the reason text
  const [now, setNow] = useState(() => Date.now());
  const [, setSession] = useState(getSession);
  const [busy, setBusy] = useState(false);
  const checking = useRef(false);

  useEffect(() => {
    setUnauthorizedHandler((err) => setExpired(reasonText(err)));
    return () => setUnauthorizedHandler(() => {});
  }, []);

  useEffect(() => subscribe(setSession), []);

  const sendActivity = useCallback(async () => {
    try {
      await api("/auth/admin/activity", { method: "POST", activity: true });
    } catch {
      /* a 401 opens the sign-in dialog; anything else is retried on the next activity */
    }
  }, []);

  useEffect(() => {
    if (expired) return undefined;
    function onActivity() {
      const t = Date.now();
      noteLocalActivity(t);
      if (heartbeatDue(t)) sendActivity();
    }
    for (const ev of ACTIVITY_EVENTS) document.addEventListener(ev, onActivity, { capture: true, passive: true });
    return () => {
      for (const ev of ACTIVITY_EVENTS) document.removeEventListener(ev, onActivity, { capture: true });
    };
  }, [expired, sendActivity]);

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const status = sessionStatus(now);

  // When the local countdown runs out, ask the server (without counting it as
  // activity). A 401 opens the dialog; otherwise the fresh headers move the deadline.
  useEffect(() => {
    if (status.kind !== "expired" || expired || checking.current) return;
    checking.current = true;
    api("/admin/me", { activity: false }).catch(() => {}).finally(() => { checking.current = false; });
  }, [status.kind, expired]);

  async function stay() {
    setBusy(true);
    await sendActivity();
    setBusy(false);
  }

  return (
    <>
      {children}
      {!expired && <SessionBanner status={status} onStay={stay} busy={busy} />}
      {expired && (
        <ReloginDialog username={username} reason={expired}
                       onSignedIn={(u) => { setExpired(null); onSignedIn?.(u); }} />
      )}
    </>
  );
}
