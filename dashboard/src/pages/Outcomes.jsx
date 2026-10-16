import { useState } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../api/useApi.js";
import { EmptyState, ErrorNote, Loading, PageHeader } from "../components/ui.jsx";

// Bar colours per outcome. Text labels and titles carry the numbers too, so colour is never the only cue.
export const OUTCOME_STYLE = {
  success: { label: "success", bar: "bg-ok" },
  partial: { label: "partial", bar: "bg-warn/50" },
  reflex_stop: { label: "reflex stop", bar: "bg-warn" },
  stuck: { label: "stuck", bar: "bg-bad/50" },
  failed: { label: "failed", bar: "bg-bad" },
  abandoned: { label: "abandoned", bar: "bg-paper-faint" },
};
const ORDER = Object.keys(OUTCOME_STYLE);

function Bar({ group }) {
  return (
    <div className="flex h-3 w-full overflow-hidden rounded-sm bg-ink" role="img"
         aria-label={ORDER.filter((o) => group.outcomes[o]).map((o) => `${group.outcomes[o]} ${OUTCOME_STYLE[o].label}`).join(", ")}>
      {ORDER.filter((o) => group.outcomes[o]).map((o) => (
        <span key={o} className={OUTCOME_STYLE[o].bar}
              style={{ width: `${(group.outcomes[o] / group.total) * 100}%` }}
              title={`${OUTCOME_STYLE[o].label}: ${group.outcomes[o]}`} />
      ))}
    </div>
  );
}

export default function Outcomes() {
  const [period, setPeriod] = useState("30");
  const [agent, setAgent] = useState("");
  const [place, setPlace] = useState("");
  const [applied, setApplied] = useState({ agent: "", place: "" });
  const { data, error, loading, reload } = useApi("/admin/outcomes", { period_days: period, ...applied });
  const groups = data?.groups || [];

  return (
    <>
      <PageHeader title="Outcomes">
        <button className="btn" onClick={reload} disabled={loading}>Refresh</button>
      </PageHeader>
      <p className="mb-4 max-w-prose text-sm text-paper-muted">
        How each kind of action turns out, per member and place. Click a row to see those actions on the Timeline.
      </p>
      <form className="mb-4 flex flex-wrap items-end gap-2" aria-label="Filter outcomes"
            onSubmit={(e) => { e.preventDefault(); setApplied({ agent: agent.trim(), place: place.trim() }); }}>
        <select className="field" value={period} onChange={(e) => setPeriod(e.target.value)} aria-label="Period">
          <option value="7">Last 7 days</option>
          <option value="30">Last 30 days</option>
          <option value="90">Last 90 days</option>
        </select>
        <input className="field w-36" placeholder="Member" value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Member" />
        <input className="field w-36" placeholder="Place" value={place} onChange={(e) => setPlace(e.target.value)} aria-label="Place" />
        <button className="btn btn-primary">Filter</button>
      </form>

      <ul className="mb-4 flex flex-wrap gap-x-4 gap-y-1 text-xs text-paper-muted" aria-label="Legend">
        {ORDER.map((o) => (
          <li key={o} className="flex items-center gap-1.5">
            <span className={`inline-block h-2.5 w-2.5 rounded-sm ${OUTCOME_STYLE[o].bar}`} aria-hidden="true" />
            {OUTCOME_STYLE[o].label}
          </li>
        ))}
      </ul>

      {loading && !data && <Loading />}
      <ErrorNote error={error} onRetry={reload} />
      {data && groups.length === 0 && (
        <EmptyState title="No finished actions in this period">
          Outcomes come from action records that carry an outcome (success, reflex stop, …). Try a longer period.
        </EmptyState>
      )}
      {groups.length > 0 && (
        <ul className="panel" aria-label="Outcome groups, most frequent first">
          {groups.map((g) => {
            const q = new URLSearchParams({ agent: g.agent, type: "action", ...(g.place ? { place: g.place } : {}) });
            return (
              <li key={`${g.agent}|${g.action}|${g.place}`} className="border-b border-ink-line last:border-b-0">
                <Link to={`/?${q}`} className="grid grid-cols-1 items-center gap-x-4 gap-y-1.5 px-4 py-3 hover:bg-ink-hover md:grid-cols-[14rem_1fr_7rem]">
                  <span className="min-w-0">
                    <span className="block truncate font-bold">{g.action || "(no summary)"}</span>
                    <span className="block truncate text-xs text-paper-muted">{g.agent}{g.place ? `, ${g.place}` : ""}</span>
                  </span>
                  <Bar group={g} />
                  <span className="text-sm md:text-right">
                    {g.success_rate === null ? "–" : `${Math.round(g.success_rate * 100)}% success`}
                    <span className="block text-xs text-paper-muted">{g.total} actions</span>
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}
