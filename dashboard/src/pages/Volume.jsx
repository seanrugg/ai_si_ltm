import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useApi } from "../api/useApi.js";
import HelpTip from "../components/HelpTip.jsx";
import { MSG_OVER_HALF, MSG_SUMMARY } from "../lib/help.js";
import { EmptyState, ErrorNote, Loading, PageHeader } from "../components/ui.jsx";

const DAYS = [7, 14, 30, 90];
const MB = 1_000_000;

function Bar({ value, max, title }) {
  const width = max ? (value / max) * 100 : 0;
  return (
    <div className="flex h-3 w-full overflow-hidden rounded-sm bg-ink" role="img" aria-label={title}>
      <span className="bg-led h-full" style={{ width: `${width}%` }} title={title} />
    </div>
  );
}

/** Swarm messages vs all records, per swarm (#302). */
export function SwarmMessages({ swarms }) {
  if (!swarms?.length) return null;
  return (
    <section className="panel mb-4 p-4" aria-labelledby="swarm-msgs-h">
      <h2 id="swarm-msgs-h" className="flex items-center gap-2 font-bold">
        Swarm messages
        <HelpTip id="msg-summary" label="About summary mode">{MSG_SUMMARY}</HelpTip>
      </h2>
      <ul className="mt-2 space-y-2 text-sm">
        {swarms.map((s) => (
          <li key={s.swarm}>
            <span className="font-bold">{s.swarm}</span>:{" "}
            {s.messages.toLocaleString()} of {s.records.toLocaleString()} records are messages ({Math.round(s.share * 100)}%)
            {s.summarised.length > 0 && (
              <span className="text-paper-muted"> · summarised for the LRS now: {s.summarised.join(", ")}</span>
            )}
            {s.days_over_half.length > 0 && (
              <p role="note" className="mt-1 text-warn">
                {MSG_OVER_HALF} ({s.days_over_half.length === 1 ? "1 day" : `${s.days_over_half.length} days`} in this range)
              </p>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function Volume() {
  const [days, setDays] = useState("14");
  const [params, setParams] = useSearchParams();
  const swarm = params.get("swarm") || "";
  const { data, error, loading, reload } = useApi("/admin/volume", swarm ? { days, swarm } : { days });
  const swarmChip = swarm && (
    <div className="mb-4 flex flex-wrap items-center gap-2" role="group" aria-label="Active filters">
      <button type="button" aria-label={`Remove filter Swarm: ${swarm}`}
              className="flex items-center gap-1.5 rounded-full border border-ink-line bg-ink-raised px-3 py-1.5 text-sm hover:bg-ink-hover"
              onClick={() => setParams((prev) => { const next = new URLSearchParams(prev); next.delete("swarm"); return next; })}>
        Swarm: {swarm}<span aria-hidden="true" className="text-paper-muted">&times;</span>
      </button>
    </div>
  );
  const rows = data?.rows || [];
  const period = data?.days || Number(days) || 1;

  if (loading && !data) return <Loading what="Loading volume" />;
  if (error) return <ErrorNote error={error} onRetry={reload} />;
  if (rows.length === 0) {
    return (
      <>
        <PageHeader title="Volume" />
        {swarmChip}
        <EmptyState title="No volume yet">
          Records appear here once approved members send them.
        </EmptyState>
      </>
    );
  }

  const members = [...new Set(rows.map((r) => r.agent))].sort();
  const totalRecords = rows.reduce((s, r) => s + r.records, 0);
  const totalMB = rows.reduce((s, r) => s + r.payload_bytes, 0) / MB;

  return (
    <>
      <PageHeader title="Volume">
        <p className="mb-4 max-w-prose text-sm text-paper-muted">
          How much each member sends, per day and per type.{" "}
          <span className="font-bold">{totalRecords.toLocaleString()} records, {totalMB.toFixed(2)} MB</span>{" "}
          in the last {period} days.
        </p>
        <form className="mb-4 flex flex-wrap items-end gap-2" aria-label="Period"
              onSubmit={(e) => { e.preventDefault(); reload(); }}>
          <select className="field" value={days} onChange={(e) => setDays(e.target.value)} aria-label="Days">
            {DAYS.map((d) => <option key={d} value={String(d)}>{d} days</option>)}
          </select>
          <button className="btn btn-primary">Refresh</button>
        </form>
      </PageHeader>
      {swarmChip}
      <SwarmMessages swarms={data?.swarms} />
      <ul className="panel" aria-label="Volume by member">
        {members.map((m) => {
          const mrows = rows.filter((r) => r.agent === m);
          const mRecords = mrows.reduce((s, r) => s + r.records, 0);
          const mMB = mrows.reduce((s, r) => s + r.payload_bytes, 0) / MB;

          // one row per type for this member, aggregated over the period
          const typeMap = new Map();
          for (const r of mrows) {
            const t = typeMap.get(r.type) || { records: 0, bytes: 0 };
            t.records += r.records;
            t.bytes += r.payload_bytes;
            typeMap.set(r.type, t);
          }
          const types = [...typeMap.entries()].sort((a, b) => b[1].records - a[1].records);
          const top = types[0];
          const maxType = top ? top[1].records : 0;

          return (
            <li key={m} className="border-b border-ink-line last:border-b-0 px-4 py-3">
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:gap-x-4">
                <div className="min-w-0">
                  <span className="block truncate font-bold">{m}</span>
                  <span className="block text-xs text-paper-muted">
                    {(mRecords / period).toFixed(1)} records/day ·
                    {(mMB / period).toFixed(2)} MB/day · top type{" "}
                    <span className="font-bold">{top ? `${top[0]} (${top[1].records})` : "–"}</span>
                  </span>
                </div>
                <ul className="flex flex-col gap-1 md:flex-col md:w-64" aria-label={`Types for ${m}`}>
                  {types.map(([type, stats]) => (
                    <li key={`${m}|${type}`} className="flex items-center gap-2 text-sm">
                      <span className="w-20 text-xs truncate">{type}</span>
                      <Bar value={stats.records} max={maxType} title={`${type}: ${stats.records}`} />
                      <span className="w-10 text-right text-xs">{stats.records}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </li>
          );
        })}
      </ul>
    </>
  );
}
