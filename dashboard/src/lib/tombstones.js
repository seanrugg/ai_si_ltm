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

// The seqs missing between the lowest and highest seq in one member's records.
// Returns [] when more than `limit` are missing: that many is a filtered list,
// not a few deletions, and each seq costs a request to look up.
export function missingSeqs(records, limit = 20) {
  const seqs = [...new Set(records.map((r) => Number(r.seq)))].sort((a, b) => a - b);
  const missing = [];
  for (let i = 1; i < seqs.length; i += 1) {
    for (let s = seqs[i - 1] + 1; s < seqs[i]; s += 1) {
      missing.push(s);
      if (missing.length > limit) return [];
    }
  }
  return missing;
}
