// Overview's "Needs attention" panel (#380), from GET /api/admin/attention.
// Severity is in words ("Action needed"), not colour alone. Ping and Snooze
// buttons name their member or item (508). Snoozing hides an item for 24 h for
// this admin only; it doesn't resolve it.
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import { useApi } from "../api/useApi.js";
import { localTime, relativeTime } from "../lib/format.js";
import { getDirectivesSeen } from "../lib/nav.js";
import PingButton from "./PingButton.jsx";
import { ErrorNote, Loading } from "./ui.jsx";

export const SEVERITY_TEXT = { action: "Action needed", info: "For your information" };

function Item({ item, onSnooze }) {
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 border-t border-ink-line py-3 first:border-t-0">
      <div className="min-w-0">
        <p className="text-xs font-bold uppercase tracking-wide">
          <span className={item.severity === "action" ? "text-warn" : "text-paper-muted"}>
            {SEVERITY_TEXT[item.severity] ?? item.severity}
          </span>
          {item.since && (
            <span className="font-normal normal-case text-paper-muted">
              {" · since "}<time dateTime={item.since} title={localTime(item.since)}>{relativeTime(item.since)}</time>
            </span>
          )}
        </p>
        <h3 className="text-sm font-bold"><Link to={item.link} className="hover:underline">{item.title}</Link></h3>
        {item.reasons?.length > 0 && (
          <ul className="mt-1 list-disc pl-5 text-sm text-paper-muted">
            {item.reasons.map((r) => <li key={r}>{r}</li>)}
          </ul>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {item.ping && item.subject && <PingButton name={item.subject} />}
        <button type="button" className="btn" onClick={() => onSnooze(item)}
                aria-label={`Snooze for 24 hours: ${item.title}`}>
          Snooze 24 h
        </button>
      </div>
    </li>
  );
}

export default function NeedsAttention() {
  const { data, error, loading, reload } = useApi("/admin/attention", { directives_seen: getDirectivesSeen() });
  const [snoozeError, setSnoozeError] = useState(null);

  async function snooze(item) {
    setSnoozeError(null);
    try {
      await api("/admin/attention/snooze", { method: "POST", body: { key: item.key } });
      reload();
    } catch (err) {
      setSnoozeError(err);
    }
  }

  async function unsnooze(key) {
    setSnoozeError(null);
    try {
      await api(`/admin/attention/snooze/${encodeURIComponent(key)}`, { method: "DELETE" });
      reload();
    } catch (err) {
      setSnoozeError(err);
    }
  }

  const items = data?.items ?? [];
  const snoozed = data?.snoozed ?? [];
  return (
    <section aria-labelledby="needs-attention" className="card mb-6 p-4">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 id="needs-attention" className="text-base font-bold">Needs attention</h2>
        <button type="button" className="btn" onClick={reload}>Check again</button>
      </div>
      <ErrorNote error={error || snoozeError} onRetry={reload} />
      {loading && !data && <Loading what="Checking what needs you" />}
      {data && items.length === 0 && (
        <p className="text-sm">
          Nothing needs you right now.{" "}
          <span className="text-paper-muted">
            Checked <time dateTime={data.checked_at} title={localTime(data.checked_at)}>{relativeTime(data.checked_at)}</time>.
          </span>
        </p>
      )}
      {items.length > 0 && (
        <ul aria-label="Items that need attention">
          {items.map((i) => <Item key={i.key} item={i} onSnooze={snooze} />)}
        </ul>
      )}
      {snoozed.length > 0 && (
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer text-paper-muted">{snoozed.length} snoozed</summary>
          <ul className="mt-2 space-y-1">
            {snoozed.map((s) => (
              <li key={s.key} className="flex flex-wrap items-center gap-2">
                <span>{s.title}</span>
                <span className="text-paper-muted">until {localTime(s.until)}</span>
                <button type="button" className="btn" onClick={() => unsnooze(s.key)}
                        aria-label={`Show again now: ${s.title}`}>Show again</button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
