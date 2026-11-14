import { useState } from "react";
import { api } from "../api/client.js";
import { useApi } from "../api/useApi.js";
import GeoCredit from "../components/GeoCredit.jsx";
import HelpTip from "../components/HelpTip.jsx";
import LocationForm from "../components/LocationForm.jsx";
import { ErrorNote, Loading, PageHeader } from "../components/ui.jsx";

const GROUPS = [
  { title: "Database", match: (k) => k.startsWith("db_") },
  { title: "Sign-in", match: (k) => k.startsWith("admin_") || k.includes("ttl") || k === "cookie_secure" || k === "future_skew_seconds" },
  { title: "Limits", match: (k) => k.includes("_rate") || k.startsWith("max_") },
  { title: "Data retention", match: (k) => k.startsWith("retention_") },
];

const LABELS = {
  db_host: "Host", db_port: "Port", db_name: "Database", db_user: "User",
  admin_username: "Admin username", token_ttl_seconds: "Member token lifetime",
  admin_idle_minutes: "Admin sign-out after inactivity (default)", admin_session_max_hours: "Admin session maximum (default)",
  admin_warn_minutes: "Admin warning before sign-out (default)", admin_idle_max_minutes: "Admin inactivity ceiling",
  challenge_ttl_seconds: "Login challenge lifetime",
  cookie_secure: "Cookie requires HTTPS", future_skew_seconds: "Allowed clock skew (future)",
  ingest_rate: "Writes per member", recall_rate: "Reads per member", enroll_rate_per_ip: "Enrollment requests per IP",
  max_batch: "Records per batch", max_recall: "Records per read", max_pending_enrollments: "Pending enrollments",
  retention_payload_rules: "Payloads purged after", retention_audit_days: "Audit log kept",
  enable_scheduler: "Scheduled jobs", dashboard_dir: "Dashboard files",
};

function duration(seconds) {
  if (seconds % 86400 === 0) return `${seconds / 86400} days`;
  if (seconds % 3600 === 0) return `${seconds / 3600} h`;
  if (seconds % 60 === 0) return `${seconds / 60} min`;
  return `${seconds} s`;
}

function describeRule(rule) {
  const what = [rule.type ?? "any type", rule.tag ? `tagged ${rule.tag}` : null].filter(Boolean).join(" ");
  return `${what}: ${rule.days} days`;
}

export function formatValue(key, value) {
  if (Array.isArray(value)) return value.length ? value.map(describeRule).join(" · ") : "Never";
  if (typeof value === "boolean") return value ? "On" : "Off";
  if (value === "" || value === null || value === undefined) return "Not set";
  if (typeof value === "number") {
    if (key.endsWith("_seconds")) return duration(value);
    if (key.endsWith("_minutes")) return duration(value * 60);
    if (key.endsWith("_hours")) return duration(value * 3600);
    if (key.endsWith("_days")) return `${value} days`;
    if (key === "enroll_rate_per_ip") return `${value} per hour`;
    if (key.endsWith("_rate")) return `${value} per second`;
  }
  return String(value);
}

export function groupSettings(settings) {
  const used = new Set();
  const out = GROUPS.map((g) => {
    const keys = Object.keys(settings).filter((k) => !used.has(k) && g.match(k));
    keys.forEach((k) => used.add(k));
    return { title: g.title, keys };
  });
  out.push({ title: "Other", keys: Object.keys(settings).filter((k) => !used.has(k)) });
  return out.filter((g) => g.keys.length);
}

export const GEO_HELP = (manualUrl) => (
  <>
    Estimates come from a city database file kept on this server: LTMS never sends members&apos; addresses
    to an online service. Turning estimates on downloads DB-IP&apos;s free City Lite file from db-ip.com,
    which only learns that this server fetched the file; LTMS fetches a new one every month. On a closed
    network, download the MMDB file by hand from {manualUrl} and upload it here. Estimates are city-level
    at best, and private and local addresses are never placed.
  </>
);

