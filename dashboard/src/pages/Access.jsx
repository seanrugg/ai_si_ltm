import { useState } from "react";
import { api } from "../api/client.js";
import { useApi } from "../api/useApi.js";
import { EmptyState, ErrorNote, Loading, PageHeader, StatusBadge } from "../components/ui.jsx";
import { localTime } from "../lib/format.js";

const SCOPE_KEYS = ["types", "agents", "swarms"];

const splitList = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);

// A human readable summary of a token scope, e.g. "types: event, thought · payloads".
function scopeSummary(scope) {
  if (!scope) return "—";
  const parts = [];
  for (const key of SCOPE_KEYS) {
    if (scope[key]?.length) parts.push(`${key}: ${scope[key].join(", ")}`);
  }
  if (scope.payloads === true) parts.push("payloads");
  return parts.length ? parts.join(" · ") : "—";
}

function tokenStatus(token) {
  if (token.revoked_at) return "revoked";
  if (token.expires_at && new Date(token.expires_at) <= new Date()) return "expired";
  return "active";
}

function copyToClipboard(text) {
  return navigator.clipboard?.writeText(text) ?? Promise.resolve();
}

function CopyButton({ text, ariaLabel }) {
  const [copied, setCopied] = useState(false);
  async function onCopy() {
    await copyToClipboard(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }
  return (
    <button className="btn h-11" type="button" onClick={onCopy} aria-label={ariaLabel}
            title="Copy to clipboard">
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

export default function Access() {
  const { data, error, loading, reload } = useApi("/admin/mcp");
  // null | "new": whether the "new token" form is open.
  const [mode, setMode] = useState(null);
  const [form, setForm] = useState({ name: "", types: "", agents: "", swarms: "", payloads: false, expires: "" });
  // Shown exactly once after creation, then cleared on reload.
  const [shown, setShown] = useState(null);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState(null);
  // { id, name } of the token awaiting revoke confirmation.
  const [revoking, setRevoking] = useState(null);
  const [revokeInput, setRevokeInput] = useState("");
  const [revokeBusy, setRevokeBusy] = useState(false);
  const [revokeError, setRevokeError] = useState(null);

  const fid = "access-new-token";

  async function createToken() {
    setFormError(null);
    const scope = {};
    if (form.payloads) scope.payloads = true;
    for (const key of SCOPE_KEYS) {
      const values = splitList(form[key]);
      if (values.length) scope[key] = values;
    }
    const body = { name: form.name.trim(), scope, expires_at: form.expires || null };
    if (!body.name) { setFormError("Give the token a name"); return; }
    setBusy(true);
    try {
      const created = await api("/admin/mcp/tokens", { method: "POST", body });
      setMode(null);
      setShown({ name: created.name, token: created.token });
    } catch (err) {
      setFormError(err);
    } finally {
      setBusy(false);
    }
  }

  function revokeToken(id, name) {
    setRevoking({ id, name });
    setRevokeInput("");
    setRevokeError(null);
  }
  function cancelRevoke() { setRevoking(null); }

  async function doRevoke() {
    if (!revoking || revokeInput !== revoking.name) return;
    setRevokeBusy(true);
    setRevokeError(null);
    try {
      await api(`/admin/mcp/tokens/${revoking.id}/revoke`, { method: "POST" });
      setRevoking(null);
      reload();
    } catch (err) {
      setRevokeError(err);
    } finally {
      setRevokeBusy(false);
    }
  }

  if (loading && !data) return <Loading what="Loading MCP access" />;
  if (error) return <ErrorNote error={error} onRetry={reload} />;

  return (
    <>
      <PageHeader title="MCP read access" />

      <section className="mb-8 rounded-md border border-ink-line bg-ink p-4" aria-labelledby="access-mode-h">
        <h2 id="access-mode-h" className="mb-3 text-lg font-bold">Mode</h2>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-sm">
          <dt className="text-paper-muted">Mode</dt>
          <dd className="font-mono text-base">{data.mode}</dd>
          <dt className="text-paper-muted">Open scope</dt>
          <dd>{scopeSummary(data.open_scope)}</dd>
          <dt className="text-paper-muted">Connection URL</dt>
          <dd>
            <div className="flex flex-wrap items-center gap-2">
              <input className="field h-11 w-64 font-mono text-sm" readOnly
                     value={`${window.location.origin}/mcp`} aria-label="MCP connection URL" id="access-connect-url" />
              <CopyButton text={`${window.location.origin}/mcp`} ariaLabel="Copy the MCP connection URL" />
            </div>
            <p className="mt-1 text-xs text-paper-muted">
              Set with <code className="hash">LTMS_MCP_READ_MODE</code> and <code className="hash">LTMS_MCP_OPEN_SCOPE</code>.
            </p>
          </dd>
        </dl>
      </section>

      <section className="mb-8" aria-labelledby="access-tokens-h">
        <h2 id="access-tokens-h" className="mb-3 text-lg font-bold">Tokens</h2>
        <p className="mb-4 text-sm text-paper-muted">
          Named, scoped read tokens for remote MCP clients. A token's value is shown once, at creation.
        </p>

        {mode === "new" && (
          <section className="mb-6 rounded-md border border-ink-line bg-ink p-4" aria-labelledby={`${fid}-h`}>
            <h2 id={`${fid}-h`} className="mb-3 text-lg font-bold">New token</h2>
            <form onSubmit={(e) => { e.preventDefault(); createToken(); }}
                  className="mb-4 flex flex-wrap items-end gap-2" aria-label="New token">
              <label htmlFor={`${fid}-name`} className="block text-sm text-paper-muted">Name</label>
              <input className="field h-11 w-48" value={form.name}
                     onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                     placeholder="daily-read" id={`${fid}-name`} required />
              <label htmlFor={`${fid}-types`} className="block text-sm text-paper-muted">Record types</label>
              <input className="field h-11 w-40" value={form.types}
                     onChange={(e) => setForm((f) => ({ ...f, types: e.target.value }))}
                     placeholder="event, thought" id={`${fid}-types`} />
              <label htmlFor={`${fid}-agents`} className="block text-sm text-paper-muted">Members</label>
              <input className="field h-11 w-40" value={form.agents}
                     onChange={(e) => setForm((f) => ({ ...f, agents: e.target.value }))}
                     placeholder="z3-r0" id={`${fid}-agents`} />
              <label htmlFor={`${fid}-swarms`} className="block text-sm text-paper-muted">Swarms</label>
              <input className="field h-11 w-40" value={form.swarms}
                     onChange={(e) => setForm((f) => ({ ...f, swarms: e.target.value }))}
                     placeholder="echo-base" id={`${fid}-swarms`} />
              <label className="flex items-center gap-2 text-sm mt-2 cursor-pointer">
                <input type="checkbox" checked={form.payloads}
                       onChange={(e) => setForm((f) => ({ ...f, payloads: e.target.checked }))}
                       className="accent-led h-5 w-5" id={`${fid}-payloads`} />
                Include payloads
              </label>
              <p className="text-xs text-paper-muted">
                With payloads off the token sees headers only (records' payloads stay on the server).
              </p>
              <label htmlFor={`${fid}-expires`} className="block text-sm text-paper-muted">Expires (optional)</label>
              <input className="field h-11 w-40" type="date" value={form.expires}
                     onChange={(e) => setForm((f) => ({ ...f, expires: e.target.value }))}
                     id={`${fid}-expires`} />
              <ErrorNote error={formError} />
              <button type="submit" className="btn btn-primary h-11" disabled={busy}>Create token</button>
              <button className="btn h-11" type="button" disabled={busy} onClick={() => setMode(null)}>Cancel</button>
            </form>
            <p className="mt-2 text-xs text-paper-muted">
              Comma-separated values per field. Empty fields are not restricted. A token can read
              records its scope covers; without <b>recall</b> it can only read what its scope allows.
            </p>
          </section>
        )}

        {shown && (
          <section className="mb-6 rounded-md border border-led bg-led/10 p-4" aria-live="polite"
                   aria-label="New token value (shown once)">
            <h2 className="mb-2 text-lg font-bold">New token: {shown.name}</h2>
            <p className="mb-3 text-xs text-paper-muted">
              Copy it now. It won't be shown again.
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <input className="field h-11 w-64 font-mono text-sm" readOnly value={shown.token}
                     aria-label="New token value" id={`${fid}-value`} />
              <CopyButton text={shown.token} ariaLabel="Copy the new token value" />
            </div>
          </section>
        )}

        {data.tokens.length === 0 && (
          <EmptyState title="No tokens yet">
            Create a token so a remote MCP client can read with a scoped token instead of the open
            access mode.
          </EmptyState>
        )}
        <ul className="panel" aria-label="MCP read tokens">
          {data.tokens.map((token) => (
            <li key={token.id} className="mb-3 border rounded-md border-ink-line bg-ink p-4"
                aria-labelledby={`token-${token.id}-h`}>
              <div className="flex flex-wrap items-baseline justify-between gap-2 mb-1">
                <div>
                  <h2 id={`token-${token.id}-h`} className="text-lg font-bold">{token.name}</h2>
                  <p className="text-sm text-paper-muted">{scopeSummary(token.scope)}</p>
                </div>
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
                  <dt className="text-paper-muted">Status</dt>
                  <dd><StatusBadge status={tokenStatus(token)} /></dd>
                  <dt className="text-paper-muted">Created</dt>
                  <dd><span title={localTime(token.created_at)}>{localTime(token.created_at) || "—"}</span></dd>
                  <dt className="text-paper-muted">Expires</dt>
                  <dd>{token.expires_at ? (
                    <span title={localTime(token.expires_at)}>{localTime(token.expires_at)}</span>
                  ) : "never"}</dd>
                  <dt className="text-paper-muted">Last used</dt>
                  <dd>{token.last_used_at ? (
                    <span title={localTime(token.last_used_at)}>{localTime(token.last_used_at)}</span>
                  ) : "never"}</dd>
                </dl>
              </div>
              <ErrorNote error={revokeError} />
              {revoking && revoking.id === token.id ? (
                <div className="mt-3 rounded-md border border-bad/40 bg-bad/5 p-3 text-sm">
                  <p>
                    Revoking a token stops it working immediately. This can't be undone. Type the
                    token's name to confirm.
                  </p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <input className="field h-11 w-48" value={revokeInput}
                           onChange={(e) => setRevokeInput(e.target.value)}
                           placeholder={token.name} aria-label={`Confirm revoke ${token.name}`} />
                    <button className="btn btn-danger h-11" disabled={revokeBusy || revokeInput !== token.name}
                            onClick={doRevoke}>
                      Revoke {token.name}
                    </button>
                    <button className="btn h-11" type="button" onClick={cancelRevoke}>Cancel</button>
                  </div>
                </div>
              ) : (
                <div className="mt-3 flex flex-wrap gap-2">
                  <button className="btn btn-danger h-11" type="button" onClick={() => revokeToken(token.id, token.name)}>
                    Revoke
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>

        <div className="mt-4 flex gap-2">
          <button className="btn btn-primary h-11" type="button" disabled={busy || mode === "new"}
                  onClick={() => setMode("new")}>
            New token
          </button>
          {mode === "new" && (
            <button className="btn h-11" type="button" onClick={() => setMode(null)}>Cancel</button>
          )}
        </div>
      </section>
    </>
  );
}
