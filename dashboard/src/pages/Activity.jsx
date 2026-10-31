// Activity page (issue #75): swarm activity overview for the research dashboard.
// Data: GET /admin/activity?hours=<1|24|168|720>, grouped by swarm (busyest first),
// the free-agent group reported as key null and titled "Free agents".
import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useApi } from "../api/useApi.js";
import { EmptyState, ErrorNote, Loading, PageHeader } from "../components/ui.jsx";
import { Sparkline, StackedBar, TypeLegend } from "../components/charts.jsx";
import { isSurge, trend } from "../lib/activity.js";

const WINDOWS = [
  { label: "1h", hours: 1 },
  { label: "24h", hours: 24 },
  { label: "7d", hours: 168 },
  { label: "30d", hours: 720 },
];
const FREE_AGENTS = "Free agents";
const REFRESH_MS = 30_000;
const PING_MS = 2_000;

function keyOf(group) {
  return group.key === null ? FREE_AGENTS : String(group.key);
}

// When telemetry is hidden, subtract each group's telemetry count from its
// displayed total and from by_type.perception (telemetry records are perceptions).
function withoutTelemetry(group) {
  const tele = group.telemetry || 0;
  const byType = { ...group.by_type };
  byType.perception = Math.max(0, (byType.perception || 0) - tele);
  return { ...group, total: Math.max(0, group.total - tele), by_type: byType };
}

function trendArrow(direction) {
  if (direction === "up") return "▲";
  if (direction === "down") return "▼";
  return "▬"; // flat or new
}

