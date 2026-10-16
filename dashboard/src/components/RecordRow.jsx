// One record in a list. Click (or Enter) to expand the signed header and payload.
import { useState } from "react";
import { Link } from "react-router-dom";
import { GENESIS, localTime, relativeTime } from "../lib/format.js";
import { HashStrip, StatusBadge } from "./ui.jsx";

const HEADER_FIELDS = ["v", "agent", "seq", "ts", "type", "summary", "place", "peers", "tags", "refs", "outcome", "sw"];

function RefLinks({ refs }) {
  return (
    <span className="flex flex-wrap gap-x-3">
      {Object.entries(refs).flatMap(([k, v]) =>
        (Array.isArray(v) ? v : [v]).map((ref) => {
          const [agent, seq] = [ref.slice(0, ref.lastIndexOf(":")), ref.slice(ref.lastIndexOf(":") + 1)];
          return (
            <span key={`${k}-${ref}`}>
              <span className="text-paper-muted">{k}: </span>
              <Link className="underline decoration-ink-line hover:decoration-led" to={`/record/${agent}/${seq}`}>
                {ref}
              </Link>
            </span>
          );
        }),
      )}
    </span>
  );
}

function Value({ name, value }) {
  if (name === "refs") return <RefLinks refs={value} />;
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return <code className="hash">{JSON.stringify(value)}</code>;
  return String(value);
}

export function RecordDetail({ record }) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <section>
        <h3 className="mb-2 text-sm font-bold text-paper-muted">Signed header</h3>
        <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1 text-sm">
          {HEADER_FIELDS.filter((f) => record[f] !== undefined).map((f) => (
            <div key={f} className="contents">
              <dt className="text-paper-muted">{f}</dt>
              <dd className="break-words">
                <Value name={f} value={record[f]} />
              </dd>
            </div>
          ))}
          <dt className="text-paper-muted">prev_hash</dt>
          <dd>
            <HashStrip hash={record.prev_hash} label={record.prev_hash === GENESIS ? "genesis" : undefined} />
          </dd>
          <dt className="text-paper-muted">hash</dt>
          <dd>
            <HashStrip hash={record.hash} />
          </dd>
          <dt className="text-paper-muted">payload_hash</dt>
          <dd>
            <HashStrip hash={record.payload_hash} />
          </dd>
        </dl>
      </section>
      <section>
        <h3 className="mb-2 text-sm font-bold text-paper-muted">Payload</h3>
        {record.payload_purged ? (
          <p className="text-sm text-paper-muted">
            Purged by retention. The header and its payload_hash are kept, so the chain still verifies.
          </p>
        ) : (
          <pre className="max-h-80 overflow-auto rounded-md bg-ink p-3 font-mono text-xs leading-relaxed">
            {JSON.stringify(record.payload, null, 2)}
          </pre>
        )}
        <Link to={`/decision-trace/${record.agent}/${record.seq}`} className="btn mt-3">
          Trace this record
        </Link>
      </section>
    </div>
  );
}

export default function RecordRow({ record, showAgent = true }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="border-b border-ink-line last:border-b-0">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="grid w-full grid-cols-[5.5rem_1fr_auto] items-baseline gap-x-3 px-3 py-2 text-left hover:bg-ink-hover
                   md:grid-cols-[7.5rem_8rem_7rem_1fr_auto_auto]"
      >
        <span className="text-xs text-paper-muted" title={localTime(record.ts)}>
          {relativeTime(record.ts)}
        </span>
        {showAgent && (
          <span className="hidden truncate text-sm font-bold md:block">
            {record.agent}
            <span className="ml-1 font-normal text-paper-faint">#{record.seq}</span>
          </span>
        )}
        {!showAgent && <span className="hidden text-sm text-paper-faint md:block">#{record.seq}</span>}
        <span className="hidden text-sm text-paper-muted md:block">{record.type}</span>
        <span className="truncate text-sm">
          <span className="mr-2 font-bold md:hidden">{record.agent}</span>
          <span className="mr-2 text-paper-muted md:hidden">{record.type}</span>
          {record.summary || <span className="text-paper-faint">(no summary)</span>}
          {record.place && <span className="ml-2 text-paper-faint">{record.place}</span>}
        </span>
        <span className="justify-self-end">
          <StatusBadge status={record.outcome} />
        </span>
        <HashStrip hash={record.hash} label={false} className="hidden justify-self-end md:inline-flex" />
      </button>
      {open && (
        <div className="bg-ink/60 px-3 pb-4 pt-2">
          <RecordDetail record={record} />
        </div>
      )}
    </li>
  );
}
