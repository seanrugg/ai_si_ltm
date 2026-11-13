import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client.js";
import RecordRow, { TombstoneRow } from "../components/RecordRow.jsx";
import TypePicker from "../components/TypePicker.jsx";
import { EmptyState, ErrorNote, Loading, PageHeader } from "../components/ui.jsx";
import { localTime } from "../lib/format.js";
import { LABELS, parseTypes } from "../lib/typeFilter.js";
import { mergeTombstones, tombstoneRange } from "../lib/tombstones.js";

const OUTCOMES = ["success", "partial", "reflex_stop", "stuck", "abandoned", "failed"];
const PAGE = 50;
const FILTERS = ["agent", "type", "outcome", "place", "q", "since", "swarm"];
// Text fields apply after typing stops; selects and the Types picker apply at once.
const TEXT_APPLY_DELAY_MS = 400;

export default function Timeline() {
  const [params, setParams] = useSearchParams();
  const filters = Object.fromEntries(FILTERS.map((k) => [k, params.get(k) || ""]));
  const filterKey = JSON.stringify(filters);

  const [records, setRecords] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [more, setMore] = useState(false);
  const [auto, setAuto] = useState(false);

  function setParam(key, value) {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(key, value);
      else next.delete(key);
      return next;
    });
  }

  // A member's own timeline (the Member filter, with or without a date):
  // deleted records show as tombstones where their seq falls (#174). One
  // request per page lists the member's tombstones in that page's seq range
  // (#205). The date filter (`since`) does NOT turn this off: a gap between
  // two records inside the date range is inside the range too, so it is a
  // deletion. Type, outcome, place and summary filters do turn it off, since
  // their gaps are filtered-out records, not deletions.
  // Tombstones have no swarm (#330), so a swarm filter turns them off too.
  const showTombstones = Boolean(filters.agent) && !filters.type && !filters.outcome && !filters.place && !filters.q
    && !filters.swarm;
  const [tombstones, setTombstones] = useState([]);
  const lowestSeq = useRef(null);

  async function loadTombstones(page, offset, last) {
    if (!showTombstones) {
      setTombstones([]);
      return;
    }
    const first = offset === 0;
    const own = page.filter((r) => r.agent === filters.agent).map((r) => Number(r.seq));
    const params = tombstoneRange(page, filters.agent, {
      first, last, since: filters.since, prevLowest: first ? null : lowestSeq.current,
    });
    if (own.length > 0) {
      lowestSeq.current = Math.min(...own, ...(first || lowestSeq.current == null ? [] : [lowestSeq.current]));
    } else if (first) {
      lowestSeq.current = null;
    }
    let found = [];
    if (params) {
      try {
        found = (await api("/admin/tombstones", { params })).tombstones || [];
      } catch {
        found = []; // tombstones are a nicety; the records still show
      }
    }
    setTombstones((prev) => (first ? found : mergeTombstones(prev, found)));
  }

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
        },
      });
      const last = res.records.length < PAGE;
      setRecords((prev) => (offset ? [...prev, ...res.records] : res.records));
      setMore(!last);
      await loadTombstones(res.records, offset, last);
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

  useEffect(() => {
    if (!auto) return undefined;
    const id = setInterval(() => load(0), 15000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auto, filterKey]);

  // Text fields (member, place, summary) hold local drafts and apply 400 ms
  // after typing stops. A URL change (chips, Clear all, reload) re-syncs them.
  const [drafts, setDrafts] = useState(null);
  const applyTimer = useRef(null);
  useEffect(() => {
    setDrafts(null);
    return () => clearTimeout(applyTimer.current);
  }, [filterKey]);
  const textValue = (key) => (drafts ? drafts[key] ?? "" : filters[key]);
  function onTextChange(e, key) {
    const value = e.target.value;
    setDrafts((d) => ({ ...(d || {}), [key]: value }));
    clearTimeout(applyTimer.current);
    applyTimer.current = setTimeout(() => setParam(key, value), TEXT_APPLY_DELAY_MS);
  }

  // Active filters become chips under the filter row; the default type
  // selection (everything except sensed, i.e. no `type` param) shows no chip.
  const active = [];
  if (filters.type) {
    const types = [...parseTypes(filters.type)];
    active.push({ key: "type", label: `Type: ${types.length ? types.map((t) => LABELS[t]).join(", ") : "none"}` });
  }
  if (filters.swarm) active.push({ key: "swarm", label: `Swarm: ${filters.swarm}` });
  if (filters.agent) active.push({ key: "agent", label: `Member: ${filters.agent}` });
  if (filters.outcome) active.push({ key: "outcome", label: `Outcome: ${filters.outcome.replace("_", " ")}` });
  if (filters.place) active.push({ key: "place", label: `Place: ${filters.place}` });
  if (filters.q) active.push({ key: "q", label: `Summary contains: ${filters.q}` });
  if (filters.since) active.push({ key: "since", label: `Since: ${localTime(filters.since)}` });

  // Tombstones go where their seq falls; the list is newest first.
  const rows = [...records];
  for (const t of tombstones) {
    const at = rows.findIndex((r) => r.agent === t.agent && Number(r.seq) < t.seq);
    rows.splice(at === -1 ? rows.length : at, 0, t);
  }

  const countText = records.length === 0
    ? "No records match"
    : records.length === 1
      ? "1 record"
      : `${records.length} records`;

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

      <form className="mb-2 flex flex-wrap items-center gap-2" aria-label="Filter records"
            onSubmit={(e) => e.preventDefault()}>
        <input className="field w-36" placeholder="Member" value={textValue("agent")}
               onChange={(e) => onTextChange(e, "agent")} aria-label="Member" />
        <select className="field" value={filters.outcome}
                onChange={(e) => setParam("outcome", e.target.value)} aria-label="Outcome">
          <option value="">Any outcome</option>
          {OUTCOMES.map((o) => <option key={o} value={o}>{o.replace("_", " ")}</option>)}
        </select>
        <input className="field w-36" placeholder="Place" value={textValue("place")}
               onChange={(e) => onTextChange(e, "place")} aria-label="Place" />
        <input className="field w-48" placeholder="Summary contains" value={textValue("q")}
               onChange={(e) => onTextChange(e, "q")} aria-label="Summary contains" />
        <TypePicker value={filters.type} onChange={(raw) => setParam("type", raw)} />
      </form>

      {active.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-2" role="group" aria-label="Active filters">
          {active.map(({ key, label }) => (
            <button key={key} type="button"
                    className="flex items-center gap-1.5 rounded-full border border-ink-line bg-ink-raised px-3 py-1.5 text-sm hover:bg-ink-hover"
                    aria-label={`Remove filter ${label}`}
                    onClick={() => setParam(key, "")}>
              {label}
              <span aria-hidden="true" className="text-paper-muted">&times;</span>
            </button>
          ))}
          <button type="button" className="btn" onClick={() => setParams({})}>
            Clear all
          </button>
        </div>
      )}

      {filters.swarm && (
        <p className="mb-2 text-xs text-paper-muted">Deleted records aren&apos;t shown when filtering by swarm.</p>
      )}
      {!loading && !error && (
        <p className="mb-2 text-sm text-paper-muted" role="status">{countText}</p>
      )}

      <ErrorNote error={error} onRetry={() => load(0)} />
      {rows.length > 0 && (
        <ul className="panel" aria-label="Records, newest first">
          {rows.map((r) => (r.deleted
            ? <TombstoneRow key={`deleted-${r.hash}`} tombstone={r} />
            : <RecordRow key={r.hash} record={r} />))}
        </ul>
      )}
      {loading && <Loading what="Loading records" />}
      {!loading && !error && records.length === 0 && (
        <EmptyState title={active.length > 0 ? "No records match these filters" : "No records yet"}>
          {active.length > 0
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
