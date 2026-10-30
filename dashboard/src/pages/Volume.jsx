import { useState } from "react";
import { useApi } from "../api/useApi.js";
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

export default function Volume() {
  const [days, setDays] = useState("14");
  const { data, error, loading, reload } = useApi("/admin/volume", { days });
  const rows = data?.rows || [];
  const period = data?.days || Number(days) || 1;

  if (loading && !data) return <Loading what="Loading volume" />;
  if (error) return <ErrorNote error={error} onRetry={reload} />;
  if (rows.length === 0) {
    return (
      <>
        <PageHeader title="Volume" />
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
