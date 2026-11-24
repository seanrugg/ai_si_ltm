// Fleet Health (#381, under Analyze): one row per active member, grouped by
// swarm with free agents last. Reads only what's already recorded
// (GET /api/admin/health); heartbeats and machine health are self-reports
// (#348) and are labelled as such.
import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useApi } from "../api/useApi.js";
import PingButton from "../components/PingButton.jsx";
import { EmptyState, ErrorNote, Loading, PageHeader } from "../components/ui.jsx";
import HelpTip from "../components/HelpTip.jsx";
import { localTime, relativeTime } from "../lib/format.js";
import {
  FILTERS, FREE, SORTS, STATUS_TEXT, filterRows, groupRows, healthSummary, intervalText, sortRows, statusClass,
} from "../lib/fleetHealth.js";
import { FLEET_HEALTH_ABOUT } from "../lib/help.js";

const profileOf = (name, tab) => `/members/${encodeURIComponent(name)}/profile?tab=${tab}`;

function Row({ r }) {
  const health = healthSummary(r.health);
  const interval = intervalText(r.heartbeat?.interval_s);
  return (
    <tr className="border-t border-ink-line align-top">
      <th scope="row" className="px-3 py-2 text-left font-normal">
        <Link to={profileOf(r.name, "senses")} className="font-bold hover:underline">{r.name}</Link>
        <div className="text-xs text-paper-muted">{r.kind}{r.role && r.role !== "hub" && r.role !== "member" ? ` · ${r.role}` : ""}</div>
      </th>
      <td className="px-3 py-2">
        {r.last_heard
          ? <time dateTime={r.last_heard} title={localTime(r.last_heard)}>{relativeTime(r.last_heard)}</time>
          : <span className="text-paper-muted">never</span>}
      </td>
      <td className="px-3 py-2">
        <span className={`font-bold ${statusClass(r.status)}`}>{STATUS_TEXT[r.status] ?? r.status}</span>
        <div className="text-xs text-paper-muted">{interval ? `Heartbeat ${interval} (self-report)` : r.status === "idle" ? "Quiet is normal: it runs only when used" : "No heartbeat interval yet"}</div>
      </td>
      <td className="px-3 py-2">
        {health
          ? <>{health}<div className="text-xs text-paper-muted">self-report{r.health.ts ? `, ${relativeTime(r.health.ts)}` : ""}</div></>
          : <span className="text-paper-muted">Not reported</span>}
      </td>
      <td className="px-3 py-2">
        {r.spec_summary
          ? <Link to={profileOf(r.name, "specs")} className="hover:underline">{r.spec_summary}</Link>
          : <span className="text-paper-muted">No spec sheet</span>}
      </td>
      <td className="px-3 py-2"><PingButton name={r.name} initial={r.ping ?? null} /></td>
    </tr>
  );
}

export default function Health() {
  const [params, setParams] = useSearchParams();
  const filter = params.get("show") || "all";
  const swarm = params.get("swarm") || "";
  const sort = params.get("sort") || "name";
  const setParam = (key, value, dflt) => setParams((prev) => {
    const next = new URLSearchParams(prev);
    if (value && value !== dflt) next.set(key, value);
    else next.delete(key);
    return next;
  }, { replace: true });

  const { data, error, loading, reload } = useApi("/admin/health");
  const rows = useMemo(() => data?.members ?? [], [data]);
  const swarms = useMemo(() => [...new Set(rows.map((r) => r.swarm).filter(Boolean))].sort(), [rows]);
  const groups = useMemo(() => groupRows(sortRows(filterRows(rows, filter, swarm), sort)), [rows, filter, swarm, sort]);
  const shown = groups.reduce((n, g) => n + g.rows.length, 0);

  return (
    <>
      <PageHeader title="Health" help={<HelpTip id="fleet-health-about" label="About this page">{FLEET_HEALTH_ABOUT}</HelpTip>}>
        <button type="button" className="btn" onClick={reload}>Refresh</button>
      </PageHeader>
      <div className="mb-4 flex flex-wrap items-center gap-3 text-sm">
        <span role="group" aria-label="Show" className="flex gap-1">
          {FILTERS.map((f) => (
            <button key={f.key} type="button" aria-pressed={filter === f.key} onClick={() => setParam("show", f.key, "all")}
                    className={`rounded-full border px-3 py-1 ${filter === f.key
                      ? "border-led text-paper" : "border-ink-line text-paper-muted hover:text-paper"}`}>
              {f.label}
            </button>
          ))}
        </span>
        <label className="flex items-center gap-2">
          Swarm
          <select className="field" value={swarm} onChange={(e) => setParam("swarm", e.target.value, "")}>
            <option value="">All swarms</option>
            {swarms.map((s) => <option key={s} value={s}>{s}</option>)}
            <option value={FREE}>Free agents</option>
          </select>
        </label>
        <label className="flex items-center gap-2">
          Sort by
          <select className="field" value={sort} onChange={(e) => setParam("sort", e.target.value, "name")}>
            {SORTS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        </label>
      </div>
      <ErrorNote error={error} onRetry={reload} />
      {loading && !data && <Loading what="Loading fleet health" />}
      {data && rows.length === 0 && <EmptyState title="No active members">Approved members appear here.</EmptyState>}
      {data && rows.length > 0 && shown === 0 && <p className="text-sm text-paper-muted">No members match these filters.</p>}
      {groups.map((g) => (
        <section key={g.swarm ?? FREE} className="card mb-4 overflow-x-auto">
          <table className="w-full text-sm">
            <caption className="px-3 py-2 text-left font-bold">
              {g.swarm ? `Swarm ${g.swarm}` : "Free agents"} ({g.rows.length})
            </caption>
            <thead className="text-xs text-paper-muted">
              <tr>
                <th scope="col" className="px-3 py-2 text-left">Member</th>
                <th scope="col" className="px-3 py-2 text-left">Last heard</th>
                <th scope="col" className="px-3 py-2 text-left">Status</th>
                <th scope="col" className="px-3 py-2 text-left">Health</th>
                <th scope="col" className="px-3 py-2 text-left">Specs</th>
                <th scope="col" className="px-3 py-2 text-left">Ping</th>
              </tr>
            </thead>
            <tbody>{g.rows.map((r) => <Row key={r.id} r={r} />)}</tbody>
          </table>
        </section>
      ))}
    </>
  );
}
