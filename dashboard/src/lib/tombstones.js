// Helpers for deleted records and their tombstones (issue #174).
import { localTime } from "./format.js";

// "agent:seq" -> { agent, seq }. Agent names may contain "." and "-", never ":".
export function splitRef(ref) {
  const at = ref.lastIndexOf(":");
  return { agent: ref.slice(0, at), seq: ref.slice(at + 1) };
}

// "Deleted by {admin} on {date}: {reason}"
export function tombstoneText(t) {
  const by = String(t.deleted_by || "an admin").replace(/^admin:/, "");
  return `Deleted by ${by} on ${localTime(t.deleted_at)}${t.reason ? `: ${t.reason}` : "."}`;
}

/**
 * The seq range to ask for one timeline page's tombstones (issue #205), as
 * query params for /admin/tombstones, or null when the page needs none.
 *
 * - The first page has no upper bound: the timeline has no end date, so a
 *   deletion newer than the newest record belongs at the top.
 * - A later page stops just below the lowest seq already loaded, so a
 *   deletion between two pages is found once.
 * - The last page has no lower bound unless a date filter (`since`) set one;
 *   with `since`, the page's lowest seq is the bound.
 *
 * `page` is this page's records; `prevLowest` is the lowest seq of the
 * member's records on earlier pages (null for the first page).
 */
export function tombstoneRange(page, agent, { first, last, since, prevLowest }) {
  const seqs = page.filter((r) => r.agent === agent).map((r) => Number(r.seq));
  if (seqs.length === 0 && !(first && last && !since)) return null;
  const params = { agent };
  if (!first && prevLowest != null) params.seq_to = prevLowest - 1;
  if (seqs.length > 0 && !(last && !since)) params.seq_from = Math.min(...seqs);
  if (params.seq_from != null && params.seq_to != null && params.seq_from > params.seq_to) return null;
  return params;
}

/** Merge tombstone lists by seq, newest first, without duplicates. */
export function mergeTombstones(a, b) {
  const bySeq = new Map([...a, ...b].map((t) => [t.seq, t]));
  return [...bySeq.values()].sort((x, y) => y.seq - x.seq);
}
