// Profile Senses tab: one row per sensor (issue #197).
import { useApi } from "../../api/useApi.js";
import { ErrorNote, Loading } from "../../components/ui.jsx";
import { MemberMark } from "../../components/visual.jsx";
import { relativeTime } from "../../lib/format.js";
import { sparkPoints } from "../../lib/profile.js";
import { category } from "../../lib/visual.js";
import { memberWithRole } from "../Profile.jsx";

function Spark({ series, label }) {
  const sense = category("sense");
  if (!series?.length) return <span className="text-xs text-paper-faint">no numbers</span>;
  return (
    <svg width="120" height="24" viewBox="0 0 120 24" role="img" aria-label={label}>
      <polyline points={sparkPoints(series, 120, 24)} fill="none" stroke={sense.color} strokeWidth="1.5" />
    </svg>
  );
}

function reading(last) {
  if (!last) return "—";
  const v = typeof last.value === "object" ? JSON.stringify(last.value) : String(last.value);
  return `${v.length > 40 ? `${v.slice(0, 40)}…` : v} · ${relativeTime(last.ts)}`;
}

export default function Senses({ subject, name, range, members, swarms }) {
  const senses = useApi("/admin/profile/senses", { [subject]: name, hours: range.hours });
  if (senses.loading && !senses.data) return <Loading what="Loading sensors" />;
  if (senses.error) return <ErrorNote error={senses.error} onRetry={senses.reload} />;
  const rows = senses.data?.sensors || [];
  if (rows.length === 0) {
    return (
      <p className="py-6 text-sm text-paper-muted">
        No sensor data in this range. Members send sensor readings as sensed records (a perception tagged{" "}
        <code>telemetry</code>); see <code>docs/sensing.md</code>.
      </p>
    );
  }
  return (
    <table className="w-full text-sm">
      <caption className="mb-2 text-left text-lg font-bold">Sensors</caption>
      <thead>
        <tr className="text-left text-paper-muted">
          <th>Sensor</th>{subject === "swarm" && <th>On</th>}<th>Modality</th><th>Rate</th>
          <th>Last reading</th><th>Last 24 h</th><th className="text-right">Feeds perceptions</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((s) => {
          const label = s.sensor ?? "(payload purged)";
          const m = memberWithRole((members || []).find((x) => x.name === s.member) || { name: s.member }, swarms);
          return (
            <tr key={`${s.member}:${label}`} className="border-t border-ink-line">
              <td className="py-1.5">{label}</td>
              {subject === "swarm" && (
                <td><span className="flex items-center gap-1.5"><MemberMark member={m} size={18} />{s.member}</span></td>
              )}
              <td>{s.modality ?? "—"}</td>
              <td>{s.rate_hz != null ? `${s.rate_hz} Hz` : "—"}</td>
              <td>{reading(s.last)}{s.unit ? ` ${s.unit}` : ""}</td>
              <td><Spark series={s.series} label={`${label}: ${s.series?.length || 0} points over the last 24 hours`} /></td>
              <td className="text-right">{s.feeds}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
