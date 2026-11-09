import { useState } from "react";
import { useApi } from "../api/useApi.js";
import { PageHeader } from "../components/ui.jsx";

export const RECORD_TYPES = ["communication", "thought", "decision", "action", "event", "perception", "lesson", "policy", "system"];

/**
 * The /api/admin/export URL for the form (issue #154). Blank fields are left
 * out; types are comma-joined, and omitted when all are checked. Dates are UTC
 * days: "To" covers the whole day.
 */
export function exportUrl({ format = "jsonl", agent = "", swarm = "", types = RECORD_TYPES, from = "", to = "" }) {
  const q = new URLSearchParams({ format });
  if (agent) q.set("agent", agent);
  if (swarm) q.set("swarm", swarm);
  if (types.length && types.length < RECORD_TYPES.length) q.set("type", types.join(","));
  if (from) q.set("since", `${from}T00:00:00Z`);
  if (to) q.set("until", `${to}T23:59:59.999Z`);
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

  const members = [...new Set((membersData?.members || []).map((m) => m.name))].sort();
  const swarms = (swarmsData || []).map((s) => s.name).sort();
  const href = exportUrl({ format, agent, swarm, types, from, to });
  const badRange = from && to && from > to;

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
          <legend className="mb-2 text-sm font-bold text-paper-muted">Format</legend>
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
          <legend className="mb-2 text-sm font-bold text-paper-muted">Dates (UTC, optional)</legend>
          <div className="flex flex-wrap gap-4">
            <label className="flex flex-col gap-1 text-xs text-paper-muted" htmlFor="export-from">
              From
              <input id="export-from" type="date" className="field" value={from} onChange={(e) => setFrom(e.target.value)} />
            </label>
            <label className="flex flex-col gap-1 text-xs text-paper-muted" htmlFor="export-to">
              To
              <input id="export-to" type="date" className="field" value={to} onChange={(e) => setTo(e.target.value)} />
            </label>
          </div>
        </fieldset>

        {types.length === 0 || badRange ? (
          <p className="text-sm text-bad" role="alert">
            {types.length === 0 ? "Choose at least one record type." : "From must be on or before To."}
          </p>
        ) : (
          <a className="btn btn-primary inline-block" href={href} download>
            Download {format === "jsonl" ? "JSONL" : "CSV"} file of records from {agent || "all members"}
          </a>
        )}
      </form>
    </>
  );
}
