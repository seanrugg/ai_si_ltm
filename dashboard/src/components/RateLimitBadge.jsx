// Ingest rate limits (#438). Two separate facts about a member:
// - it has its own limit (members.ingest_rate), overriding the server default;
// - the server recently refused its writes with a 429 (in-process, so it resets
//   when the server restarts). Refused records stay in the member's outbox and
//   are sent again later; nothing is lost.
import { relativeTime } from "../lib/format.js";

// A refusal older than this is history, not a current problem.
export const REFUSED_RECENT_MS = 10 * 60 * 1000;

export function rateLimitNote(rate) {
  return `Limited to ${rate} records per second, instead of the server default.`;
}

export function refusedNote(iso, now) {
  return `The server last slowed this member's writes ${relativeTime(iso, now)}. ` +
    "Its records wait in its outbox and are sent when allowed.";
}

export default function RateLimitBadge({ rate, refusedAt, now = Date.now() }) {
  const recent = refusedAt && now - new Date(refusedAt).getTime() < REFUSED_RECENT_MS;
  if (rate == null && !recent) return null;
  return (
    <>
      {rate != null && (
        <span className="inline-flex items-center gap-1 rounded-full border border-led/60 px-2 py-0.5 text-xs text-led"
              title={rateLimitNote(rate)}>
          Limit {rate}/s<span className="sr-only">: {rateLimitNote(rate)}</span>
        </span>
      )}
      {recent && (
        <span className="inline-flex items-center gap-1 rounded-full border border-warn/60 px-2 py-0.5 text-xs text-warn"
              title={refusedNote(refusedAt, now)}>
          Rate limited<span className="sr-only">: {refusedNote(refusedAt, now)}</span>
        </span>
      )}
    </>
  );
}
