// Directives (#307): issue a signed instruction to one swarm or all, and see who
// received, accepted or declined it.
import { useState } from "react";
import { api } from "../api/client.js";
import { useApi } from "../api/useApi.js";
import HelpTip from "../components/HelpTip.jsx";
import { EmptyState, ErrorNote, Loading, PageHeader } from "../components/ui.jsx";
import { localTime, relativeTime } from "../lib/format.js";
import { DIRECTIVES_ABOUT, DIRECTIVE_CONFIRM } from "../lib/help.js";

const STATUS_TEXT = { open: "Open", cancelled: "Cancelled", expired: "Expired" };

export function targetText(d) {
  return d.scope?.all ? "All swarms" : d.scope?.swarm_name || d.scope?.swarm || "?";
}

export function deliveryText(v) {
  if (!v) return "—";
  return `meant for ${v.meant_for} · received ${v.received} · accepted ${v.accepted} · declined ${v.declined}`;
}

export function IssueForm({ swarms, onIssued }) {
  const active = (swarms || []).filter((s) => s.status === "active");
  const [target, setTarget] = useState("all");
  const [text, setText] = useState("");
  const [days, setDays] = useState("7");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function issue() {
    setBusy(true);
    setError(null);
    try {
      await api("/admin/directives", { method: "POST", body: {
        scope: target === "all" ? { all: true } : { swarm: target }, text: text.trim(), expires_in_days: Number(days),
      } });
      setText("");
      setConfirming(false);
      onIssued?.();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel mb-6 p-4" aria-labelledby="issue-h">
      <h2 id="issue-h" className="mb-2 flex items-center gap-2 font-bold">
        Issue a directive <HelpTip id="directives-about" label="About directives">{DIRECTIVES_ABOUT}</HelpTip>
      </h2>
      <form className="flex flex-wrap items-end gap-3" aria-label="Issue a directive"
            onSubmit={(e) => { e.preventDefault(); setConfirming(true); }}>
        <label className="flex flex-col text-sm text-paper-muted">
          Target
          <select className="field" value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="all">All swarms</option>
            {active.map((s) => <option key={s.id} value={s.name}>{s.name}</option>)}
          </select>
        </label>
        <label className="flex flex-1 flex-col text-sm text-paper-muted">
          Directive
          <textarea className="field min-w-[16rem]" rows={2} maxLength={2000} value={text}
                    onChange={(e) => setText(e.target.value)} required />
        </label>
        <label className="flex flex-col text-sm text-paper-muted">
          Expires after (days)
          <input className="field w-24" type="number" min="1" max="90" value={days} onChange={(e) => setDays(e.target.value)} />
        </label>
        <button type="submit" className="btn btn-primary" disabled={!text.trim()}>Review</button>
      </form>
      {confirming && (
        <div className="mt-3 rounded-md border border-ink-line p-3 text-sm" role="group" aria-label="Confirm directive">
          <p>{DIRECTIVE_CONFIRM} Issue to <strong>{target === "all" ? "all swarms" : target}</strong>?</p>
          <p className="mt-1 italic">“{text.trim()}”</p>
          <span className="mt-2 flex gap-2">
            <button type="button" className="btn btn-primary" disabled={busy} onClick={issue}>Issue directive</button>
            <button type="button" className="btn" onClick={() => setConfirming(false)}>Back</button>
          </span>
        </div>
      )}
      <ErrorNote error={error} />
    </section>
  );
}

function Row({ d, onChanged }) {
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState(null);
  const v = d.delivery;
  async function cancel() {
    try {
      await api(`/admin/directives/${d.id}/cancel`, { method: "POST" });
      onChanged?.();
    } catch (err) {
      setError(err);
    }
  }
  return (
    <tr className="border-t border-ink-line align-top">
      <td className="px-3 py-2">{d.text}</td>
      <td className="px-3 py-2">{targetText(d)}</td>
      <td className="px-3 py-2">{d.issued_by}<span className="block text-xs text-paper-muted" title={localTime(d.issued_at)}>{relativeTime(d.issued_at)}</span></td>
      <td className="px-3 py-2">{STATUS_TEXT[d.status] || d.status}</td>
      <td className="px-3 py-2">
        {deliveryText(v)}
        {v?.declines?.length > 0 && (
          <details className="mt-1 text-xs">
            <summary className="cursor-pointer">Declines ({v.declines.length})</summary>
            <ul>{v.declines.map((x) => <li key={x.member}><strong>{x.member}</strong>: {x.reason}</li>)}</ul>
          </details>
        )}
      </td>
      <td className="px-3 py-2">
        {d.status === "open" && (confirm ? (
          <span className="flex gap-1">
            <button type="button" className="btn btn-danger" onClick={cancel}>Confirm cancel</button>
            <button type="button" className="btn" onClick={() => setConfirm(false)}>Keep</button>
          </span>
        ) : <button type="button" className="btn" onClick={() => setConfirm(true)}>Cancel</button>)}
        <ErrorNote error={error} />
      </td>
    </tr>
  );
}

export default function Directives() {
  const { data, error, loading, reload } = useApi("/admin/directives");
  const swarms = useApi("/admin/swarms");
  const rows = data?.directives || [];
  return (
    <>
      <PageHeader title="Directives" />
      <IssueForm swarms={swarms.data} onIssued={reload} />
      {loading && !data ? <Loading what="Loading directives" /> : error ? <ErrorNote error={error} onRetry={reload} /> :
        rows.length === 0 ? (
          <EmptyState title="No directives yet">Issue one above. Members follow directives by default and say whether they accepted.</EmptyState>
        ) : (
          <div className="panel overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="px-3 py-2 text-left font-bold">Directives, newest first</caption>
              <thead className="text-xs text-paper-muted">
                <tr>{["Directive", "Target", "Issued", "Status", "Delivery", ""].map((h, i) => (
                  <th key={i} scope="col" className="px-3 py-2 text-left">{h || <span className="sr-only">Actions</span>}</th>))}</tr>
              </thead>
              <tbody>{rows.map((d) => <Row key={d.id} d={d} onChanged={reload} />)}</tbody>
            </table>
          </div>
        )}
    </>
  );
}
