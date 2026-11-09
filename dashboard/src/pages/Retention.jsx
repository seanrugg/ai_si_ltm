import { useState } from "react";
import { api } from "../api/client.js";
import { useApi } from "../api/useApi.js";
import { ErrorNote, Loading, PageHeader } from "../components/ui.jsx";
import { localTime } from "../lib/format.js";

// Types a rule may target: policy and system payloads are never purged.
export const RULE_TYPES = ["communication", "thought", "decision", "action", "event", "perception", "lesson"];
export const MIN_AUDIT_DAYS = 30;

const blankRule = () => ({ type: "", tag: "", days: 30 });

/** Problems with the rule rows, as readable strings; empty when they can be saved. */
export function validateRules(rules) {
  const problems = [];
  rules.forEach((r, i) => {
    const n = i + 1;
    const tag = (r.tag || "").trim();
    if (!r.type && !tag) problems.push(`Rule ${n} needs a type, a tag, or both.`);
    if (tag === "keep") problems.push(`Rule ${n}: "keep" marks records that are never purged; it can't be a rule.`);
    const days = Number(r.days);
    if (!Number.isInteger(days) || days < 1) problems.push(`Rule ${n}: days must be a whole number, at least 1.`);
  });
  return problems;
}

const key = (r) => `${r.type || ""}\u0000${(r.tag || "").trim()}`;

/**
 * True when the new settings could delete something the old ones would keep:
 * a rule with no same-target rule at the same or shorter age before, or a
 * shorter (or newly set) audit log limit. Removing rules or keeping forever is false.
 */
export function couldExpireMore(before, after) {
  const old = new Map();
  for (const r of before.payload_rules || []) {
    const k = key(r);
    old.set(k, Math.min(old.get(k) ?? Infinity, Number(r.days)));
  }
  const payloads = (after.payload_rules || []).some((r) => !(old.get(key(r)) <= Number(r.days)));
  const audit = after.audit_days != null && (before.audit_days == null || after.audit_days < before.audit_days);
  return payloads || audit;
}

export function toBody({ keepAll, rules, auditForever, auditDays }) {
  return {
    payload_rules: keepAll ? [] : rules.map((r) => ({
      type: r.type || null, tag: (r.tag || "").trim() || null, days: Number(r.days),
    })),
    audit_days: auditForever ? null : Number(auditDays),
  };
}

