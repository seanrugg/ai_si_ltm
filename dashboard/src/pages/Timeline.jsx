import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client.js";
import RecordRow from "../components/RecordRow.jsx";
import { EmptyState, ErrorNote, Loading, PageHeader } from "../components/ui.jsx";
import { localTime } from "../lib/format.js";

const TYPES = ["communication", "thought", "decision", "action", "event", "perception", "lesson", "policy", "system"];
const OUTCOMES = ["success", "partial", "reflex_stop", "stuck", "abandoned", "failed"];
const PAGE = 50;
const FILTERS = ["agent", "type", "outcome", "place", "q", "since"];

export default function Timeline() {
  const [params, setParams] = useSearchParams();
  const filters = Object.fromEntries(FILTERS.map((k) => [k, params.get(k) || ""]));
  const filterKey = JSON.stringify(filters);

  const [records, setRecords] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [more, setMore] = useState(false);
  const [auto, setAuto] = useState(false);
  const [draft, setDraft] = useState(filters);

  function removeSince() {
    setParams((prev) => {
      prev.delete("since");
      return prev;
    });
  }
  // off by default: telemetry is hidden unless the box is checked
  const [showTelemetry, setShowTelemetry] = useState(false);

  async function load(offset = 0) {
    setLoading(true);
    setError(null);
    try {
      // The admin timeline endpoint takes the window start as the "from" query
      // param; the browser URL keeps it as "since" so drill-down links can share it.
      const { since, ...rest } = filters;
      const res = await api("/admin/timeline", {
        params: {
          ...rest,
          limit: PAGE,
          offset,
          ...(since ? { from: since } : {}),
          ...(showTelemetry ? {} : { exclude_tag: "telemetry" }),
        },
      });
      setRecords((prev) => (offset ? [...prev, ...res.records] : res.records));
      setMore(res.records.length === PAGE);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    setDraft(filters);
    load(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey]);

  useEffect(() => {
    if (!auto) return undefined;
    const id = setInterval(() => load(0), 15000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auto, filterKey]);

  function apply(e) {
    e.preventDefault();
    const next = {};
    for (const [k, v] of Object.entries(draft)) if (v) next[k] = v.trim();
    if (!showTelemetry) next.exclude_tag = "telemetry";
    setParams(next);
  }

  const filtered = FILTERS.some((k) => filters[k]);

  return (
    <>
      <PageHeader title="Timeline">
        <label className="flex items-center gap-2 text-sm text-paper-muted">
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} className="accent-led" />
          Refresh every 15 s
        </label>
        <button className="btn" onClick={() => load(0)} disabled={loading}>
          Refresh
        </button>
      </PageHeader>

      <form onSubmit={apply} className="mb-4 flex flex-wrap items-end gap-2" aria-label="Filter records">
        <input className="field w-36" placeholder="Member" value={draft.agent}
               onChange={(e) => setDraft({ ...draft, agent: e.target.value })} aria-label="Member" />
        <div className="flex flex-wrap gap-2 items-end" aria-label="Type">
          <span className="text-xs text-paper-muted">Type</span>
          {TYPES.map((t) => {
            const checked = (draft.type || "").split(",").includes(t);
            return (
              <label key={t} className="flex items-center gap-1 text-sm cursor-pointer">
                <input type="checkbox" checked={checked} className="accent-led"
                       onChange={(e) => {
                         const sel = (draft.type || "").split(",").filter(Boolean);
                         if (e.target.checked) sel.push(t); else sel.splice(sel.indexOf(t), 1);
                         setDraft({ ...draft, type: sel.join(",") });
                       }} />
                {t}
              </label>
            );
          })}
        </div>
        <label className="flex items-center gap-2 text-sm text-paper-muted cursor-pointer">
          <input type="checkbox" checked={showTelemetry} className="accent-led"
                 onChange={(e) => setShowTelemetry(e.target.checked)} />
          Show telemetry
        </label>
        <select className="field" value={draft.outcome} onChange={(e) => setDraft({ ...draft, outcome: e.target.value })} aria-label="Outcome">
          <option value="">Any outcome</option>
          {OUTCOMES.map((o) => <option key={o} value={o}>{o.replace("_", " ")}</option>)}
        </select>
        <input className="field w-36" placeholder="Place" value={draft.place}
               onChange={(e) => setDraft({ ...draft, place: e.target.value })} aria-label="Place" />
        <input className="field w-48" placeholder="Summary contains" value={draft.q}
               onChange={(e) => setDraft({ ...draft, q: e.target.value })} aria-label="Summary contains" />
        <button className="btn btn-primary">Filter</button>
        {filtered && (
          <button type="button" className="btn" onClick={() => setParams({})}>
            Clear
          </button>
        )}
        {filters.since && (
          <span className="flex items-center gap-2 rounded border border-ink-line bg-ink-raised px-2.5 py-1.5 text-xs">
            <span className="text-paper-muted">Since</span>
            <span className="font-mono">{localTime(filters.since)}</span>
            <button
              type="button"
              onClick={removeSince}
              className="rounded border border-ink-line p-1 hover:bg-ink-hover"
              aria-label="Remove since filter"
            >
              &times;
            </button>
          </span>
        )}
      </form>

      <ErrorNote error={error} onRetry={() => load(0)} />
      {records.length > 0 && (
        <ul className="panel" aria-label="Records, newest first">
          {records.map((r) => <RecordRow key={r.hash} record={r} />)}
        </ul>
      )}
      {loading && <Loading what="Loading records" />}
      {!loading && !error && records.length === 0 && (
        <EmptyState title={filtered ? "No records match these filters" : "No records yet"}>
          {filtered
            ? "Try clearing a filter."
            : "Records appear here once an approved member sends them. Approve a member on the Members page, then run `python -m cli_agent send`."}
        </EmptyState>
      )}
      {more && !loading && (
        <button className="btn mt-4" onClick={() => load(records.length)}>
          Load {PAGE} more
        </button>
      )}
    </>
  );
}
