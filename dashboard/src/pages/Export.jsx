import { useState } from "react";
import HelpTip from "../components/HelpTip.jsx";
import { EXPORT_DATES, EXPORT_FORMAT } from "../lib/help.js";
import { browserTimeZone, localDate, localDayRange } from "../lib/localDays.js";
import { useApi } from "../api/useApi.js";
import { PageHeader } from "../components/ui.jsx";

export const RECORD_TYPES = ["communication", "thought", "decision", "action", "event", "perception", "lesson", "policy", "system"];

const HOUR = 3600 * 1000;

// Quick ranges (#394). Rolling ones end "now", when the file is downloaded.
export const QUICK_RANGES = [
  { id: "hour", label: "Last hour", rollingMs: HOUR },
  { id: "day", label: "Last 24 hours", rollingMs: 24 * HOUR },
  { id: "today", label: "Today" },
  { id: "week", label: "Last 7 days", rollingMs: 7 * 24 * HOUR },
  { id: "all", label: "All" },
];

/**
 * The /api/admin/export URL for the form (issues #154, #394). Blank fields are
 * left out; types are comma-joined, and omitted when all are checked.
 *
 * Dates are calendar days in `timeZone` (the browser's by default): From
 * starts at local midnight and To covers the whole local day, sent as UTC
 * instants. A rolling `range` (hour, day, week) replaces the dates and ends at
 * `now`. `local_date` (To, or today) only names the file.
 */
export function exportUrl({ format = "jsonl", agent = "", swarm = "", types = RECORD_TYPES, from = "", to = "",
  range = "", now = Date.now(), timeZone = browserTimeZone() }) {
  const q = new URLSearchParams({ format });
  if (agent) q.set("agent", agent);
  if (swarm) q.set("swarm", swarm);
  if (types.length && types.length < RECORD_TYPES.length) q.set("type", types.join(","));
  const rolling = QUICK_RANGES.find((r) => r.id === range)?.rollingMs;
  if (rolling) {
    q.set("since", new Date(now - rolling).toISOString());
  } else {
    const { since, until } = localDayRange(from, to, timeZone);
    if (since) q.set("since", since);
    if (until) q.set("until", until);
  }
  q.set("local_date", !rolling && to ? to : localDate(now, timeZone));
  return `/api/admin/export?${q.toString()}`;
}

export default function Export() {
  const { data: membersData } = useApi("/admin/members");
  const { data: swarmsData } = useApi("/admin/swarms");
  const [format, setFormat] = useState("jsonl");
  const [agent, setAgent] = useState("");
  const [swarm, setSwarm] = useState("");
  const [types, setTypes] = useState(RECORD_TYPES);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [range, setRange] = useState("");
  const timeZone = browserTimeZone();

  const members = [...new Set((membersData?.members || []).map((m) => m.name))].sort();
  const swarms = (swarmsData || []).map((s) => s.name).sort();
  const params = { format, agent, swarm, types, from, to, range, timeZone };
  const href = exportUrl(params);
  const badRange = !range && from && to && from > to;
  const rolling = QUICK_RANGES.find((r) => r.id === range && r.rollingMs);

  function pickRange(id) {
    if (id === "today") {
      const today = localDate(Date.now(), timeZone);
      setFrom(today);
      setTo(today);
      setRange("");
    } else if (id === "all") {
      setFrom("");
      setTo("");
      setRange("");
    } else {
      setFrom("");
      setTo("");
      setRange(id);
    }
  }

  function pickDate(set, value) {
    set(value);
    setRange("");
  }

  function toggleType(t) {
    setTypes((cur) => (cur.includes(t) ? cur.filter((x) => x !== t) : RECORD_TYPES.filter((x) => cur.includes(x) || x === t)));
  }

  return (
    <>
      <PageHeader title="Export" />
      <p className="mb-2 max-w-prose text-sm text-paper-muted">
        Download records as a file, from the log of record. JSONL is a complete copy that anyone can re-verify
        with the members&apos; public keys; CSV opens in a spreadsheet.
      </p>
      <p className="mb-6 max-w-prose text-sm font-bold">
        Includes payloads. Treat the file as you would the records themselves.
      </p>

      <form className="panel max-w-2xl p-4" onSubmit={(e) => e.preventDefault()} aria-label="Export records">
        <fieldset className="mb-4">
          <legend className="mb-2 text-sm font-bold text-paper-muted">
            Format <HelpTip id="export-format" label="About export formats">{EXPORT_FORMAT}</HelpTip>
          </legend>
          {[["jsonl", "JSONL: complete and verifiable"], ["csv", "CSV: for spreadsheets"]].map(([value, label]) => (
            <label key={value} className="mb-1 flex items-center gap-2 text-sm cursor-pointer">
              <input type="radio" name="export-format" value={value} className="accent-led"
                     checked={format === value} onChange={() => setFormat(value)} />
              {label}
            </label>
          ))}
        </fieldset>

        <div className="mb-4 flex flex-wrap gap-4">
          <label className="flex flex-col gap-1 text-xs text-paper-muted" htmlFor="export-member">
            Member
            <select id="export-member" className="field" value={agent} onChange={(e) => setAgent(e.target.value)}>
              <option value="">All</option>
              {members.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-paper-muted" htmlFor="export-swarm">
            Swarm
            <select id="export-swarm" className="field" value={swarm} onChange={(e) => setSwarm(e.target.value)}>
              <option value="">All</option>
              {swarms.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
        </div>

        <fieldset className="mb-4">
          <legend className="mb-2 text-sm font-bold text-paper-muted">Record types</legend>
          <div className="grid grid-cols-2 gap-1 sm:grid-cols-3">
            {RECORD_TYPES.map((t) => (
              <label key={t} className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" className="accent-led" checked={types.includes(t)} onChange={() => toggleType(t)} />
                {t}
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset className="mb-4">
          <legend className="mb-2 text-sm font-bold text-paper-muted">
            When <HelpTip id="export-dates" label="About export dates">{EXPORT_DATES}</HelpTip>
          </legend>
          <div className="mb-3 flex flex-wrap gap-2" role="group" aria-label="Quick ranges">
            {QUICK_RANGES.map((r) => (
              <button key={r.id} type="button" className="btn h-11" aria-pressed={range === r.id}
                      onClick={() => pickRange(r.id)}>
                {r.label}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap gap-4">
            <label className="flex flex-col gap-1 text-xs text-paper-muted" htmlFor="export-from">
              From
              <input id="export-from" type="date" className="field" value={from}
                     onChange={(e) => pickDate(setFrom, e.target.value)} />
            </label>
            <label className="flex flex-col gap-1 text-xs text-paper-muted" htmlFor="export-to">
              To
              <input id="export-to" type="date" className="field" value={to}
                     onChange={(e) => pickDate(setTo, e.target.value)} />
            </label>
          </div>
          <p className="mt-2 text-xs text-paper-muted" role="status">
            {rolling
              ? `${rolling.label}, up to the moment you download.`
              : `Dates are in your time zone (${timeZone}). To includes the whole day.`}
          </p>
        </fieldset>

        {types.length === 0 || badRange ? (
          <p className="text-sm text-bad" role="alert">
            {types.length === 0 ? "Choose at least one record type." : "From must be on or before To."}
          </p>
        ) : (
          <a className="btn btn-primary inline-block" href={href} download
             onClick={(e) => { e.currentTarget.href = exportUrl({ ...params, now: Date.now() }); }}>
            Download {format === "jsonl" ? "JSONL" : "CSV"} file of records from {agent || "all members"}
          </a>
        )}
      </form>
    </>
  );
}
