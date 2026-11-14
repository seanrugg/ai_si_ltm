// Fleet charter (#256): publish the fleet's governance as markdown, preview it,
// and see which members have read the current version. Version control (#345):
// change notes, compare, make an older version current, withdraw or delete.
import { useEffect, useState } from "react";
import HelpTip from "../components/HelpTip.jsx";
import Markdown from "../components/Markdown.jsx";
import { api } from "../api/client.js";
import { useApi } from "../api/useApi.js";
import { EmptyState, ErrorNote, Loading, PageHeader } from "../components/ui.jsx";
import { CHARTER, CHARTER_GRACE, CHARTER_HISTORY, CHARTER_NOTE, CHARTER_WITHDRAW } from "../lib/help.js";
import { localTime, shortHash } from "../lib/format.js";
import { diffSummary, lineDiff } from "../lib/lineDiff.js";

export const MAX_NOTE = 200;

// Not yet read first (overdue before due-later), then readers by name.
export function sortReadStatus(members) {
  const rank = (m) => (m.read ? 2 : m.overdue ? 0 : 1);
  return [...(members || [])].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
}

function CopyHash({ hash }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(hash);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }
  return (
    <span className="inline-flex items-center gap-2">
      <code className="font-mono text-sm" title={hash}>{shortHash(hash, 12)}</code>
      <button type="button" className="btn" onClick={copy} aria-label="Copy the charter hash">
        {copied ? "Copied" : "Copy"}
      </button>
    </span>
  );
}

