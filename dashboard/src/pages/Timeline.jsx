import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client.js";
import RecordRow from "../components/RecordRow.jsx";
import { EmptyState, ErrorNote, Loading, PageHeader } from "../components/ui.jsx";

const TYPES = ["communication", "decision", "action", "event", "perception", "lesson", "policy", "system"];
const OUTCOMES = ["success", "partial", "reflex_stop", "stuck", "abandoned", "failed"];
const PAGE = 50;
const FILTERS = ["agent", "type", "outcome", "place", "q"];

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

  async function load(offset = 0) {
    setLoading(true);
    setError(null);
    try {
      const res = await api("/admin/timeline", { params: { ...filters, limit: PAGE, offset } });
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
        <select className="field" value={draft.type} onChange={(e) => setDraft({ ...draft, type: e.target.value })} aria-label="Type">
          <option value="">Any type</option>
          {TYPES.map((t) => <option key={t}>{t}</option>)}
        </select>
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
