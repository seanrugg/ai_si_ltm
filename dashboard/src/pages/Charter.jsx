// Fleet charter (#256): publish the fleet's governance as markdown, preview it,
// and see which members have read the current version.
import { useState } from "react";
import HelpTip from "../components/HelpTip.jsx";
import Markdown from "../components/Markdown.jsx";
import { api } from "../api/client.js";
import { useApi } from "../api/useApi.js";
import { EmptyState, ErrorNote, Loading, PageHeader } from "../components/ui.jsx";
import { CHARTER, CHARTER_GRACE } from "../lib/help.js";
import { localTime, shortHash } from "../lib/format.js";

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
      const res = await api("/admin/charter", { method: "PUT", body: { markdown: text } });
      setResult(res);
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
          {result.published ? `Published version ${result.version}.` : "No change: that's the current version."}
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

function History({ versions, current }) {
  const [shown, setShown] = useState(null);
  const [error, setError] = useState(null);
  async function view(version) {
    setError(null);
    if (shown?.version === version) return setShown(null);
    try {
      setShown(await api(`/admin/charter/versions/${version}`));
    } catch (err) {
      setError(err);
    }
  }
  return (
    <section aria-labelledby="charter-history-h">
      <h2 id="charter-history-h" className="mb-2 text-lg font-bold">Version history</h2>
      <ul className="panel">
        {versions.map((v) => (
          <li key={v.version} className="flex flex-wrap items-center gap-3 border-b border-ink-line px-3 py-2 last:border-b-0 text-sm">
            <span className="font-bold">v{v.version}{v.version === current ? " (current)" : ""}</span>
            <code className="font-mono" title={v.hash}>{shortHash(v.hash || "", 12)}</code>
            <span className="text-paper-muted">{v.published_at ? localTime(v.published_at) : ""} · {v.published_by}</span>
            <button type="button" className="btn" onClick={() => view(v.version)}>
              {shown?.version === v.version ? "Hide" : "View"}
            </button>
          </li>
        ))}
      </ul>
      <ErrorNote error={error} />
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
      <PageHeader title="Fleet charter">
        <HelpTip id="charter-about" label="About the charter">{CHARTER}</HelpTip>
      </PageHeader>
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
      {cur && <History versions={data.versions} current={cur.version} />}
    </>
  );
}
