// Activity page (issue #75): swarm activity overview for the research dashboard.
// Data: GET /admin/activity?hours=<1|24|168|720>, grouped by swarm (busiest first),
// the free-agent group reported as key null and titled "Free agents".
import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useApi } from "../api/useApi.js";
import { EmptyState, ErrorNote, Loading, PageHeader } from "../components/ui.jsx";
import { Sparkline, StackedBar, TypeLegend } from "../components/charts.jsx";
import { isSurge, trend, typeColor, TYPE_ORDER } from "../lib/activity.js";
import { localTime } from "../lib/format.js";

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
  return {
    ...group,
    total: Math.max(0, group.total - tele),
    by_type: byType,
    // Drop the per-bucket telemetry count so the sparkline matches the bars.
    series: group.series.map((n, i) => Math.max(0, n - (group.telemetry_series?.[i] || 0))),
  };
}

function trendArrow(direction) {
  if (direction === "up") return "▲";
  if (direction === "down") return "▼";
  return "▬"; // flat or new
}

export default function Activity() {
  const [params, setParams] = useSearchParams();
  const [includeTelemetry, setIncludeTelemetry] = useState(true);
  const [live, setLive] = useState(false);
  const [typeFilter, setTypeFilter] = useState(null);
  const [pings, setPings] = useState({}); // card key -> timestamp of the last new-activity ping
  const prevTotals = useRef({});
  const [reducedMotion, setReducedMotion] = useState(false);
  const [openGroup, setOpenGroup] = useState(null); // key of the drill-down panel open below the cards
  const cardRefs = useRef({}); // card key -> its header button, to return focus to on close

  const requested = Number(params.get("hours"));
  const hours = Number.isFinite(requested) && requested >= 1 && requested <= 720 ? requested : 24;

  const { data, error, loading, reload } = useApi("/admin/activity", { params: { hours } });
  const groups = (data?.groups || []).map((g) =>
    includeTelemetry ? g : withoutTelemetry(g)
  );
  const openGroupData = openGroup ? groups.find((g) => keyOf(g) === openGroup) : undefined;

  // Summed counts across every group for the type legend, with the same
  // telemetry setting as the cards.
  const summed = {};
  for (const g of groups) {
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

  function onSegment(groupKey, type) {
    // A segment click opens the drill-down for that group with the type highlighted.
    setTypeFilter(type);
    setOpenGroup(groupKey);
  }

  function closeDrill(groupKey) {
    setOpenGroup(null);
    cardRefs.current[groupKey]?.focus();
  }

  function clearTypeFilter() {
    setTypeFilter(null);
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
              <div className="flex flex-wrap items-baseline justify-between gap-2 px-3 py-2">
                <div>
                  <h3>
                    <button
                      type="button"
                      aria-expanded={openGroup === key}
                      aria-controls="drill-panel"
                      onClick={() => setOpenGroup(openGroup === key ? null : key)}
                      ref={(el) => (cardRefs.current[key] = el)}
                      className="text-base font-bold text-paper hover:underline bg-transparent border-none p-0 m-0 flex items-center cursor-pointer"
                    >
                      {key}
                    </button>
                  </h3>
                  <p className="text-sm text-paper-muted">{g.total.toLocaleString()} records</p>
                </div>
                <p className="font-mono text-sm">
                  <span aria-hidden="true">{trendArrow(t.direction)} </span>
                  {t.label}
                </p>
                {surge && (
                  <span className="rounded border border-warn bg-warn/20 px-2 py-0.5 text-xs font-bold text-warn">
                    Surge
                  </span>
                )}
              </div>
              <div className="mt-4 flex flex-col md:flex-row md:items-center gap-4">
                <div className="flex-1">
                  <Sparkline series={g.series} label={`${key}: records per bucket`} />
                </div>
                <div className="flex-1">
                  <StackedBar
                    byType={g.by_type}
                    onSegment={(type) => onSegment(key, type)}
                    active={typeFilter}
                    label={`${key} by record type`}
                  />
                </div>
              </div>
            </li>
          );
        })}
      </ul>

    {openGroupData && (
      <DrillPanel
        group={openGroupData}
        hours={hours}
        since={data?.since}
        bucketSeconds={data?.bucket_seconds}
        activeType={typeFilter}
        includeTelemetry={includeTelemetry}
        onClose={closeDrill}
        onClear={clearTypeFilter}
      />
    )}
    </>
  );
}