function mb(bytes) {
  return bytes ? `${(bytes / (1024 * 1024)).toFixed(0)} MB` : null;
}

function when(iso) {
  return iso ? new Date(iso).toLocaleString() : "never";
}

/** The one-line state of location estimates (#346, #388). */
export function geoLine(geo, src) {
  if (src?.env) return `On: set by the server's environment (LTMS_GEOIP_DB = ${src.env_path}).`;
  if (src?.on) {
    const bits = [src.provider ? `${src.provider} city database` : "city database",
      src.build_date ? `built ${src.build_date}` : null, mb(src.size)].filter(Boolean);
    return `On: ${bits.join(", ")}.`;
  }
  if (geo?.enabled) return `On: city database at ${geo.path}.`;
  return `Off: ${geo?.reason || "no city database"}.`;
}

/** Location estimates: status, and (#388) the controls to turn them on, upload a file, or turn them off. */
export function GeoStatus({ geo, src, onChanged }) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [file, setFile] = useState(null);
  const [provider, setProvider] = useState("DB-IP");
  const [licence, setLicence] = useState("CC BY 4.0");
  const [confirmDelete, setConfirmDelete] = useState(false);
  if (!geo && !src) return null;

  async function act(name, fn) {
    setBusy(name);
    setError(null);
    try {
      await fn();
      onChanged?.();
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy(null);
    }
  }
  const download = () => act("download", () => api("/admin/geoip/download", { method: "POST" }));
  const upload = (e) => {
    e.preventDefault();
    if (!file) { setError("Choose a .mmdb or .mmdb.gz file."); return; }
    const body = new FormData();
    body.append("file", file);
    body.append("provider", provider);
    if (licence) body.append("licence", licence);
    act("upload", () => api("/admin/geoip/upload", { method: "POST", body }));
  };
  const off = (del) => act("off", async () => {
    await api("/admin/geoip/off", { method: "POST", body: { delete: del } });
    setConfirmDelete(false);
  });
  const controls = src && !src.env;
  const manual = src?.manual_url || "https://db-ip.com/db/download/ip-to-city-lite";

  return (
    <section className="panel mb-6 p-4" aria-labelledby="geo-h">
      <h2 id="geo-h" className="font-bold">
        Location estimates <HelpTip id="geo-about" label="About location estimates">{GEO_HELP(manual)}</HelpTip>
      </h2>
      <p className="text-sm" role="status">{geoLine(geo, src)}</p>
      {src && !src.env && (
        <dl className="mt-1 grid max-w-prose grid-cols-[auto_minmax(0,1fr)] gap-x-4 text-xs text-paper-muted">
          {src.source && (<><dt>Source</dt><dd className="truncate" title={src.source}>{src.source}</dd></>)}
          {src.licence && (<><dt>Licence</dt><dd>{src.licence}</dd></>)}
          <dt>Last check</dt><dd>{when(src.last_check)}</dd>
          {src.mode === "download" && src.next_check && (<><dt>Next check</dt><dd>{when(src.next_check)}</dd></>)}
        </dl>
      )}
      {src?.error && <p className="mt-1 text-xs text-bad">Last attempt failed: {src.error}</p>}
      <GeoCredit geo={src} className="mt-1" />
      {controls && (
        <div className="mt-3 space-y-3">
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn btn-primary" disabled={!!busy} onClick={download}>
              {busy === "download" ? "Downloading…" : src.mode === "download" ? "Download the latest now"
                : "Turn on network estimates (download DB-IP City Lite)"}
            </button>
            {src.on && !confirmDelete && (
              <>
                <button type="button" className="btn" disabled={!!busy} onClick={() => off(false)}>Turn off</button>
                <button type="button" className="btn btn-danger" disabled={!!busy}
                        onClick={() => setConfirmDelete(true)}>Turn off and delete the file</button>
              </>
            )}
            {!src.on && src.file_kept && !confirmDelete && (
              <button type="button" className="btn btn-danger" disabled={!!busy}
                      onClick={() => setConfirmDelete(true)}>Delete the kept file</button>
            )}
            {confirmDelete && (
              <span className="flex flex-wrap items-center gap-2 text-sm" role="group" aria-label="Confirm deletion">
                Delete the city database file? Estimates stop until you download or upload one again.
                <button type="button" className="btn btn-danger" disabled={!!busy} onClick={() => off(true)}>Delete</button>
                <button type="button" className="btn" onClick={() => setConfirmDelete(false)}>Cancel</button>
              </span>
            )}
          </div>
          <form className="flex flex-wrap items-end gap-2 text-sm" onSubmit={upload} aria-label="Upload a city database">
            <label className="flex flex-col gap-1">
              <span className="text-xs text-paper-muted">Upload a .mmdb or .mmdb.gz file</span>
              <input type="file" accept=".mmdb,.gz,application/gzip" className="field"
                     onChange={(e) => setFile(e.target.files?.[0] || null)} />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs text-paper-muted">Provider</span>
              <input className="field w-32" value={provider} onChange={(e) => setProvider(e.target.value)} required />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs text-paper-muted">Licence</span>
              <input className="field w-36" value={licence} onChange={(e) => setLicence(e.target.value)} />
            </label>
            <button type="submit" className="btn" disabled={!!busy}>{busy === "upload" ? "Uploading…" : "Upload"}</button>
          </form>
        </div>
      )}
      {error && <p className="mt-2 text-sm text-bad" role="alert">{error}</p>}
      <p className="mt-2 max-w-prose text-xs text-paper-muted">
        LTMS can place a member from the public address it connects from, using a city database kept on this
        server. Nothing is looked up online. Members on a local network are placed through their hub, or by
        their swarm&apos;s declared home.
      </p>
    </section>
  );
}

