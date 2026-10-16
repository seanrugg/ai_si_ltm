import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { localTime, relativeTime } from "../lib/format.js";
import { EmptyState, ErrorNote, Loading, PageHeader } from "../components/ui.jsx";

// The audit log actions the server emits (recall is the default view).
const ACTIONS = [
  "recall",
  "ingest_reject",
  "enroll_request",
  "enroll_approve",
  "enroll_reject",
  "member_revoke",
  "permissions_change",
  "admin_login",
  "admin_login_failed",
  "token_issued",
  "token_denied",
  "retention_purge",
];
const PAGE = 50;

function humanize(action) {
  return action.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function RecallLog() {
  const [draft, setDraft] = useState({ action: "recall", member: "" });
  const [entries, setEntries] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [more, setMore] = useState(false);

  const filterKey = JSON.stringify(draft);

  async function load(offset = 0) {
    setLoading(true);
    setError(null);
    try {
      const res = await api("/admin/recall-log", { params: draft });
      setEntries((prev) => (offset ? [...prev, ...res.entries] : res.entries));
      setMore(res.entries.length === PAGE);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey]);

  function reset() {
    setDraft({ action: "recall", member: "" });
  }

  return (
    <>
      <PageHeader title="Recall log">
        <button className="btn" onClick={() => load(0)} disabled={loading}>
          {loading ? "Loading…" : "Reload"}
        </button>
      </PageHeader>
      <p className="mb-4 max-w-prose text-sm text-paper-muted">
        The admin audit log. Every action a member takes on the server — recalls, enrollment, revokes, retention
        purges — is recorded here with who did it and when.
      </p>

      <form className="mb-4 flex flex-wrap items-end gap-2" aria-label="Filter recall log">
        <label className="flex flex-col gap-1 text-xs text-paper-muted">
          <span className="sr-only">Action</span>
          <select
            className="field"
            value={draft.action}
            onChange={(e) => setDraft({ ...draft, action: e.target.value })}
            aria-label="Action"
          >
            {ACTIONS.map((a) => (
              <option key={a} value={a}>
                {a === "recall" ? "Recalls" : a === "all" ? "Everything" : humanize(a)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-paper-muted">
          <span className="sr-only">Member</span>
          <input
            className="field w-40"
            placeholder="Member"
            value={draft.member}
            onChange={(e) => setDraft({ ...draft, member: e.target.value })}
            aria-label="Member"
          />
        </label>
        <button type="button" className="btn" onClick={reset}>
          Reset
        </button>
      </form>

      <ErrorNote error={error} onRetry={() => load(0)} />
      {entries.length > 0 && (
        <ul className="panel" aria-label="Audit log entries, newest first">
          {entries.map((e) => (
            <li key={`${e.id}-${e.ts}`} className="border-b border-ink-line last:border-b-0">
              <button
                type="button"
                aria-expanded="false"
                aria-label={`Entry ${humanize(e.action)} by ${e.actor}`}
                className="grid w-full grid-cols-[5.5rem_1fr_auto] items-baseline gap-x-3 px-3 py-2 text-left hover:bg-ink-hover
                           md:grid-cols-[7.5rem_1fr_auto_auto]"
              >
                <span className="text-xs text-paper-muted" title={localTime(e.ts)}>
                  {relativeTime(e.ts)}
                </span>
                <span className="truncate text-sm">
                  <span className="mr-2 font-bold">{e.actor}</span>
                  <span className="truncate text-paper-muted">{e.detail || <span className="text-paper-faint">(no detail)</span>}</span>
                </span>
                <span className="justify-self-end">
                  <span className="inline-block rounded border px-1.5 text-xs border-ink-line text-paper-muted">
                    {humanize(e.action)}
                  </span>
                </span>
                {e.response_count > 0 && (
                  <span className="justify-self-end hidden text-xs text-paper-muted md:block">
                    {e.response_count} resp
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
      {loading && <Loading what="Loading the audit log" />}
      {!loading && !error && entries.length === 0 && (
        <EmptyState title="No log entries yet">
          Entries appear here as members act on the server. Try selecting a different action, or check the timeline.
        </EmptyState>
      )}
      {more && !loading && (
        <button className="btn mt-4" onClick={() => load(entries.length)}>
          Load {PAGE} more
        </button>
      )}
    </>
  );
}
