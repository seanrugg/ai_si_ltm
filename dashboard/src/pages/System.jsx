// System status (#383, under Configure): read-only. Version, migrations,
// background jobs, fleet key and integrations, from GET /api/admin/system.
// The server builds the diagnostics text and keeps secrets, paths, IPs and
// member data out of it; this page only shows and copies it.
import { useState } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../api/useApi.js";
import HelpTip from "../components/HelpTip.jsx";
import { ErrorNote, Loading, PageHeader } from "../components/ui.jsx";
import { localTime, relativeTime } from "../lib/format.js";
import { SYSTEM_ABOUT } from "../lib/help.js";

/** "3 d 4 h", "5 h 2 min", "40 s". */
export function uptimeText(seconds) {
  if (seconds == null) return "unknown";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d) return `${d} d ${h} h`;
  if (h) return `${h} h ${m} min`;
  if (m) return `${m} min`;
  return `${seconds} s`;
}

/** "12.3 GB" for bytes, or "unknown". */
export function gb(bytes) {
  return bytes == null ? "unknown" : `${(bytes / 1e9).toFixed(1)} GB`;
}

function When({ iso }) {
  if (!iso) return <span className="text-paper-muted">—</span>;
  return <time dateTime={iso} title={localTime(iso)}>{relativeTime(iso)}</time>;
}

function Section({ title, children }) {
  return (
    <section className="card mb-4 p-4" aria-labelledby={`sys-${title}`}>
      <h2 id={`sys-${title}`} className="mb-3 text-base font-bold">{title}</h2>
      {children}
    </section>
  );
}

function Facts({ rows }) {
  return (
    <dl className="grid grid-cols-[12rem_1fr] gap-x-3 gap-y-1 text-sm">
      {rows.filter(Boolean).map(([k, v]) => (
        <div key={k} className="contents"><dt className="text-paper-muted">{k}</dt><dd>{v}</dd></div>
      ))}
    </dl>
  );
}

const STATE_TEXT = { ok: "OK", warning: "Needs a look", none: "None set up" };

export default function System() {
  const { data, error, loading, reload } = useApi("/admin/system");
  const [copied, setCopied] = useState(null);

  async function copy() {
    try {
      await navigator.clipboard.writeText(data.diagnostics);
      setCopied("Copied to the clipboard.");
    } catch {
      setCopied("Couldn't copy; select the text below instead.");
    }
  }

  return (
    <>
      <PageHeader title="System status" help={<HelpTip id="system-about" label="About this page">{SYSTEM_ABOUT}</HelpTip>}>
        <button type="button" className="btn" onClick={reload}>Refresh</button>
        {data && <button type="button" className="btn" onClick={copy}>Copy diagnostics</button>}
        {copied && <span role="status" className="text-sm text-paper-muted">{copied}</span>}
      </PageHeader>
      <ErrorNote error={error} onRetry={reload} />
      {loading && !data && <Loading what="Loading system status" />}
      {data && <Body data={data} />}
    </>
  );
}

function Body({ data }) {
  const { server: s, database: db, jobs, keys, integrations } = data;
  const fk = keys.fleet_key;
  return (
    <>
      <Section title="Server">
        <Facts rows={[
          ["Version", <>{s.version}{s.commit && <> <code className="text-paper-muted">({s.commit})</code></>}</>],
          ["Started", <When key="v" iso={s.started_at} />],
          ["Uptime", uptimeText(s.uptime_seconds)],
          ["Database", s.database],
          ["Data directory free", `${gb(s.data_dir.free_bytes)} of ${gb(s.data_dir.total_bytes)}`],
        ]} />
      </Section>

      <Section title="Database">
        {db.pending && <p role="alert" className="mb-3 font-bold text-warn">Warning: {db.warning}</p>}
        <Facts rows={[
          ["Migration", db.managed ? (db.current || "none") : "Not managed by migrations (schema created directly)"],
          ["Newest in this code", db.head || "unknown"],
          ["State", db.pending ? "Migrations pending" : "Up to date"],
        ]} />
      </Section>

      <Section title="Background jobs">
        <p className="mb-2 text-sm text-paper-muted">
          Scheduler {jobs.scheduler_running ? "running" : "not running"}.
          {" "}Results are kept since the server started.
        </p>
        {jobs.jobs.length === 0
          ? <p className="text-sm text-paper-muted">No jobs scheduled.</p>
          : (
            <table className="w-full text-sm">
              <caption className="sr-only">Background jobs</caption>
              <thead className="text-xs text-paper-muted">
                <tr>
                  <th scope="col" className="py-1 pr-3 text-left">Job</th>
                  <th scope="col" className="py-1 pr-3 text-left">Last run</th>
                  <th scope="col" className="py-1 pr-3 text-left">Result</th>
                  <th scope="col" className="py-1 text-left">Next run</th>
                </tr>
              </thead>
              <tbody>
                {jobs.jobs.map((j) => (
                  <tr key={j.name} className="border-t border-ink-line align-top">
                    <th scope="row" className="py-1 pr-3 text-left font-normal">{j.label}</th>
                    <td className="py-1 pr-3"><When iso={j.last_run} /></td>
                    <td className="py-1 pr-3">
                      {j.result === "error"
                        ? <span className="text-bad"><span className="font-bold">Error</span>: {j.message}</span>
                        : j.result === "ok" ? <span className="text-ok">OK</span>
                          : <span className="text-paper-muted">Not run yet</span>}
                    </td>
                    <td className="py-1"><When iso={j.next_run} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
      </Section>

      <Section title="Keys">
        <Facts rows={[
          ["Fleet key", fk.present ? "Present" : (fk.error || "Not created yet (made when the first directive is signed)")],
          fk.fingerprint && ["Public key fingerprint", <code key="v" className="break-all">{fk.fingerprint}</code>],
          fk.created_at && ["Created", <When key="v" iso={fk.created_at} />],
          ["Swarms with a leader", `${keys.swarms_with_leader} of ${keys.swarms}`],
        ]} />
        <p className="mt-2 text-sm text-paper-muted">{fk.backup_note}</p>
      </Section>

      <Section title="Integrations">
        <ul className="space-y-1 text-sm">
          <li>
            <Link className="underline" to="/lrs">{integrations.lrs.label}</Link>: {integrations.lrs.total}
            {" "}({integrations.lrs.enabled} enabled, {integrations.lrs.failing} failing). {STATE_TEXT[integrations.lrs.state]}
          </li>
          <li>
            <Link className="underline" to="/access">{integrations.mcp.label}</Link>: {integrations.mcp.total}
            {" "}({integrations.mcp.active} active, {integrations.mcp.revoked} revoked, {integrations.mcp.expired} expired). {STATE_TEXT[integrations.mcp.state]}
          </li>
          <li>
            <Link className="underline" to="/datasets">{integrations.reference_sources.label}</Link>:
            {" "}{integrations.reference_sources.imported} of {integrations.reference_sources.total} imported,
            {" "}{integrations.reference_sources.pending_versions} new versions waiting. {STATE_TEXT[integrations.reference_sources.state]}
          </li>
        </ul>
      </Section>

      <Section title="Diagnostics">
        <p className="mb-2 text-sm text-paper-muted">For a bug report. No secrets, tokens, keys, IP addresses or member data.</p>
        <pre className="max-h-64 overflow-auto rounded bg-ink p-3 text-xs" tabIndex={0} aria-label="Diagnostics text">{data.diagnostics}</pre>
      </Section>
    </>
  );
}
