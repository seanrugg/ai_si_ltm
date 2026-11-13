// A hub's machine health (#349): self-reports, not sensing.
import HelpTip from "../../components/HelpTip.jsx";
import { useApi } from "../../api/useApi.js";
import { relativeTime } from "../../lib/format.js";
import { HEALTH_ABOUT } from "../../lib/help.js";
import { healthReports, hottestSeries, latestRows } from "../../lib/health.js";
import { sparkPoints } from "../../lib/profile.js";

export function HealthView({ reports }) {
  if (!reports.length) return null;
  const latest = reports[reports.length - 1];
  const hot = hottestSeries(reports);
  return (
    <section className="panel mb-6 p-4" aria-labelledby="health-h">
      <h2 id="health-h" className="flex items-center gap-2 text-lg font-bold">
        Machine health <span className="text-sm font-normal text-paper-muted">self-report · {relativeTime(latest.ts)}</span>
        <HelpTip id="health-about" label="About machine health">{HEALTH_ABOUT}</HelpTip>
      </h2>
      {hot.length > 1 && (
        <svg width="240" height="32" viewBox="0 0 240 32" role="img" className="my-2"
             aria-label={`Hottest temperature per report, ${hot.length} reports, last ${hot[hot.length - 1]} °C`}>
          <polyline points={sparkPoints(hot, 240, 32)} fill="none" stroke="currentColor" strokeWidth="1.5" />
        </svg>
      )}
      <table className="text-sm">
        <tbody>
          {latestRows(latest).map(([k, v]) => (
            <tr key={k}><th scope="row" className="pr-4 text-left font-normal text-paper-muted">{k}</th><td>{v}</td></tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export default function MachineHealth({ name }) {
  const tl = useApi("/admin/timeline", { agent: name, type: "self_report", limit: 100 });
  return <HealthView reports={healthReports(tl.data?.records)} />;
}