export function Editor({ initial, graceDays, onPublished }) {
  const [text, setText] = useState(initial || "");
  const [note, setNote] = useState("");
  const [preview, setPreview] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const changed = text.trim() && text !== initial;

  async function upload(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    setText(await file.text());
    setPreview(true);
  }

  async function publish() {
    setBusy(true);
    setError(null);
    try {
      const res = await api("/admin/charter", { method: "PUT", body: { markdown: text, note: note.trim() || null } });
      setResult(res);
      setNote("");
      setConfirming(false);
      onPublished?.();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mb-6 rounded-md border border-ink-line bg-ink p-4" aria-labelledby="charter-edit-h">
      <h2 id="charter-edit-h" className="mb-2 text-lg font-bold">Edit</h2>
      <label htmlFor="charter-text" className="block text-sm text-paper-muted">Charter (markdown)</label>
      <textarea id="charter-text" className="field h-64 w-full font-mono text-sm" value={text}
                onChange={(e) => { setText(e.target.value); setConfirming(false); setResult(null); }} />
      <label htmlFor="charter-note" className="mt-2 block text-sm text-paper-muted">
        Change note (optional) <HelpTip id="charter-note-about" label="About the change note">{CHARTER_NOTE}</HelpTip>
      </label>
      <input id="charter-note" className="field w-full text-sm" maxLength={MAX_NOTE} value={note}
             placeholder="What changed, e.g. Article 4: incident reports within 24 hours"
             aria-describedby="charter-note-count" onChange={(e) => setNote(e.target.value)} />
      <p id="charter-note-count" className="text-xs text-paper-muted">{note.length}/{MAX_NOTE}</p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <label className="btn cursor-pointer">
          Upload .md
          <input type="file" accept=".md,.markdown,text/markdown,text/plain" className="sr-only" onChange={upload} />
        </label>
        <button type="button" className="btn" aria-pressed={preview} onClick={() => setPreview((p) => !p)}>
          {preview ? "Hide preview" : "Preview"}
        </button>
        <button type="button" className="btn btn-primary" disabled={!changed || busy} onClick={() => setConfirming(true)}>
          Publish
        </button>
      </div>
      {confirming && (
        <div role="group" aria-label="Confirm publish" className="mt-3 rounded-md border border-warn/50 p-3">
          <p className="text-sm text-warn">{CHARTER_GRACE.replace("{days}", String(graceDays))}</p>
          <div className="mt-2 flex gap-2">
            <button type="button" className="btn btn-primary" disabled={busy} onClick={publish}>
              {busy ? "Publishing…" : "Publish this version"}
            </button>
            <button type="button" className="btn" onClick={() => setConfirming(false)}>Cancel</button>
          </div>
        </div>
      )}
      {result && (
        <p className="mt-2 text-sm text-ok" role="status">
          {!result.published ? "No change: that's the current version."
            : result.reverted_from ? `Same text as version ${result.reverted_from}: published as version ${result.version}.`
              : `Published version ${result.version}.`}
        </p>
      )}
      <ErrorNote error={error} />
      {preview && (
        <section aria-labelledby="charter-preview-h" className="mt-4 rounded-md border border-ink-line p-4">
          <h3 id="charter-preview-h" className="mb-2 text-sm font-bold text-paper-muted">Preview</h3>
          <Markdown source={text} />
        </section>
      )}
    </section>
  );
}

export function ReadStatus({ members }) {
  const [filter, setFilter] = useState("all");
  const sorted = sortReadStatus(members);
  const rows = filter === "unread" ? sorted.filter((m) => !m.read) : sorted;
  const unread = sorted.filter((m) => !m.read).length;
  return (
    <section className="mb-6" aria-labelledby="charter-status-h">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 id="charter-status-h" className="text-lg font-bold">Read status</h2>
        <div role="group" aria-label="Filter members" className="flex gap-1">
          <button type="button" className={`btn ${filter === "all" ? "btn-primary" : ""}`} aria-pressed={filter === "all"}
                  onClick={() => setFilter("all")}>All</button>
          <button type="button" className={`btn ${filter === "unread" ? "btn-primary" : ""}`}
                  aria-pressed={filter === "unread"} onClick={() => setFilter("unread")}>Not yet ({unread})</button>
        </div>
      </div>
      <table className="w-full text-sm">
        <caption className="sr-only">Which members have read the current charter</caption>
        <thead><tr className="text-left text-paper-muted"><th className="font-normal">Member</th><th className="font-normal">Status</th></tr></thead>
        <tbody>
          {rows.map((m) => (
            <tr key={m.name} className="border-t border-ink-line">
              <td className="py-1">{m.name}</td>
              <td className="py-1">
                {m.read ? (
                  <span className="text-ok">✓ Read {localTime(m.read_at)}</span>
                ) : (
                  <span className={m.overdue ? "text-warn" : ""}>
                    Not yet{m.due ? ` · due ${localTime(m.due)}` : ""}{m.overdue ? " (writes paused)" : ""}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

const STATUS = {
  current: { label: "Current", cls: "text-ok" },
  superseded: { label: "Superseded", cls: "text-paper-muted" },
  withdrawn: { label: "Withdrawn", cls: "text-warn" },
  deleted: { label: "Deleted", cls: "text-paper-muted" },
};

/** The versions to list: withdrawn and deleted ones only when asked for. */
export function visibleVersions(versions, showHidden) {
  return (versions || []).filter((v) => showHidden || (v.status !== "withdrawn" && v.status !== "deleted"));
}

/** A unified line diff with +/− in the text (colour is only extra), and a summary. */
export function CharterDiff({ from, to }) {
  const rows = lineDiff(from.markdown, to.markdown);
  const summary = diffSummary(from.markdown, to.markdown, rows);
  return (
    <section aria-label={`Changes from version ${from.version} to version ${to.version}`}
             className="mt-3 rounded-md border border-ink-line p-3">
      <p className="mb-2 text-sm" role="status">
        Version {from.version} → version {to.version}: {summary.text}
      </p>
      {summary.added + summary.removed > 0 && (
        <pre className="max-h-[32rem] overflow-auto text-xs leading-5" aria-label="Line diff">
          {rows.map((r, k) => (
            <div key={k} className={r.op === "+" ? "bg-ok/10 text-ok" : r.op === "−" ? "bg-warn/10 text-warn" : ""}>
              <span aria-hidden="true" className="inline-block w-10 select-none text-right text-paper-muted">
                {r.op === "+" ? r.newNo : r.oldNo}
              </span>
              <span className="inline-block w-5 select-none text-center font-bold">{r.op === " " ? "" : r.op}</span>
              {r.op === "+" && <span className="sr-only">added: </span>}
              {r.op === "−" && <span className="sr-only">removed: </span>}
              {r.text || " "}
            </div>
          ))}
        </pre>
      )}
    </section>
  );
}

function Compare({ versions, current, initial, onClose }) {
  const choices = versions.filter((v) => v.status !== "deleted");
  const [fromV, setFromV] = useState(initial?.from ?? choices.find((v) => v.version !== current)?.version ?? current);
  const [toV, setToV] = useState(initial?.to ?? current);
  const [texts, setTexts] = useState({});
  const [error, setError] = useState(null);
  const from = texts[fromV];
  const to = texts[toV];

  useEffect(() => {
    let live = true;
    for (const n of [fromV, toV]) {
      if (texts[n]) continue;
      api(`/admin/charter/versions/${n}`)
        .then((res) => live && setTexts((t) => ({ ...t, [n]: res })))
        .catch((err) => live && setError(err));
    }
    return () => { live = false; };
  }, [fromV, toV, texts]);

  const pick = (label, value, set) => (
    <label className="flex items-center gap-2 text-sm">
      {label}
      <select className="field" value={value} onChange={(e) => { setError(null); set(Number(e.target.value)); }}>
        {choices.map((v) => <option key={v.version} value={v.version}>Version {v.version}</option>)}
      </select>
    </label>
  );
  return (
    <section aria-labelledby="charter-compare-h" className="mt-3 rounded-md border border-ink-line p-3">
      <div className="flex flex-wrap items-center gap-3">
        <h3 id="charter-compare-h" className="text-sm font-bold">Compare</h3>
        {pick("From", fromV, setFromV)}
        {pick("To", toV, setToV)}
        <button type="button" className="btn" onClick={() => { const f = fromV; setFromV(toV); setToV(f); }}>Swap</button>
        <button type="button" className="btn" onClick={onClose}>Close</button>
      </div>
      <ErrorNote error={error} />
      {from && to ? <CharterDiff from={from} to={to} /> : !error && <p className="mt-2 text-sm text-paper-muted">Loading…</p>}
    </section>
  );
}

function VersionActions({ v, busy, onAct, onView, shown, onCompare }) {
  const [mode, setMode] = useState(null); // "withdraw" | "delete"
  const [reason, setReason] = useState("");
  const [confirm, setConfirm] = useState("");
  if (v.status === "deleted") {
    return <span className="text-xs text-paper-muted">by {v.deleted_by} {v.deleted_at ? localTime(v.deleted_at) : ""}</span>;
  }
  const canDelete = v.status !== "current" && !v.reads;
  return (
    <div className="flex flex-wrap items-center gap-1">
      <button type="button" className="btn" onClick={onView} aria-label={`${shown ? "Hide" : "View"} version ${v.version}`}>
        {shown ? "Hide" : "View"}
      </button>
      <button type="button" className="btn" onClick={onCompare} aria-label={`Compare version ${v.version}`}>Compare</button>
      {v.status === "superseded" && (
        <button type="button" className="btn" disabled={busy} aria-label={`Make version ${v.version} current`}
                onClick={() => onAct(`/admin/charter/versions/${v.version}/make-current`, { method: "POST" })}>
          Make current
        </button>
      )}
      {v.status === "superseded" && (
        <button type="button" className="btn" aria-pressed={mode === "withdraw"} aria-label={`Withdraw version ${v.version}…`}
                onClick={() => setMode(mode === "withdraw" ? null : "withdraw")}>Withdraw…</button>
      )}
      {canDelete && (
        <button type="button" className="btn" aria-pressed={mode === "delete"} aria-label={`Delete version ${v.version}…`}
                onClick={() => setMode(mode === "delete" ? null : "delete")}>Delete…</button>
      )}
      {mode === "withdraw" && (
        <form className="mt-1 flex w-full flex-wrap items-center gap-2" aria-label={`Withdraw version ${v.version}`}
              onSubmit={(e) => {
                e.preventDefault();
                onAct(`/admin/charter/versions/${v.version}/withdraw`, { method: "POST", body: { reason } });
              }}>
          <label className="text-sm" htmlFor={`withdraw-${v.version}`}>Reason</label>
          <input id={`withdraw-${v.version}`} className="field text-sm" required maxLength={MAX_NOTE} value={reason}
                 onChange={(e) => setReason(e.target.value)} />
          <button className="btn btn-primary" disabled={busy || !reason.trim()}>Withdraw version {v.version}</button>
        </form>
      )}
      {mode === "delete" && (
        <form className="mt-1 flex w-full flex-wrap items-center gap-2" aria-label={`Delete version ${v.version}`}
              onSubmit={(e) => {
                e.preventDefault();
                onAct(`/admin/charter/versions/${v.version}?confirm=${encodeURIComponent(confirm)}`, { method: "DELETE" });
              }}>
          <label className="text-sm text-warn" htmlFor={`delete-${v.version}`}>
            Nobody has read it. Type {v.version} to delete it for good
          </label>
          <input id={`delete-${v.version}`} className="field w-20 text-sm" inputMode="numeric" value={confirm}
                 onChange={(e) => setConfirm(e.target.value)} />
          <button className="btn btn-primary" disabled={busy || confirm.trim() !== String(v.version)}>
            Delete version {v.version}
          </button>
        </form>
      )}
    </div>
  );
}

export function History({ versions, current, onChanged }) {
  const [shown, setShown] = useState(null);
  const [showHidden, setShowHidden] = useState(false);
  const [comparing, setComparing] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [done, setDone] = useState(null);
  const rows = visibleVersions(versions, showHidden);
  const hidden = (versions || []).length - visibleVersions(versions, false).length;

  async function view(version) {
    setError(null);
    if (shown?.version === version) return setShown(null);
    try {
      setShown(await api(`/admin/charter/versions/${version}`));
    } catch (err) {
      setError(err);
    }
  }

  async function act(path, opts) {
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const res = await api(path, opts);
      setDone(res.status === "deleted" ? `Deleted version ${res.version}.`
        : res.status === "withdrawn" ? `Withdrew version ${res.version}.`
          : res.published ? `Version ${res.reverted_from} is current again, as version ${res.version}.` : "No change.");
      setShown(null);
      onChanged?.();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="charter-history-h">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 id="charter-history-h" className="text-lg font-bold">
          Version history <HelpTip id="charter-history-about" label="About version history">{CHARTER_HISTORY}</HelpTip>
          {" "}<HelpTip id="charter-withdraw-about" label="About withdraw and delete">{CHARTER_WITHDRAW}</HelpTip>
        </h2>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} />
          Show withdrawn and deleted ({hidden})
        </label>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <caption className="sr-only">Charter versions, newest first</caption>
          <thead>
            <tr className="text-left text-paper-muted">
              <th className="font-normal">Version</th><th className="font-normal">Hash</th>
              <th className="font-normal">Published</th><th className="font-normal">Change note</th>
              <th className="font-normal">Status</th><th className="font-normal">Reads</th>
              <th className="font-normal"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((v) => (
              <tr key={v.version} className="border-t border-ink-line align-top">
                <td className="py-1 font-bold">v{v.version}</td>
                <td className="py-1"><code className="font-mono" title={v.hash || ""}>{shortHash(v.hash || "", 12)}</code></td>
                <td className="py-1 text-paper-muted">
                  {v.published_at ? `${localTime(v.published_at)} · ${v.published_by}` : "—"}
                </td>
                <td className="py-1">
                  {v.note ? v.note : <span className="text-paper-muted">no note</span>}
                  {v.status === "withdrawn" && v.withdrawn?.reason && (
                    <div className="text-xs text-warn">Withdrawn: {v.withdrawn.reason}</div>
                  )}
                </td>
                <td className={`py-1 ${STATUS[v.status]?.cls || ""}`}>{STATUS[v.status]?.label || v.status}</td>
                <td className="py-1">{v.status === "deleted" ? "—" : v.reads ?? 0}</td>
                <td className="py-1">
                  <VersionActions v={v} busy={busy} onAct={act} shown={shown?.version === v.version}
                                  onView={() => view(v.version)}
                                  onCompare={() => setComparing({
                                    from: v.version === current ? undefined : v.version, to: current })} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {done && <p className="mt-2 text-sm text-ok" role="status">{done}</p>}
      <ErrorNote error={error} />
      {comparing && (
        <Compare key={`${comparing.from}-${comparing.to}`} versions={versions} current={current} initial={comparing}
                 onClose={() => setComparing(null)} />
      )}
      {shown && (
        <section aria-label={`Charter version ${shown.version}`} className="mt-3 rounded-md border border-ink-line p-4">
          <Markdown source={shown.markdown} />
        </section>
      )}
    </section>
  );
}

export default function Charter() {
  const { data, error, loading, reload } = useApi("/admin/charter");
  if (loading && !data) return <Loading what="Loading the charter" />;
  if (error) return <ErrorNote error={error} onRetry={reload} />;
  const cur = data?.current;
  return (
    <>
      <PageHeader title="Fleet charter"
                  help={<HelpTip id="charter-about" label="About the charter">{CHARTER}</HelpTip>} />
      {cur ? (
        <section className="mb-6 rounded-md border border-ink-line p-4" aria-labelledby="charter-current-h">
          <h2 id="charter-current-h" className="text-lg font-bold">Current charter: version {cur.version}</h2>
          <p className="mb-3 flex flex-wrap items-center gap-3 text-sm text-paper-muted">
            <CopyHash hash={cur.hash} /> <span>Published {localTime(cur.published_at)} by {cur.published_by}</span>
          </p>
          <Markdown source={cur.markdown} />
        </section>
      ) : (
        <EmptyState title="No charter yet">
          Paste or upload the fleet&apos;s charter below. Once it&apos;s published, every member reads it before writing.
        </EmptyState>
      )}
      <Editor initial={cur?.markdown || ""} graceDays={data?.grace_days ?? 14} onPublished={reload} />
      {cur && <ReadStatus members={data.members} />}
      {cur && <History versions={data.versions} current={cur.version} onChanged={reload} />}
    </>
  );
}
