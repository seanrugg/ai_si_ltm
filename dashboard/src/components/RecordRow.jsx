// One record in a list. Click (or Enter) to expand the signed header and payload.
import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import { useApi } from "../api/useApi.js";
import { GENESIS, localTime, relativeTime } from "../lib/format.js";
import { RECORD_DELETE, WIPE_VS_DELETE } from "../lib/help.js";
import { splitRef, tombstoneText } from "../lib/tombstones.js";
import HelpTip from "./HelpTip.jsx";
import { ErrorNote, HashStrip, StatusBadge } from "./ui.jsx";

const HEADER_FIELDS = ["v", "agent", "seq", "ts", "type", "summary", "place", "peers", "tags", "refs", "outcome", "sw"];

// Which of these refs point at a deleted record (issue #174). A deleted record
// answers with its tombstone, {deleted: true, ...}; anything else stays a link.
function useDeletedRefs(refList) {
  const key = refList.join(" ");
  const [deleted, setDeleted] = useState(() => new Set());
  useEffect(() => {
    let live = true;
    Promise.all(refList.map(async (ref) => {
      const { agent, seq } = splitRef(ref);
      try {
        const rec = await api(`/admin/records/${encodeURIComponent(agent)}/${seq}`);
        return rec?.deleted ? ref : null;
      } catch {
        return null;
      }
    })).then((found) => {
      if (live) setDeleted(new Set(found.filter(Boolean)));
    });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return deleted;
}

function RefLinks({ refs }) {
  const pairs = Object.entries(refs).flatMap(([k, v]) => (Array.isArray(v) ? v : [v]).map((ref) => [k, ref]));
  const deleted = useDeletedRefs([...new Set(pairs.map(([, ref]) => ref))]);
  return (
    <span className="flex flex-wrap gap-x-3">
      {pairs.map(([k, ref]) => {
        const { agent, seq } = splitRef(ref);
        return (
          <span key={`${k}-${ref}`}>
            <span className="text-paper-muted">{k}: </span>
            {deleted.has(ref) ? (
              <span>{ref} (deleted record)</span>
            ) : (
              <Link className="underline decoration-ink-line hover:decoration-led" to={`/record/${agent}/${seq}`}>
                {ref}
              </Link>
            )}
          </span>
        );
      })}
    </span>
  );
}

function Value({ name, value }) {
  if (name === "refs") return <RefLinks refs={value} />;
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return <code className="hash">{JSON.stringify(value)}</code>;
  return String(value);
}

// "echo-base via jetson-hub", "echo-base, sent directly" or "free agent"
export function provenanceText(p) {
  if (!p || !p.swarm) return "free agent";
  return p.relay ? `${p.swarm} via ${p.relay}` : `${p.swarm}, sent directly`;
}

// What is left of a deleted record: who deleted it, when and why (issue #174).
// `announce` is for a record deleted just now: it takes focus and is a status.
export function TombstoneNote({ tombstone, announce = false }) {
  const note = useRef(null);
  useEffect(() => {
    if (announce) note.current?.focus();
  }, [announce]);
  return (
    <p ref={note} tabIndex={announce ? -1 : undefined} role={announce ? "status" : undefined}
       className="text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-led">
      <span className="font-bold">Deleted record {tombstone.agent}:{tombstone.seq}.</span>{" "}
      {tombstoneText(tombstone)}{" "}
      <span className="text-paper-muted">The chain still verifies: a tombstone keeps its hash.</span>{" "}
      <HashStrip hash={tombstone.hash} />
    </p>
  );
}

// A deleted record's place in a list, where a seq gap would otherwise show.
export function TombstoneRow({ tombstone, showAgent = true, announce = false }) {
  return (
    <li className="border-b border-ink-line px-3 py-2 last:border-b-0">
      {!showAgent && <span className="mr-2 text-sm text-paper-faint">#{tombstone.seq}</span>}
      <TombstoneNote tombstone={tombstone} announce={announce} />
    </li>
  );
}

// The confirmation panel for deleting one record (issue #174).
function DeleteRecordPanel({ record, onDeleted, onCancel }) {
  const id = useId();
  const panel = useRef(null);
  const path = `/admin/records/${encodeURIComponent(record.agent)}/${record.seq}`;
  const { data: preview, error: previewError, loading } = useApi(`${path}/delete-preview`);
  const [reason, setReason] = useState("");
  const [policyOk, setPolicyOk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    panel.current?.focus();
  }, []);

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      onDeleted(await api(path, { method: "DELETE", body: { reason: reason.trim() || null } }));
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  const refs = preview?.referenced_by || [];
  const blocked = busy || loading || !preview || (Boolean(preview.policy_effect) && !policyOk);
  return (
    <section ref={panel} tabIndex={-1} aria-labelledby={`${id}-h`}
             className="mt-3 rounded-md border border-bad/40 bg-bad/5 p-3 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-led md:col-span-2">
      <div className="mb-2 flex items-center">
        <h3 id={`${id}-h`} className="font-bold">Delete record {record.agent}:{record.seq}</h3>
        <HelpTip id={`${id}-vs`} label="Wipe vs delete">{WIPE_VS_DELETE}</HelpTip>
      </div>
      <p>
        This permanently deletes {record.type} record {record.agent}:{record.seq} (<em>{record.summary || "no summary"}</em>).
        Its chain stays valid: a tombstone with only its hash is kept.
      </p>
      {loading && <p className="mt-2 text-paper-muted" role="status">Checking what refers to it…</p>}
      <ErrorNote error={previewError} />
      {refs.length > 0 && (
        <div className="mt-2">
          <p>
            {refs.length === 1
              ? "This 1 record refers to it; it'll show it as deleted."
              : `These ${refs.length.toLocaleString()} records refer to it; they'll show it as deleted.`}
          </p>
          <ul className="mt-1 list-disc pl-5 text-paper-muted">
            {refs.map((r) => (
              <li key={`${r.agent}:${r.seq}`}>
                {r.type} {r.agent}:{r.seq}{r.summary ? `, ${r.summary}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
      {preview?.policy_effect && (
        <div className="mt-2">
          <p><strong>Warning: {preview.policy_effect}</strong></p>
          <label className="mt-1 flex items-center gap-2">
            <input type="checkbox" className="accent-led" checked={policyOk}
                   onChange={(e) => setPolicyOk(e.target.checked)} />
            I understand this changes which policies are in force.
          </label>
        </div>
      )}
      <label htmlFor={`${id}-reason`} className="mt-2 block text-xs text-paper-muted">Reason (optional)</label>
      <div className="mt-1 flex flex-wrap items-center gap-2">
        <input id={`${id}-reason`} className="field w-72" value={reason} maxLength={500}
               onChange={(e) => setReason(e.target.value)} />
        <button type="button" className="btn btn-danger" disabled={blocked} onClick={remove}>Delete</button>
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
      </div>
      <ErrorNote error={error} />
    </section>
  );
}

export function RecordDetail({ record, onDeleted }) {
  const [deleting, setDeleting] = useState(false);
  const [tombstone, setTombstone] = useState(null);
  const deleteButton = useRef(null);
  const helpId = useId();
  const wasDeleting = useRef(false);

  // Focus goes back to "Delete record" when the panel closes without a delete.
  useEffect(() => {
    if (wasDeleting.current && !deleting && !tombstone) deleteButton.current?.focus();
    wasDeleting.current = deleting;
  }, [deleting, tombstone]);

  function deleted(tomb) {
    setDeleting(false);
    if (onDeleted) onDeleted(tomb);
    else setTombstone(tomb);
  }

  if (tombstone) return <TombstoneNote tombstone={tombstone} announce />;
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {record.provenance && (
        <p className="mb-2 text-xs text-paper-muted">Came from: {provenanceText(record.provenance)}</p>
      )}
      <section className="min-w-0">
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
      <section className="min-w-0">
        <h3 className="mb-2 text-sm font-bold text-paper-muted">Payload</h3>
        {record.payload_purged ? (
          <p className="text-sm text-paper-muted">
            Payload purged. The header and its payload_hash are kept, so the chain still verifies.
          </p>
        ) : (
          <pre className="max-h-80 overflow-auto rounded-md bg-ink p-3 font-mono text-xs leading-relaxed">
            {JSON.stringify(record.payload, null, 2)}
          </pre>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Link to={`/decision-trace/${record.agent}/${record.seq}`} className="btn">
            Trace this record
          </Link>
          <span>
            <button ref={deleteButton} type="button" className="btn btn-danger" aria-expanded={deleting}
                    onClick={() => setDeleting(true)} disabled={deleting}>
              Delete record
            </button>
            <HelpTip id={`${helpId}-delete`} label="About deleting a record">{RECORD_DELETE}</HelpTip>
          </span>
        </div>
      </section>
      {deleting && (
        <DeleteRecordPanel record={record} onDeleted={deleted} onCancel={() => setDeleting(false)} />
      )}
    </div>
  );
}

export default function RecordRow({ record, showAgent = true, status }) {
  const [open, setOpen] = useState(false);
  const [tombstone, setTombstone] = useState(null);
  if (tombstone) return <TombstoneRow tombstone={tombstone} showAgent={showAgent} announce />;
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
          {status ? <StatusBadge status={status} /> : <StatusBadge status={record.outcome} />}
        </span>
        <HashStrip hash={record.hash} label={false} className="hidden justify-self-end md:inline-flex" />
      </button>
      {open && (
        <div className="bg-ink/60 px-3 pb-4 pt-2">
          <RecordDetail record={record} onDeleted={setTombstone} />
        </div>
      )}
    </li>
  );
}