export default function Activity() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const [includeTelemetry, setIncludeTelemetry] = useState(true);
  const [live, setLive] = useState(false);
  const [typeFilter, setTypeFilter] = useState(null);
  const [pings, setPings] = useState({}); // card key -> timestamp of the last new-activity ping
  const prevTotals = useRef({});
  const [reducedMotion, setReducedMotion] = useState(false);

  const requested = Number(params.get("hours"));
  const hours = Number.isFinite(requested) && requested >= 1 && requested <= 720 ? requested : 24;

  const { data, error, loading, reload } = useApi("/admin/activity", { params: { hours } });
  const groups = (data?.groups || []).map((g) =>
    includeTelemetry ? g : withoutTelemetry(g)
  );

  // Summed counts across every group, shown in the type legend (raw counts,
  // not the telemetry-adjusted card totals).
  const summed = {};
  for (const g of data?.groups || []) {
    for (const [type, count] of Object.entries(g.by_type || {})) {
      summed[type] = (summed[type] || 0) + count;
    }
  }

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return undefined;
    const mql = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReducedMotion(mql.matches);
    const handler = () => setReducedMotion(mql.matches);
    mql.addEventListener("change", handler);
    return () => mql.removeEventListener("change", handler);
  }, []);

  // New-activity cue: in Live mode, ping any card whose total grew since the
  // previous fetch. The ring is removed after PING_MS by the expiry interval
  // below, and never added under prefers-reduced-motion: reduce.
  useEffect(() => {
    if (!live || !data) return;
    const now = {};
    const justPinged = [];
    for (const g of data.groups) {
      const k = keyOf(g);
      now[k] = g.total;
      const prev = prevTotals.current[k];
      if (prev !== undefined && g.total > prev && !reducedMotion) justPinged.push(k);
    }
    prevTotals.current = now;
    if (justPinged.length) {
      setPings((cur) => {
        const next = { ...cur };
        for (const k of justPinged) next[k] = Date.now();
        return next;
      });
    }
  }, [data, live, reducedMotion]);

  useEffect(() => {
    const id = setInterval(() => {
      const now = Date.now();
      setPings((cur) => {
        const next = {};
        let changed = false;
        for (const [k, t] of Object.entries(cur)) {
          if (now - t < PING_MS) next[k] = t;
          else changed = true;
        }
        return changed ? next : cur;
      });
    }, 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (!live) return undefined;
    const id = setInterval(() => reload(), REFRESH_MS);
    return () => clearInterval(id);
  }, [live, reload]);

  const highlighted = new Set(
    Object.keys(pings).filter((k) => Date.now() - pings[k] < PING_MS)
  );

  function pickWindow(h) {
    setParams({ hours: String(h) });
  }

  function toggleType(type) {
    setTypeFilter((current) => (current === type ? null : type));
  }

  function onSegment(type) {
    // Drill-down (4/4) is not landed yet: link to the timeline filtered to the type.
    navigate(`/timeline?type=${encodeURIComponent(type)}`);
  }

  if (loading && !data) return <Loading what="Loading activity" />;
  if (error) return <ErrorNote error={error} onRetry={reload} />;

  return (
    <>
      <PageHeader title="Activity">
        <form
          onSubmit={(e) => e.preventDefault()}
          className="mb-4 flex flex-wrap items-end gap-3"
          aria-label="Activity controls"
        >
          {WINDOWS.map((w) => (
            <button
              key={w.hours}
              type="button"
              aria-pressed={hours === w.hours}
              onClick={() => pickWindow(w.hours)}
              className={`btn min-h-[44px] ${
                hours === w.hours ? "bg-ink-hover font-bold text-paper" : ""
              }`}
            >
              {w.label}
            </button>
          ))}
          <label className="flex items-center gap-2 text-sm text-paper-muted cursor-pointer">
            <input
              type="checkbox"
              checked={includeTelemetry}
              onChange={() => setIncludeTelemetry((x) => !x)}
              className="accent-led"
            />
            Include telemetry
          </label>
          <label className="flex items-center gap-2 text-sm text-paper-muted cursor-pointer">
            <input
              type="checkbox"
              checked={live}
              onChange={() => setLive((x) => !x)}
              className="accent-led"
            />
            Live — refetch every 30 s
          </label>
        </form>
      </PageHeader>

      <TypeLegend counts={summed} onType={toggleType} active={typeFilter} />

      {groups.length === 0 && (
        <EmptyState title="No activity in this window">
          Try a wider window (1h, 24h, 7d, 30d) or turn Live on.
        </EmptyState>
      )}

      <ul className="mt-6 flex flex-col gap-3" aria-label="Swarm activity">
        {groups.map((g) => {
          const key = keyOf(g);
          const surge = isSurge(g.total, g.previous_total);
          const t = trend(g.total, g.previous_total);
          return (
            <li
              key={key}
              className={`rounded-lg border p-3 ${surge ? "border-warn" : "border-ink-line"} ${
                highlighted.has(key) ? "ring-2 ring-led" : ""
              }`}
            >
              <button
                type="button"
                aria-disabled="true"
                className="flex flex-wrap justify-between items-baseline gap-2 px-3 py-2 text-sm rounded-md border border-transparent hover:bg-ink-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-led"
                onClick={() => {}}
              >
                <div>
                  <h3 className="text-base font-bold">{key}</h3>
                  <p className="text-sm text-paper-muted">
                    {g.total.toLocaleString()} records
                  </p>
                </div>
                <span className="text-sm font-mono">
                  {trendArrow(t.direction)}{" "}
                  {t.direction === "new" ? "new activity" : t.label}
                </span>
                {surge && (
                  <span className="rounded border border-warn bg-warn/20 px-2 py-0.5 text-xs font-bold text-warn">
                    Surge
                  </span>
                )}
              </button>
              <div className="mt-4 flex flex-col md:flex-row md:items-center gap-4">
                <div className="flex-1">
                  <Sparkline series={g.series} label={`${key}: records per bucket`} />
                </div>
                <div className="flex-1">
                  <StackedBar
                    byType={g.by_type}
                    onSegment={onSegment}
                    active={typeFilter}
                    label={`${key} by record type`}
                  />
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </>
  );
}