// Drill-down panel (issue #76): opens below one swarm card at a time. Esc or Close
// returns focus to the card's header button.
function DrillPanel({ group, hours, since, bucketSeconds, activeType, includeTelemetry, onClose, onClear }) {
  const key = keyOf(group);
  const navigate = useNavigate();
  const [tableView, setTableView] = useState(false);
  const { data, error, loading, reload } = useApi("/admin/activity", {
    params: { hours, group: "member", swarm: group.key ?? "none" },
  });
  const members = (data?.groups || [])
    .map((m) => (includeTelemetry ? m : withoutTelemetry(m)))
    .sort((a, b) => b.total - a.total);
  const total = members.reduce((sum, m) => sum + m.total, 0);

  // Types that actually appear, in the legend's group order.
  const typesPresent = new Set();
  for (const m of members) for (const t of Object.keys(m.by_type || {})) typesPresent.add(t);
  const showTypes = TYPE_ORDER.filter((t) => typesPresent.has(t));

  // Dismiss on Escape.
  useEffect(() => {
    const handler = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  if (loading && !data) return <Loading what="Loading members" />;
  if (error) return <ErrorNote error={error} onRetry={reload} />;

  return (
    <section id="drill-panel" className="mt-6 rounded-lg border border-ink-line bg-ink-raised p-4" aria-label="Drill-down">
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h2 className="text-base font-bold">{key}</h2>
          <p className="text-sm text-paper-muted">{members.length} members · {total.toLocaleString()} records</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {activeType && (
            <>
              <span className="text-xs text-paper-muted">Filtered by {activeType}</span>
              <button type="button" onClick={onClear} className="btn">
                Clear filter
              </button>
            </>
          )}
          <button type="button" onClick={() => onClose(key)} className="btn">
            Close
          </button>
          <button type="button" onClick={() => setTableView((v) => !v)} className="btn">
            {tableView ? "Show as chart" : "Show as table"}
          </button>
        </div>
      </div>

      {members.length === 0 ? (
        <EmptyState title="No members with activity in this window" />
      ) : tableView ? (
        <DrillTable members={members} showTypes={showTypes} />
      ) : (
        <>
          {/* Over time: stacked columns from the parent card's series_by_type. */}
          <div className="mb-4">
            <h3 className="text-sm font-bold text-paper-muted">Records over time</h3>
            <DrillOverTime
              seriesByType={group.series_by_type}
              since={since}
              bucketSeconds={bucketSeconds}
              types={showTypes}
            />
          </div>

          <ul className="mt-4 flex flex-col gap-2" aria-label={`Members in ${key}`}>
            {members.map((m) => {
              const t = trend(m.total, m.previous_total);
              return (
                <li key={m.key} className="flex flex-col md:flex-row md:items-center gap-3 p-2 rounded-lg border border-ink-line">
                  <div className="flex-1 min-w-0">
                    <button
                      type="button"
                      onClick={() => navigate(timelineUrl({ agent: m.key, since }))}
                      className="text-paper font-bold hover:underline"
                    >
                      {m.key}
                    </button>
                    <p className="text-sm text-paper-muted">
                      {m.total.toLocaleString()} records · {t.label}
                    </p>
                  </div>
                  <div className="flex-1">
                    <Sparkline series={m.series} label={`${m.key}: records per bucket`} />
                  </div>
                  <div className="flex-1">
                    <StackedBar
                      byType={m.by_type}
                      onSegment={(type) => navigate(timelineUrl({ agent: m.key, type, since }))}
                      active={activeType}
                      label={`${m.key} by record type`}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}

// Per-member rows as an HTML table (per-member x type counts), for screen-reader
// users and for copying.
function DrillTable({ members, showTypes }) {
  return (
    <table className="mt-4 w-full border border-ink-line border-collapse text-sm">
      <thead>
        <tr>
          <th scope="col" className="text-left border-b border-r border-ink-line bg-ink-raised p-2">
            Member
          </th>
          {showTypes.map((type) => (
            <th scope="col" key={type} className="text-left border-b border-r border-ink-line bg-ink-raised p-2 font-mono">
              {type}
            </th>
          ))}
          <th scope="col" className="text-left border-b border-ink-line bg-ink-raised p-2 font-mono">
            Total
          </th>
        </tr>
      </thead>
      <tbody>
        {members.map((m) => (
          <tr key={m.key} className="border-b border-ink-line">
            <th scope="row" className="border-r border-ink-line p-2 text-paper">
              {m.key}
            </th>
            {showTypes.map((type) => (
              <td key={type} className="border-r border-ink-line p-2 font-mono">
                {m.by_type[type] || 0}
              </td>
            ))}
            <td className="p-2 font-mono">{m.total.toLocaleString()}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// 24 stacked columns from the parent card's series_by_type; x labels at the
// first, middle and last bucket.
function DrillOverTime({ seriesByType, since, bucketSeconds, types }) {
  const width = 720;
  const height = 110;
  const colWidth = width / 24;
  const start = since ? Date.parse(since) : Date.now();
  const totals = [];
  let maxTotal = 0;
  for (let i = 0; i < 24; i++) {
    let total = 0;
    for (const type of types) total += (seriesByType[type] || [])[i] || 0;
    totals.push(total);
    maxTotal = Math.max(maxTotal, total);
  }
  const columns = [];
  for (let i = 0; i < 24; i++) {
    let y = height;
    const segs = [];
    for (const type of types) {
      const count = (seriesByType[type] || [])[i] || 0;
      if (!count) continue;
      const h = maxTotal ? (count / maxTotal) * height : 0;
      y -= h;
      segs.push(<rect key={type} x={1} y={y} width={colWidth - 2} height={h} fill={typeColor(type)} />);
    }
    const bucketStart = new Date(start + i * bucketSeconds * 1000);
    const bucketEnd = new Date(bucketStart.getTime() + bucketSeconds * 1000);
    columns.push(
      <g key={i} transform={`translate(${i * colWidth},0)`}>
        <title>{hhmm(bucketStart)}–{hhmm(bucketEnd)}: {totals[i]} records</title>
        {segs}
      </g>
    );
  }
  const labels = [0, 11, 23].map((i) => (
    <text
      key={i}
      x={i * colWidth + colWidth / 2}
      y={height + 22}
      textAnchor="middle"
      fontSize="11"
      fill="#93A0B8"
    >
      {localTime(new Date(start + i * bucketSeconds * 1000).toISOString())}
    </text>
  ));
  return (
    <svg viewBox={`0 0 ${width} ${height + 30}`} role="img" aria-label="Records over time" className="block w-full">
      {columns}
      {labels}
    </svg>
  );
}

function hhmm(date) {
  return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function timelineUrl(query) {
  const clean = Object.fromEntries(Object.entries(query).filter(([, v]) => v !== undefined && v !== ""));
  return `/timeline?${new URLSearchParams(clean).toString()}`;
}
