import { Link } from "react-router-dom";
import { useApi } from "../api/useApi.js";
import { EmptyState, ErrorNote, Loading, PageHeader, StatusBadge } from "../components/ui.jsx";

const EXPLAIN = {
  gap: "A record is missing from the chain.",
  hash: "Stored header bytes no longer match the record's hash: the record was edited.",
  sig: "The signature does not verify with the member's key.",
  chain: "A record does not link to the one before it.",
  column_mismatch: "A database column was edited after signing (someone bypassed the append-only guard).",
  payload_hash: "A stored payload was edited after signing.",
  head: "The member's chain head doesn't match its last stored record.",
};

export default function Integrity() {
  const { data, error, loading, reload } = useApi("/admin/integrity");
  const reports = data?.members || [];
  const broken = reports.filter((r) => r.status === "broken");

  return (
    <>
      <PageHeader title="Integrity">
        <button className="btn" onClick={reload} disabled={loading}>
          {loading ? "Checking…" : "Check again"}
        </button>
      </PageHeader>
      <p className="mb-4 max-w-prose text-sm text-paper-muted">
        Re-verifies every member's chain from the stored, signed bytes: each hash, each signature, each link. Purged
        payloads are expected and don't count against the chain.
      </p>
      {loading && !data && <Loading what="Verifying every chain" />}
      <ErrorNote error={error} onRetry={reload} />
      {data && reports.length === 0 && <EmptyState title="No members with records yet" />}
      {data && reports.length > 0 && (
        <>
          <p className={`mb-4 text-lg ${broken.length ? "text-bad" : "text-ok"}`} role="status">
            {broken.length
              ? `${broken.length} of ${reports.length} chains need attention.`
              : `All ${reports.length} chains verify.`}
          </p>
          <ul className="panel">
            {reports.map((r) => (
              <li key={r.member_id} className="border-b border-ink-line px-4 py-3 last:border-b-0">
                <div className="grid grid-cols-1 items-baseline gap-2 md:grid-cols-[12rem_6rem_1fr]">
                  <Link to={`/?agent=${encodeURIComponent(r.member)}`} className="font-bold hover:underline">{r.member}</Link>
                  <span><StatusBadge status={r.status} /></span>
                  <span className="text-sm text-paper-muted">
                    {r.records} records verified
                    {r.payloads_purged > 0 && `, ${r.payloads_purged} payloads purged by retention`}
                  </span>
                </div>
                {r.error && (
                  <div className="mt-2 rounded-md border border-bad/40 bg-bad/5 p-3 text-sm">
                    <p>
                      At <Link className="underline" to={`/record/${r.member}/${r.error.seq}`}>#{r.error.seq}</Link>:{" "}
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
