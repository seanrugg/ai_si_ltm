import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { useApi } from "../api/useApi.js";
import { EmptyState, ErrorNote, Loading, PageHeader } from "../components/ui.jsx";
import { localTime } from "../lib/format.js";

const XAPI_VERSIONS = ["2.0.0", "1.0.3"];
const SCOPE_KEYS = ["types", "agents", "places", "swarms"];

const splitList = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);

function scopeFromForm(form) {
  const scope = {};
  for (const key of SCOPE_KEYS) scope[key] = splitList(form[key]);
  return scope;
}

function EndpointRow({ endpoint, backlog, onReload }) {
  const [mode, setMode] = useState(null); // null | "edit" | "delete" | "test"
  const [testResult, setTestResult] = useState(null);
  const [testBusy, setTestBusy] = useState(false);
  const [confirmName, setConfirmName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function act(method, path, body) {
    setBusy(true);
    setError(null);
    try {
      await api(path, { method, body });
      setMode(null);
      onReload();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  function toggleEnabled() {
    act("PATCH", `/admin/lrs/${endpoint.id}`, { enabled: !endpoint.enabled });
  }

  function testConnection() {
    setMode("test");
    setTestResult(null);
    setTestBusy(true);
    api(`/admin/lrs/${endpoint.id}/test`, { method: "POST" })
      .then((result) => { setTestResult(result); setTestBusy(false); })
      .catch((err) => { setError(err); setTestBusy(false); });
  }

  return (
    <li key={endpoint.id} className="mb-4 border rounded-md border-ink-line bg-ink p-4" aria-labelledby={`lrs-${endpoint.name}-h`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2 mb-1">
        <div>
          <h2 id={`lrs-${endpoint.name}-h`} className="text-lg font-bold">{endpoint.name}</h2>
          <p className="text-sm text-paper-muted">{endpoint.url}</p>
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
          <dt className="text-paper-muted">xAPI version</dt>
          <dd>{endpoint.xapi_version}</dd>
          <dt className="text-paper-muted">Enabled</dt>
          <dd>
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" className="accent-led" checked={endpoint.enabled}
                     onChange={toggleEnabled} aria-label={`Enable ${endpoint.name}`} />
              {endpoint.enabled ? "on" : "off"}
            </label>
          </dd>
          <dt className="text-paper-muted">Backlog</dt>
          <dd>{backlog === null ? "—" : String(backlog ?? 0)}</dd>
          <dt className="text-paper-muted">Last success</dt>
          <dd>{endpoint.last_success_at ? <span title={localTime(endpoint.last_success_at)}>{localTime(endpoint.last_success_at)}</span> : "never"}</dd>
        </dl>
      </div>

      {endpoint.last_error && (
        <p className="mb-2 text-xs text-bad" role="status" aria-label="Last error">
          Last error: {endpoint.last_error}
        </p>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        <button className="btn" onClick={testConnection} disabled={testBusy}>
          {testBusy ? "Testing…" : "Test connection"}
        </button>
        {testResult && (
          <p className="mt-1 text-xs text-paper-muted" aria-label="Test result">
            ok={String(testResult.ok)} status={testResult.status ?? "—"}
            {testResult.versions && testResult.versions.length > 0
              ? ` versions=${testResult.versions.join(",")}`
              : ""
            }
            {testResult.error ? ` error=${testResult.error}` : ""}
          </p>
        )}
        {mode === null && (
          <>
            <button className="btn" onClick={() => setMode("edit")}>Edit</button>
            <button className="btn btn-danger" onClick={() => setMode("delete")}>Delete</button>
          </>
        )}
      </div>

      <ErrorNote error={error} />

      {mode === "edit" && (
        <EndpointForm initial={endpoint} onSaved={() => { setMode(null); onReload(); }} onCancel={() => setMode(null)} busy={busy} />
      )}
      {mode === "delete" && (
        <div className="mt-3 rounded-md border border-bad/40 bg-bad/5 p-3 text-sm">
          <p>Deleting an endpoint stops forwarding to it. This can't be undone. Type the endpoint's name to confirm.</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <input className="field w-48" value={confirmName}
                   onChange={(e) => setConfirmName(e.target.value)}
                   placeholder={endpoint.name} aria-label={`Confirm delete ${endpoint.name}`} />
            <button className="btn btn-danger" disabled={busy || confirmName !== endpoint.name}
                    onClick={() => act("DELETE", `/admin/lrs/${endpoint.id}`)}>
              Delete {endpoint.name}
            </button>
            <button className="btn" onClick={() => setMode(null)}>Cancel</button>
          </div>
        </div>
      )}
    </li>
  );
}

function EndpointForm({ initial, onSaved, onCancel, busy }) {
  const [form, setForm] = useState({
    name: initial?.name || "",
    url: initial?.url || "",
    xapi_version: initial?.xapi_version || "2.0.0",
    username: initial?.username || "",
    password: "",
    clearPassword: false,
    types: (initial?.scope?.types || []).join(", "),
    agents: (initial?.scope?.agents || []).join(", "),
    places: (initial?.scope?.places || []).join(", "),
    swarms: (initial?.scope?.swarms || []).join(", "),
    enabled: initial?.enabled || false,
  });
  const [error, setError] = useState(null);

  async function save() {
    setError(null);
    const body = {
      name: form.name.trim(),
      url: form.url.trim(),
      username: form.username.trim(),
      xapi_version: form.xapi_version,
      scope: scopeFromForm(form),
      enabled: form.enabled,
    };
    if (initial) {
      if (form.clearPassword) body.secret = "";
      else if (form.password) body.secret = form.password;
    } else if (form.password || form.clearPassword) {
      body.secret = form.clearPassword ? "" : form.password;
    }
    try {
      if (initial) {
        await api(`/admin/lrs/${initial.id}`, { method: "PATCH", body });
      } else {
        const created = await api("/admin/lrs", { method: "POST", body });
        // The create API doesn't take "enabled" (endpoints start disabled).
        if (form.enabled && created?.id) {
          await api(`/admin/lrs/${created.id}`, { method: "PATCH", body: { enabled: true } });
        }
      }
      onSaved();
    } catch (err) {
      setError(err);
    }
  }

  const heading = initial ? "Edit endpoint" : "Add endpoint";
  const fid = initial ? `lrs-${initial.id}` : "lrs-new";

  return (
    <section className="mb-6 border rounded-md border-ink-line bg-ink p-4" aria-labelledby={`${fid}-h`}>
      <h2 id={`${fid}-h`} className="mb-3 text-lg font-bold">{heading}</h2>
      <form onSubmit={(e) => { e.preventDefault(); save(); }}
            className="mb-4 flex flex-wrap items-end gap-2" aria-label={heading}>
        <label htmlFor={`${fid}-name`} className="block text-sm text-paper-muted">Name</label>
        <input className="field w-48" value={form.name}
               onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
               placeholder="sql-lrs" id={`${fid}-name`} required />
        <label htmlFor={`${fid}-url`} className="block text-sm text-paper-muted">URL</label>
        <input className="field w-64" value={form.url}
               onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))}
               placeholder="https://lrs.example.org/xapi" id={`${fid}-url`} required />
        <label htmlFor={`${fid}-xapi`} className="block text-sm text-paper-muted">xAPI version</label>
        <select className="field" value={form.xapi_version}
                onChange={(e) => setForm((f) => ({ ...f, xapi_version: e.target.value }))}
                id={`${fid}-xapi`}>
          {XAPI_VERSIONS.map((v) => <option key={v} value={v}>{v}</option>)}
        </select>
        <label htmlFor={`${fid}-username`} className="block text-sm text-paper-muted">Username</label>
        <input className="field w-40" value={form.username}
               onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))}
               placeholder="admin" id={`${fid}-username`} />
        <div className="flex flex-wrap items-end gap-2">
          <div>
            <label htmlFor={`${fid}-password`} className="block text-sm text-paper-muted">Password (write-only)</label>
            <input className="field w-48 mb-1" type="password" value={form.password}
                   onChange={(e) => setForm((f) => ({ ...f, password: e.target.value, clearPassword: false }))}
                   placeholder="leave blank to keep" id={`${fid}-password`} />
            <p className="text-xs text-paper-muted">
              Never prefilled. Leave blank to keep the current one.
              {initial ? ` Password set: ${initial.has_secret ? "yes" : "no"}.` : ""}
            </p>
            <label className="flex items-center gap-1 text-sm text-paper-muted cursor-pointer">
              <input type="checkbox" checked={form.clearPassword}
                     onChange={(e) => setForm((f) => ({ ...f, clearPassword: e.target.checked, password: "" }))}
                     className="accent-led" />
              Clear password
            </label>
          </div>
        </div>
        <div className="mt-2 flex flex-wrap gap-x-4">
          {SCOPE_KEYS.map((key) => (
            <div key={key} className="flex flex-col gap-1">
              <label htmlFor={`${fid}-${key}`} className="text-sm text-paper-muted">{key}</label>
              <input className="field w-32" value={form[key]}
                     onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
                     placeholder={key} id={`${fid}-${key}`} />
            </div>
          ))}
        </div>
        <p className="mt-1 text-xs text-paper-muted">Comma-separated values per field. An empty <b>types</b> field means nothing is exported.</p>
        <label className="flex items-center gap-2 text-sm mt-2 cursor-pointer">
          <input type="checkbox" checked={form.enabled}
                 onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))} className="accent-led" />
          Enabled
        </label>
        <button type="submit" className="btn btn-primary" disabled={busy}>Save</button>
        <button className="btn" type="button" onClick={onCancel} disabled={busy}>Cancel</button>
      </form>
      <ErrorNote error={error} />
    </section>
  );
}

