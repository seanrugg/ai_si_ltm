# Admin sessions

An admin dashboard session ends after a period **without admin activity**, or
after a **maximum length** from sign-in, whichever comes first (issue #378).

| Setting | Range | Default | Environment default |
|---|---|---|---|
| Sign out after inactivity | 15 min – 8 h | 60 min | `LTMS_ADMIN_IDLE_MINUTES` |
| Maximum session length | 1 – 24 h (≥ the inactivity timeout) | 12 h | `LTMS_ADMIN_SESSION_MAX_HOURS` |
| Warn before sign-out | 1 – 10 min (less than the inactivity timeout) | 2 min | `LTMS_ADMIN_WARN_MINUTES` |

Change them in **Settings → Admin sessions**. The dashboard value overrides the
environment and applies to sessions already open at their next request. Each
change is written to the audit log as `settings_change` with the old and new
values. Member and MCP token lifetimes are not affected.

## What counts as activity

What the admin does, not what the page does:

- requests the admin starts (opening a page, submitting a form, filtering) carry
  `X-LTMS-Activity: user`;
- reading, scrolling and typing without a request send a heartbeat
  (`POST /api/auth/admin/activity`) at most once a minute.

Background polling and auto-refresh carry no activity header, so an unattended
dashboard still times out. The server stores and compares the idle time
(`admin_sessions.last_active_at`, written at most once a minute per session);
the browser can't set its own expiry. Every admin response reports the time left
in `X-LTMS-Session-Idle-Remaining` and `X-LTMS-Session-Max-Remaining` (seconds).

## Warning and signing in again

Before the inactivity limit the dashboard shows a banner, announced to screen
readers, with a **Stay signed in** button. Near the maximum length it says the
session is about to end; that can't be extended. When a session ends, a sign-in
dialog opens over the current page. The page stays as it was, including unsaved
input such as a charter draft or a directive, and you return to it after signing in.

The cookie lives 24 hours (the longest maximum allowed) so the browser never
drops it before the server's limit; the server enforces the configured limits on
every request.

## Security policy

A longer inactivity timeout means an unattended, signed-in browser stays usable
for longer. Check the setting against your site's security policy. On a
government network, STIG/ATO session-lock controls may cap the idle timeout,
typically well under an hour; confirm the exact control and value before relying
on it. A site can enforce its own ceiling with `LTMS_ADMIN_IDLE_MAX_MINUTES`: the
dashboard then cannot set a longer inactivity timeout, and an existing longer
value is held to the ceiling. LTMS doesn't hard-code a policy value.

`LTMS_ADMIN_SESSION_TTL_SECONDS` (the old fixed 30-minute lifetime) is no longer
used and is ignored if set.
