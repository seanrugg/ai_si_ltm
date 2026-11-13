import { useApi } from "../api/useApi.js";
import LocationForm from "../components/LocationForm.jsx";
import { ErrorNote, Loading, PageHeader } from "../components/ui.jsx";

const GROUPS = [
  { title: "Database", match: (k) => k.startsWith("db_") },
  { title: "Sign-in", match: (k) => k === "admin_username" || k.includes("ttl") || k === "cookie_secure" || k === "future_skew_seconds" },
  { title: "Limits", match: (k) => k.includes("_rate") || k.startsWith("max_") },
  { title: "Data retention", match: (k) => k.startsWith("retention_") },
];

const LABELS = {
  db_host: "Host", db_port: "Port", db_name: "Database", db_user: "User",
  admin_username: "Admin username", token_ttl_seconds: "Member token lifetime",
  admin_session_ttl_seconds: "Admin session lifetime", challenge_ttl_seconds: "Login challenge lifetime",
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

/** Whether LTMS can estimate locations from addresses (#346). */
export function GeoStatus({ geo }) {
  if (!geo) return null;
  return (
    <section className="panel mb-6 p-4" aria-labelledby="geo-h">
      <h2 id="geo-h" className="font-bold">Location estimates</h2>
      {geo.enabled ? (
        <p className="text-sm" role="status">On: city database at <code className="hash">{geo.path}</code>.</p>
      ) : (
        <p className="text-sm" role="status">Off: {geo.reason}.</p>
      )}
      <p className="mt-1 max-w-prose text-xs text-paper-muted">
        LTMS can place a member from the public address it connects from, using an offline city database
        (<code className="hash">pip install &quot;ltms[geo]&quot;</code> and <code className="hash">LTMS_GEOIP_DB</code>
        pointing at a <code className="hash">.mmdb</code> file; see docs/locations.md). Nothing is looked up online.
        Members on a local network are placed through their hub, or by their swarm&apos;s declared home.
      </p>
    </section>
  );
}

export default function Settings() {
  const { data, error, loading, reload } = useApi("/admin/settings");
  const locations = useApi("/admin/locations");
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
      <GeoStatus geo={locations.data?.geo} />
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