export default function Lrs() {
  const { data, error, loading, reload } = useApi("/admin/lrs");
  const endpoints = data?.endpoints || [];
  const [backlog, setBacklog] = useState({});

  // Depend on the fetched data, not a derived array: a new array each render
  // re-ran this effect (and its requests) forever.
  useEffect(() => {
    const list = data?.endpoints || [];
    let cancelled = false;
    Promise.all(list.map((ep) =>
      api(`/admin/lrs/${encodeURIComponent(ep.id)}/status`)
        .then((d) => [ep.id, d.backlog ?? 0])
        .catch(() => [ep.id, null]),
    )).then((pairs) => { if (!cancelled) setBacklog(Object.fromEntries(pairs)); });
    return () => { cancelled = true; };
  }, [data]);

  if (loading && !data) return <Loading what="Loading LRS endpoints" />;
  if (error) return <ErrorNote error={error} onRetry={reload} />;

  return (
    <>
      <PageHeader title="LRS endpoints" />
      <p className="mb-4 text-sm text-paper-muted">
        Optional. LTMS works without an LRS. Any xAPI-conformant LRS can be added.
      </p>
      <EndpointForm onSaved={reload} />

      {endpoints.length === 0 && (
        <EmptyState title="No LRS endpoints yet">
          Add an xAPI-conformant LRS (for example a Yet Analytics SQL LRS) so LTMS can forward
          in-scope records to it. LTMS works fine without one.
        </EmptyState>
      )}
      <ul className="panel" aria-label="LRS endpoints">
        {endpoints.map((ep) => (
          <EndpointRow key={ep.id} endpoint={ep} backlog={backlog[ep.id]} onReload={reload} />
        ))}
      </ul>
    </>
  );
}