/** Check the admin session form; returns an error message or null (#378). */
export function validateSessionForm(form, limits) {
  const idle = Number(form.idle_minutes);
  const max = Number(form.max_hours);
  const warn = Number(form.warn_minutes);
  if (![idle, max, warn].every(Number.isInteger)) return "Use whole numbers.";
  if (idle < limits.idle_minutes_min || idle > limits.idle_minutes_max)
    return `Sign out after inactivity must be ${limits.idle_minutes_min}–${limits.idle_minutes_max} minutes.`;
  if (max < limits.max_hours_min || max > limits.max_hours_max)
    return `Maximum session length must be ${limits.max_hours_min}–${limits.max_hours_max} hours.`;
  if (max * 60 < idle) return "Maximum session length must be at least the inactivity timeout.";
  if (warn < limits.warn_minutes_min || warn > limits.warn_minutes_max)
    return `Warn ${limits.warn_minutes_min}–${limits.warn_minutes_max} minutes before sign-out.`;
  if (warn >= idle) return "The warning must come before the inactivity timeout.";
  return null;
}

function SessionForm({ current, onSaved }) {
  const [form, setForm] = useState({
    idle_minutes: String(current.idle_minutes), max_hours: String(current.max_hours),
    warn_minutes: String(current.warn_minutes),
  });
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const lim = current.limits;

  async function submit(e) {
    e.preventDefault();
    setSaved(false);
    const problem = validateSessionForm(form, lim);
    if (problem) { setError(problem); return; }
    setBusy(true);
    setError(null);
    try {
      await api("/admin/sessions/settings", { method: "PUT", body: {
        idle_minutes: Number(form.idle_minutes), max_hours: Number(form.max_hours), warn_minutes: Number(form.warn_minutes),
      } });
      setSaved(true);
      onSaved?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const field = (key, label, min, max, unit) => (
    <label className="block text-sm">
      {label}
      <span className="mt-1 flex items-center gap-2">
        <input className="field w-24" type="number" inputMode="numeric" min={min} max={max} step="1" required
               value={form[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value })} />
        <span className="text-paper-muted">{unit} ({min}–{max})</span>
      </span>
    </label>
  );

  return (
    <form onSubmit={submit} aria-label="Admin sessions" className="grid gap-3 sm:grid-cols-3">
      {field("idle_minutes", "Sign out after inactivity", lim.idle_minutes_min, lim.idle_minutes_max, "min")}
      {field("max_hours", "Maximum session length", lim.max_hours_min, lim.max_hours_max, "h")}
      {field("warn_minutes", "Warn before sign-out", lim.warn_minutes_min, lim.warn_minutes_max, "min")}
      <div className="flex items-center gap-3 sm:col-span-3">
        <button className="btn btn-primary" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
        {saved && <span role="status" className="text-sm">Saved. Open sessions use it from their next request.</span>}
        {error && <span role="alert" className="text-sm text-bad">{error}</span>}
      </div>
    </form>
  );
}

/** Settings → Admin sessions: idle timeout, maximum length, warning (#378). */
export function AdminSessions() {
  const { data, error, reload } = useApi("/admin/sessions/settings");
  return (
    <section className="panel mb-6 p-4" aria-labelledby="admin-sessions-h">
      <h2 id="admin-sessions-h" className="font-bold">Admin sessions</h2>
      <p className="mb-3 max-w-prose text-sm text-paper-muted">
        Admins are signed out after a period without activity (clicks, typing, scrolling, pages they open),
        and always after the maximum length. Background refreshes don&apos;t count as activity.
      </p>
      <ErrorNote error={error} onRetry={reload} />
      {data && <SessionForm key={JSON.stringify([data.idle_minutes, data.max_hours, data.warn_minutes])}
                            current={data} onSaved={reload} />}
      {data && (
        <p className="mt-3 max-w-prose text-xs text-paper-muted">
          A longer inactivity timeout keeps an unattended, signed-in browser usable for longer. Check it against
          your site&apos;s security policy (on government networks, STIG/ATO session-lock controls may set a lower
          limit). The server can cap it with <code className="hash">LTMS_ADMIN_IDLE_MAX_MINUTES</code>
          {data.limits.idle_minutes_max < 480 && <> (capped here at {data.limits.idle_minutes_max} min)</>}.
          {" "}Current values come from {data.source === "dashboard" ? "this page" : "the server environment"}.
        </p>
      )}
    </section>
  );
}

export default function Settings() {
  const { data, error, loading, reload } = useApi("/admin/settings");
  const locations = useApi("/admin/locations");
  const geoip = useApi("/admin/geoip");
  return (
    <>
      <PageHeader title="Settings" />
      <p className="mb-6 max-w-prose text-sm text-paper-muted">
        The server's current configuration. Settings change through environment variables on the server
        (<code className="hash">/etc/ltms/ltms.env</code>, defaults in <code className="hash">config/default.yaml</code>)
        and take effect after a restart. Passwords are never shown.
      </p>
      <section className="panel mb-6 p-4" aria-labelledby="server-location-h">
        <h2 id="server-location-h" className="font-bold">Server location</h2>
        <p className="text-sm text-paper-muted">Where this LTMS server is. The Network tab&apos;s globe draws data flowing here.</p>
        <LocationForm key={JSON.stringify(locations.data?.server ?? null)} subjectType="member" name="ltms-server"
                      title="Server" current={locations.data?.server ?? null} onDone={locations.reload} />
      </section>
      <AdminSessions />
      <GeoStatus geo={locations.data?.geo} src={geoip.data}
                 onChanged={() => { geoip.reload(); locations.reload(); }} />
      {loading && <Loading />}
      <ErrorNote error={error} onRetry={reload} />
      {data && (
        <div className="grid gap-4 lg:grid-cols-2">
          {groupSettings(data).map((g) => (
            <section key={g.title} className="panel p-4" aria-labelledby={`set-${g.title}`}>
              <h2 id={`set-${g.title}`} className="mb-3 font-bold">{g.title}</h2>
              <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1.5 text-sm">
                {g.keys.map((k) => (
                  <div key={k} className="contents">
                    <dt className="text-paper-muted" title={k}>{LABELS[k] || k}</dt>
                    <dd className="text-right">{formatValue(k, data[k])}</dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
      )}
    </>
  );
}
