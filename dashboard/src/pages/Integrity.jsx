import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import { useApi } from "../api/useApi.js";
import { EmptyState, ErrorNote, Loading, PageHeader, StatusBadge } from "../components/ui.jsx";
import { localTime, plural, relativeTime } from "../lib/format.js";
import RecordLink from "../components/RecordLink.jsx";

const EXPLAIN = {
  gap: "A record is missing from the chain.",
  hash: "Stored header bytes no longer match the record's hash: the record was edited.",
  sig: "The signature does not verify with the member's key.",
  chain: "A record does not link to the one before it.",
  column_mismatch: "A database column was edited after signing (someone bypassed the append-only guard).",
  payload_hash: "A stored payload was edited after signing.",
  head: "The member's chain head doesn't match its last stored record.",
};

const POLL_MS = 1000;

// One line about where a member's chain is verified to, and how (#439).
export function verifiedLine(r) {
  if (r.verified_through === undefined || r.verified_through === null) return "";
  const parts = [`verified through #${r.verified_through}`];
  if (r.head_seq !== undefined && r.head_seq !== null && r.verified_through < r.head_seq) {
    parts.push(`of #${r.head_seq}`);
  }
  if (r.verified_at) parts.push(relativeTime(r.verified_at));
  if (r.mode) parts.push(`(${r.mode})`);
  return parts.join(" ");
}

// The latest full check, as one sentence.
export function fullRunLine(run) {
  if (!run) return "No full check has run yet.";
  if (run.status === "running") {
    return `Full check running: ${run.members_done} of ${run.members_total} members, `
      + `${run.records_done.toLocaleString()} records so far.`;
  }
  if (run.status === "failed") return `The last full check failed (${localTime(run.started_at)}): ${run.error}`;
  const result = run.broken ? `${plural(run.broken, "chain")} broken` : "every chain verified";
  return `Last full check ${localTime(run.finished_at)}: ${plural(run.members_total, "member")}, `
    + `${run.records_done.toLocaleString()} records, ${result}.`;
}

export default function Integrity() {
  const { data, error, loading, reload } = useApi("/admin/integrity");
  const reports = data?.members || [];
  const broken = reports.filter((r) => r.status === "broken");
  const partial = reports.filter((r) => r.status === "partial");

  const [run, setRun] = useState(null);
  const [runError, setRunError] = useState(null);
  useEffect(() => {
    if (data && "full" in data) setRun(data.full);
  }, [data]);

  const running = run?.status === "running";
  useEffect(() => {
    if (!running) return undefined;
    const t = setInterval(async () => {
      try {
        const res = await api("/admin/integrity/full", { activity: false });
        setRun(res.run);
        if (res.run?.status !== "running") reload();
      } catch (e) {
        setRunError(e);
      }
    }, POLL_MS);
    return () => clearInterval(t);
  }, [running, reload]);

  const startFull = useCallback(async () => {
    setRunError(null);
    try {
      const res = await api("/admin/integrity/full", { method: "POST" });
      setRun(res.run);
    } catch (e) {
      setRunError(e);
    }
  }, []);

  return (
    <>
      <PageHeader title="Integrity">
        <button className="btn" onClick={reload} disabled={loading}>
          {loading ? "Checking…" : "Check again"}
        </button>
        <button className="btn" onClick={startFull} disabled={running}>
          {running ? "Full check running…" : "Full check"}
        </button>
      </PageHeader>
      <p className="mb-2 max-w-prose text-sm text-paper-muted">
        Re-verifies each member's chain from the stored, signed bytes: each hash, each signature, each link. Purged
        payloads are expected and don't count against the chain. Neither do records an admin deleted: each leaves a
        tombstone that keeps the chain linked.
      </p>
      <p className="mb-2 max-w-prose text-sm text-paper-muted">
        <strong>Check again</strong> verifies only records added since each member's last verified record, so it
        stays quick as history grows. <strong>Full check</strong> re-verifies every chain from the start in the
        background; it also catches edits made behind the database's guards to records already verified.
      </p>
      <p className="mb-4 text-sm" role="status" aria-live="polite">{fullRunLine(run)}</p>
      <ErrorNote error={runError} />
      {loading && !data && <Loading what="Verifying new records" />}
      <ErrorNote error={error} onRetry={reload} />
      {data && reports.length === 0 && <EmptyState title="No members with records yet" />}
      {data && reports.length > 0 && (
        <>
          <p className={`mb-4 text-lg ${broken.length ? "text-bad" : "text-ok"}`} role="status">
            {broken.length
              ? `${broken.length} of ${reports.length} chains need attention.`
              : `All ${reports.length} chains verify.`}
            {partial.length > 0 && (
              <span className="ml-2 text-sm text-paper-muted">
                {plural(partial.length, "chain")} still being caught up; check again to continue.
              </span>
            )}
          </p>
          <ul className="panel">
            {reports.map((r) => (
              <li key={r.member_id} className="border-b border-ink-line px-4 py-3 last:border-b-0">
                <div className="grid grid-cols-1 items-baseline gap-2 md:grid-cols-[12rem_6rem_1fr]">
                  <Link to={`/?agent=${encodeURIComponent(r.member)}`} className="font-bold hover:underline">{r.member}</Link>
                  <span><StatusBadge status={r.status} /></span>
                  <span className="text-sm text-paper-muted">
                    <span>
                      {r.records} records verified
                      {r.payloads_purged > 0 && `, ${r.payloads_purged} payloads purged`}
                      {r.deleted > 0 && `, ${plural(r.deleted, "record")} deleted (tombstones kept)`}
                    </span>
                    {verifiedLine(r) && <span className="block text-xs">{verifiedLine(r)}</span>}
                    {r.checkpoint_reset && (
                      <span className="block text-xs">Re-verified from the start: {r.checkpoint_reset}.</span>
                    )}
                  </span>
                </div>
                {r.error && (
                  <div className="mt-2 rounded-md border border-bad/40 bg-bad/5 p-3 text-sm">
                    <p>
                      At <RecordLink className="underline" agent={r.member} seq={r.error.seq}>#{r.error.seq}</RecordLink>:{" "}
                      {EXPLAIN[r.error.code] || r.error.message}
                    </p>
                    <p className="mt-1 text-xs text-paper-muted">{r.error.code}: {r.error.message}</p>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}
