import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { useApi } from "../api/useApi.js";
import { EmptyState, ErrorNote, Loading, PageHeader, StatusBadge } from "../components/ui.jsx";
import { localTime, plural } from "../lib/format.js";

const RECORD_TYPES = [
  "action", "communication", "decision", "event", "lesson",
  "perception", "policy", "system", "thought",
];

const MODE_LABELS = {
  off: ["Off", "No MCP reads are allowed."],
  token: ["Token", "Only a presented, valid read token grants access."],
  open: ["Open", "Anyone can read the open scope (headers only, never payloads)."],
};

const splitList = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);

const POLICY_LABELS = {
  manual: ["Manual", "Requests wait for an admin to approve or reject."],
  auto_deny: ["Auto-deny", "New requests are refused at once (403 enrollment_closed)."],
  auto_approve: ["Auto-approve", "Free-agent requests are approved at once with recall only."],
};

// A human readable summary of a token scope, e.g. "types: event, thought · payloads".
function scopeSummary(scope) {
  if (!scope) return "—";
  const parts = [];
  for (const key of ["types", "agents", "swarms"]) {
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

/** The settings form's starting values from the server's effective settings. */
export function settingsFormFrom(data) {
  const scope = data?.open_scope || {};
  return {
    mode: data?.mode ?? "off",
    types: new Set(scope.types ?? []),
    swarms: Array.isArray(scope.swarms) ? scope.swarms.filter((s) => s !== null).join(", ") : "",
  };
}

export default function Access() {
  const { data, error, loading, reload } = useApi("/admin/mcp");
  const { data: policyData, error: policyApiError, loading: policyLoading, reload: reloadPolicy } = useApi("/admin/enrollment/policy");
  // null | "new": whether the "new token" form is open.
  const [mode, setMode] = useState(null);
  const [form, setForm] = useState({ name: "", types: "", agents: "", swarms: "", payloads: false, expires: "", owner: "", purpose: "" });
  // Shown exactly once after creation, then cleared on reload.
  const [shown, setShown] = useState(null);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState(null);
  // { id, name } of the token awaiting revoke confirmation.
  const [revoking, setRevoking] = useState(null);
  const [revokeInput, setRevokeInput] = useState("");
  const [revokeBusy, setRevokeBusy] = useState(false);
  const [revokeError, setRevokeError] = useState(null);

  // MCP settings form state.
  const [settingsMode, setSettingsMode] = useState(data?.mode ?? "off");
  const [openTypes, setOpenTypes] = useState(new Set(data?.open_scope?.types ?? []));
  const [openSwarms, setOpenSwarms] = useState(
    Array.isArray(data?.open_scope?.swarms) ? data.open_scope.swarms.join(", ") : "",
  );
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [settingsError, setSettingsError] = useState(null);
  const [settingsMessage, setSettingsMessage] = useState(null);
  // The settings arrive after the first render (useApi), so sync the form from
  // the server's effective values whenever they load or change. Without this
  // the radios start at "off" and a Save would overwrite the real mode.
  const loadedMode = data?.mode;
  const loadedScope = JSON.stringify(data?.open_scope ?? null);

  // Enrollment policy form state, synced from the server's effective policy below.
  const [policy, setPolicy] = useState(policyData?.policy ?? "manual");
  const [policyConfirmOpen, setPolicyConfirmOpen] = useState(false);
  const [policyBusy, setPolicyBusy] = useState(false);
  const [policyError, setPolicyError] = useState(null);
  const [policyMessage, setPolicyMessage] = useState(null);
  useEffect(() => {
    if (loadedMode === undefined) return;
    const f = settingsFormFrom({ mode: loadedMode, open_scope: JSON.parse(loadedScope) });
    setSettingsMode(f.mode);
    setOpenTypes(f.types);
    setOpenSwarms(f.swarms);
    setConfirmOpen(false);
  }, [loadedMode, loadedScope]);

  // Sync the enrollment-policy form from the server's effective policy whenever it
  // loads or changes, so a Save never overwrites the real policy (the #127 bug).
  const loadedPolicy = policyData?.policy;
  useEffect(() => {
    if (loadedPolicy === undefined) return;
    setPolicy(loadedPolicy);
    setPolicyConfirmOpen(false);
    setPolicyError(null);
    setPolicyMessage(null);
  }, [loadedPolicy]);

  const fid = "access-new-token";

  function toggleType(type) {
    setOpenTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type); else next.add(type);
      return next;
    });
  }

  // Whether the proposed settings widen public access vs what's currently in effect.
  function wouldWiden() {
    const cur = data?.open_scope ?? { types: [], swarms: null };
    const curTypes = new Set(cur.types ?? []);
    const newTypes = openTypes;
    if (settingsMode === "open" && data?.mode !== "open") return true;
    if (settingsMode === "open" || data?.mode === "open") {
      if ([...newTypes].some((t) => !curTypes.has(t))) return true;
      const curSwarms = cur.swarms;
      const newSwarms = openSwarms.trim() ? splitList(openSwarms) : null;
      if (curSwarms !== null && newSwarms === null) return true;
      if (curSwarms !== null && newSwarms !== null && newSwarms.some((s) => !curSwarms.includes(s)))
        return true;
    }
    return false;
  }

  const showConfirm = settingsMode === "open" && wouldWiden();

  async function saveSettings() {
    setSettingsError(null);
    if (showConfirm && !confirmOpen) {
      setSettingsError("Confirm that you understand anyone on the internet can read these record summaries.");
      return;
    }
    const newSwarms = openSwarms.trim() ? splitList(openSwarms) : null;
    const body = {
      mode: settingsMode,
      open_scope: { types: [...openTypes], swarms: newSwarms },
    };
    if (showConfirm) body.confirm = "open";
    setSettingsBusy(true);
    try {
      await api("/admin/mcp/settings", { method: "PUT", body });
      setSettingsMessage("MCP settings saved.");
      setConfirmOpen(false);
      reload();
    } catch (err) {
      setSettingsError(err.message);
    } finally {
      setSettingsBusy(false);
    }
  }

  async function resetSettings() {
    setSettingsError(null);
    setSettingsBusy(true);
    try {
      await api("/admin/mcp/settings", { method: "DELETE" });
      setSettingsMessage("Reset to the environment default.");
      reload();
    } catch (err) {
      setSettingsError(err.message);
    } finally {
      setSettingsBusy(false);
    }
  }

  async function savePolicy() {
    setPolicyError(null);
    setPolicyMessage(null);
    if (policy === "auto_approve" && !policyConfirmOpen) {
      setPolicyError('Confirm that you understand anyone on the internet can join and read.');
      return;
    }
    const body = { policy };
    if (policy === "auto_approve") body.confirm = "auto_approve";
    setPolicyBusy(true);
    try {
      await api("/admin/enrollment/policy", { method: "PUT", body });
      setPolicyMessage("Enrollment policy saved.");
      setPolicyConfirmOpen(false);
      reloadPolicy();
    } catch (err) {
      setPolicyError(err.message);
    } finally {
      setPolicyBusy(false);
    }
  }

  async function createToken() {
    setFormError(null);
    const scope = {};
    if (form.payloads) scope.payloads = true;
    for (const key of ["types", "agents", "swarms"]) {
      const values = splitList(form[key]);
      if (values.length) scope[key] = values;
    }
    const body = { name: form.name.trim(), scope, expires_at: form.expires || null, owner: form.owner.trim() || null, purpose: form.purpose.trim() || null };
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
  const locked = Boolean(data.locked);

  return (
    <>
      <PageHeader title="MCP read access" />

      {/* ---- MCP mode and open scope ---- */}
      <section className="mb-8 rounded-md border border-ink-line bg-ink p-4" aria-labelledby="access-mode-h">
        <h2 id="access-mode-h" className="mb-3 text-lg font-bold">Mode</h2>

        {locked && (
          <p className="mb-4 text-sm text-paper-muted">
            Locked by the server environment (<code className="hash">LTMS_MCP_SETTINGS_LOCKED</code>). The dashboard
            shows the effective settings but cannot change them; edit the environment and redeploy instead.
          </p>
        )}

        <fieldset disabled={locked}>
          <legend className="mb-2 text-sm font-bold text-paper-muted">Read mode</legend>
          <div className="space-y-2">
            {Object.entries(MODE_LABELS).map(([key, [label, desc]]) => {
              const checked = settingsMode === key;
              return (
                <label key={key} className="flex items-start gap-3 cursor-pointer">
                  <input type="radio" name="access-mode" value={key} checked={checked}
                         onChange={() => setSettingsMode(key)}
                         className="accent-led mt-0.5" />
                  <div>
                    <span className={`font-semibold ${locked ? "text-paper-muted" : ""}`}>{label}</span>{" "}
                    <span className="text-xs text-paper-muted">{desc}</span>
                  </div>
                </label>
              );
            })}
          </div>
        </fieldset>

        {settingsMode === "open" && (
          <fieldset disabled={locked}>
            <legend className="mt-6 mb-2 text-sm font-bold text-paper-muted">Open scope (headers only, never payloads)</legend>
            <div className="space-y-3">
              <div>
                <p className="mb-2 text-sm text-paper-muted">Record types</p>
                <div className="grid grid-cols-3 gap-2 text-sm">
                  {RECORD_TYPES.map((type) => {
                    const selected = openTypes.has(type);
                    return (
                      <label key={type} className="flex items-center gap-2 cursor-pointer">
                        <input type="checkbox" checked={selected}
                               onChange={() => toggleType(type)}
                               className="accent-led h-5 w-5" />
                        {type}
                      </label>
                    );
                  })}
                </div>
              </div>
              <div>
                <label className="block text-sm text-paper-muted mb-1">Swarms (comma-separated, empty = all)</label>
                <input className="field h-11 w-64" value={openSwarms}
                       onChange={(e) => setOpenSwarms(e.target.value)}
                       placeholder="echo-base, alpha" disabled={locked} />
                <p className="mt-1 text-xs text-paper-muted">
                  Leave empty to include every swarm. List swarms to restrict open reads to them.
                </p>
              </div>
            </div>
          </fieldset>
        )}

        {showConfirm && (
          <fieldset disabled={locked}>
            <legend className="mt-6 mb-2 text-sm font-bold text-paper-muted">Confirm</legend>
            <label className="flex items-start gap-3 cursor-pointer">
              <input type="checkbox" checked={confirmOpen}
                     onChange={(e) => setConfirmOpen(e.target.checked)}
                     className="accent-led mt-0.5 h-5 w-5" disabled={locked} />
              <div>
                <span className="font-semibold">I understand anyone on the internet can read these record summaries.</span>
                <p className="mt-1 text-xs text-paper-muted">
                  Payloads are never part of the open scope. Headers only.
                </p>
              </div>
            </label>
          </fieldset>
        )}

        <div className="mt-4 flex flex-wrap gap-2">
          {!locked && (
            <>
              <button type="button" className="btn btn-primary h-11" disabled={settingsBusy}
                      onClick={saveSettings}>
                Save settings
              </button>
              <button type="button" className="btn h-11" disabled={settingsBusy}
                      onClick={resetSettings}>
                Reset to environment default
              </button>
              {settingsMessage && (
                <span className="mt-3 text-xs text-ok">{settingsMessage}</span>
              )}
            </>
          )}
          {settingsError && <ErrorNote error={settingsError} />}
        </div>

        <dl className="mt-5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs text-paper-muted">
          <dt>Source</dt>
          <dd className="font-mono">{data.source}</dd>
          <dt>Last changed</dt>
          <dd>
            {data.updated_by && data.updated_at
              ? `by ${data.updated_by} at ${localTime(data.updated_at)}`
              : "— (environment default)"}
          </dd>
          {locked && (
            <>
              <dt>Locked</dt>
              <dd>Yes — by the server environment</dd>
            </>
          )}
        </dl>
      </section>

      {/* ---- Enrollment policy ---- */}
      <section className="mb-8 rounded-md border border-ink-line bg-ink p-4" aria-labelledby="enrollment-policy-h">
        <h2 id="enrollment-policy-h" className="mb-3 text-lg font-bold">Enrollment policy</h2>

        {policyLoading && !policyData && <Loading what="Loading enrollment policy" />}
        {policyApiError && <ErrorNote error={policyApiError} onRetry={reloadPolicy} />}
        {policyData && (
          <>
            {policyData.locked && (
              <p className="mb-4 text-sm text-paper-muted">
                Locked by the server environment (<code className="hash">LTMS_ENROLLMENT_POLICY_LOCKED</code>). The dashboard
                shows the effective policy but cannot change it; edit the environment and redeploy instead.
              </p>
            )}
            {policyData.policy === "auto_approve" && (
              <p className="mb-4 text-sm text-bad">
                Warning: Anyone on the internet can join and read.
              </p>
            )}

            <fieldset disabled={policyData.locked}>
              <legend className="mb-2 text-sm font-bold text-paper-muted">Policy</legend>
              <div className="space-y-2">
                {Object.entries(POLICY_LABELS).map(([key, [label, desc]]) => {
                  const checked = policy === key;
                  return (
                    <label key={key} className="flex items-start gap-3 cursor-pointer">
                      <input type="radio" name="enrollment-policy" value={key} checked={checked}
                             onChange={() => setPolicy(key)}
                             className="accent-led mt-0.5" />
                      <div>
                        <span className={`font-semibold ${policyData.locked ? "text-paper-muted" : ""}`}>{label}</span>{" "}
                        <span className="text-xs text-paper-muted">{desc}</span>
                      </div>
                    </label>
                  );
                })}
              </div>
            </fieldset>

            {policy === "auto_approve" && (
              <fieldset disabled={policyData.locked}>
                <legend className="mt-6 mb-2 text-sm font-bold text-paper-muted">Confirm</legend>
                <label className="flex items-start gap-3 cursor-pointer">
                  <input type="checkbox" checked={policyConfirmOpen}
                         onChange={(e) => setPolicyConfirmOpen(e.target.checked)}
                         className="accent-led mt-0.5 h-5 w-5" disabled={policyData.locked} />
                  <div>
                    <span className="font-semibold">I understand anyone on the internet can join and read.</span>
                    <p className="mt-1 text-xs text-paper-muted">
                      Free-agent requests are approved at once with recall only; they cannot write records or relay.
                    </p>
                  </div>
                </label>
              </fieldset>
            )}

            <div className="mt-4 flex flex-wrap gap-2">
              {!policyData.locked && (
                <>
                  <button type="button" className="btn btn-primary h-11" disabled={policyBusy}
                          onClick={savePolicy}>
                    Save policy
                  </button>
                  {policyMessage && (
                    <span className="mt-3 text-xs text-ok">{policyMessage}</span>
                  )}
                </>
              )}
              {policyError && <ErrorNote error={policyError} />}
            </div>

            <dl className="mt-5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs text-paper-muted">
              <dt>Source</dt>
              <dd className="font-mono">{policyData.source}</dd>
              <dt>Last changed</dt>
              <dd>
                {policyData.updated_by && policyData.updated_at
                  ? `by ${policyData.updated_by} at ${localTime(policyData.updated_at)}`
                  : "— (environment default)"}
              </dd>
              {policyData.locked && (
                <>
                  <dt>Locked</dt>
                  <dd>Yes — by the server environment</dd>
                </>
              )}
            </dl>

            {policyData.policy === "auto_deny" && (
              <p className="mt-4 text-sm text-paper-muted" id="enrollment-refusals">
                {plural(policyData.refusals.count, "request")} refused since{" "}
                {policyData.refusals.since ? localTime(policyData.refusals.since) : "—"}
              </p>
            )}
          </>
        )}
      </section>

      <p className="mb-4 text-sm text-paper-muted" id="access-mode-desc">
        Set the default with <code className="hash">LTMS_MCP_READ_MODE</code> and{" "}
        <code className="hash">LTMS_MCP_OPEN_SCOPE</code>. Dashboard changes take effect on the next
        MCP request — no restart needed — unless the server is locked.
      </p>

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
              <label htmlFor={`${fid}-owner`} className="block text-sm text-paper-muted">Owner</label>
              <input className="field h-11 w-48" value={form.owner}
                     onChange={(e) => setForm((f) => ({ ...f, owner: e.target.value }))}
                     placeholder="Sean (Claude connector)" id={`${fid}-owner`} />
              <label htmlFor={`${fid}-purpose`} className="block text-sm text-paper-muted">Purpose</label>
              <input className="field h-11 w-48" value={form.purpose}
                     onChange={(e) => setForm((f) => ({ ...f, purpose: e.target.value }))}
                     placeholder="Daily read of robot outcomes" id={`${fid}-purpose`} />
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
                  {token.owner && <p className="text-sm text-paper-muted">Owner: {token.owner}</p>}
                  {token.purpose && <p className="text-sm text-paper-muted">Purpose: {token.purpose}</p>}
                </div>
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
                  <dt className="text-paper-muted">Status</dt>
                  <dd><StatusBadge status={tokenStatus(token)} /></dd>
                  <dt className="text-paper-muted">Uses</dt>
                  <dd>{token.use_count ?? 0}</dd>
                  <dt className="text-paper-muted">Stale</dt>
                  <dd>{token.stale ? "Yes" : "No"}</dd>
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