function RetentionForm({ data, onSaved }) {
  const locked = data.locked;
  const [keepAll, setKeepAll] = useState(data.payload_rules.length === 0);
  const [rules, setRules] = useState(data.payload_rules.length
    ? data.payload_rules.map((r) => ({ type: r.type || "", tag: r.tag || "", days: r.days }))
    : [blankRule()]);
  const [auditForever, setAuditForever] = useState(data.audit_days == null);
  const [auditDays, setAuditDays] = useState(data.audit_days ?? 365);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [message, setMessage] = useState("");

  const problems = keepAll ? [] : validateRules(rules);
  if (!auditForever && (!Number.isInteger(Number(auditDays)) || Number(auditDays) < MIN_AUDIT_DAYS)) {
    problems.push(`The audit log must be kept forever or at least ${MIN_AUDIT_DAYS} days.`);
  }
  const body = toBody({ keepAll, rules, auditForever, auditDays });

  function setRule(i, patch) {
    setRules((cur) => cur.map((r, j) => (j === i ? { ...r, ...patch } : r)));
    setConfirming(false);
  }

  async function save() {
    setBusy(true);
    setError(null);
    setMessage("");
    try {
      await api("/admin/retention", { method: "PUT", body });
      setConfirming(false);
      setMessage("Saved. The next scheduled run uses these settings.");
      onSaved();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  function submit(e) {
    e.preventDefault();
    if (problems.length) return;
    if (!confirming && couldExpireMore(data, body)) {
      setConfirming(true);
      return;
    }
    save();
  }

  return (
    <form onSubmit={submit} className="panel max-w-3xl p-4" aria-label="Data retention settings">
      <fieldset disabled={locked || busy}>
        <fieldset className="mb-4">
          <legend className="mb-2 text-sm font-bold text-paper-muted">Payloads</legend>
          <label className="mb-1 flex items-center gap-2 text-sm cursor-pointer">
            <input type="radio" name="payload-mode" className="accent-led" checked={keepAll}
                   onChange={() => { setKeepAll(true); setConfirming(false); }} />
            Keep all payloads forever
          </label>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="radio" name="payload-mode" className="accent-led" checked={!keepAll}
                   onChange={() => { setKeepAll(false); setConfirming(false); }} />
            Expire payloads by rule
          </label>
        </fieldset>

        {!keepAll && (
          <div className="mb-4">
            <ol className="mb-2">
              {rules.map((r, i) => {
                const n = i + 1;
                return (
                  <li key={i} className="mb-2 flex flex-wrap items-end gap-2">
                    <label className="flex flex-col gap-1 text-xs text-paper-muted">
                      Type
                      <select className="field" value={r.type} onChange={(e) => setRule(i, { type: e.target.value })}
                              aria-label={`Rule ${n} type`}>
                        <option value="">any</option>
                        {RULE_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                      </select>
                    </label>
                    <label className="flex flex-col gap-1 text-xs text-paper-muted">
                      Tag
                      <input className="field w-36" value={r.tag} maxLength={64}
                             onChange={(e) => setRule(i, { tag: e.target.value })} aria-label={`Rule ${n} tag`} />
                    </label>
                    <label className="flex flex-col gap-1 text-xs text-paper-muted">
                      Days
                      <input className="field w-24" type="number" min={1} step={1} value={r.days}
                             onChange={(e) => setRule(i, { days: e.target.value })} aria-label={`Rule ${n} days`} />
                    </label>
                    <button type="button" className="btn" aria-label={`Remove rule ${n}`}
                            onClick={() => { setRules((cur) => cur.filter((_, j) => j !== i)); setConfirming(false); }}>
                      Remove
                    </button>
                  </li>
                );
              })}
            </ol>
            <button type="button" className="btn" onClick={() => { setRules((cur) => [...cur, blankRule()]); setConfirming(false); }}>
              Add rule
            </button>
            <p className="mt-2 text-xs text-paper-muted">
              A rule purges the payloads of matching records once they are older than its days. With a type and a
              tag, a record must match both.
            </p>
          </div>
        )}

        <fieldset className="mb-4">
          <legend className="mb-2 text-sm font-bold text-paper-muted">Audit log</legend>
          <label className="mb-1 flex items-center gap-2 text-sm cursor-pointer">
            <input type="radio" name="audit-mode" className="accent-led" checked={auditForever}
                   onChange={() => { setAuditForever(true); setConfirming(false); }} />
            Keep forever
          </label>
          <label className="flex flex-wrap items-center gap-2 text-sm cursor-pointer">
            <input type="radio" name="audit-mode" className="accent-led" checked={!auditForever}
                   onChange={() => { setAuditForever(false); setConfirming(false); }} />
            Keep for
            <input className="field w-24" type="number" min={MIN_AUDIT_DAYS} step={1} value={auditDays}
                   disabled={auditForever || locked || busy} aria-label="Audit log days"
                   onChange={(e) => { setAuditDays(e.target.value); setConfirming(false); }} />
            days (at least {MIN_AUDIT_DAYS})
          </label>
        </fieldset>

        {problems.length > 0 && (
          <ul className="mb-3 text-sm text-bad" role="alert">
            {problems.map((p) => <li key={p}>{p}</li>)}
          </ul>
        )}

        {confirming && (
          <div className="mb-3 rounded-md border border-warn/50 p-3 text-sm" role="alert">
            <p className="mb-2">
              <span className="font-bold">This can delete more than before.</span> Payloads past these limits are
              permanently deleted at the next nightly run. Headers are kept, so chains still verify. Copies already
              sent to an LRS aren&apos;t affected.
            </p>
            <div className="flex gap-2">
              <button type="submit" className="btn btn-danger">Save and apply</button>
              <button type="button" className="btn" onClick={() => setConfirming(false)}>Cancel</button>
            </div>
          </div>
        )}
        {!confirming && (
          <button type="submit" className="btn btn-primary" disabled={problems.length > 0}>Save</button>
        )}
      </fieldset>
      <p className="mt-2 text-sm text-ok" role="status">{message}</p>
      <ErrorNote error={error} />
    </form>
  );
}

export default function Retention() {
  const { data, error, loading, reload } = useApi("/admin/retention");
  return (
    <>
      <PageHeader title="Data retention" />
      <p className="mb-2 max-w-prose text-sm text-paper-muted">
        How long payloads and audit entries are kept. Storage is the operator&apos;s responsibility; these settings
        apply at the next scheduled run, with no restart.
      </p>
      <p className="mb-4 max-w-prose text-sm font-bold">
        Headers are never purged. Policy and system payloads and records tagged keep are always kept.
      </p>
      {loading && !data && <Loading />}
      <ErrorNote error={error} onRetry={reload} />
      {data && (
        <>
          <dl className="mb-4 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
            <dt className="text-paper-muted">Source</dt>
            <dd>{data.source === "dashboard" ? "Set in the dashboard" : "From server configuration"}</dd>
            <dt className="text-paper-muted">Last changed</dt>
            <dd>{data.updated_at ? `${localTime(data.updated_at)} by ${data.updated_by}` : "Never changed here"}</dd>
          </dl>
          {data.locked && (
            <p className="mb-4 text-sm" role="note">
              <span className="font-bold">Locked:</span> the server&apos;s configuration controls data retention
              (LTMS_RETENTION_LOCKED), so it can&apos;t be changed here.
            </p>
          )}
          <RetentionForm key={`${data.source}:${data.updated_at ?? ""}`} data={data} onSaved={reload} />
        </>
      )}
    </>
  );
}
