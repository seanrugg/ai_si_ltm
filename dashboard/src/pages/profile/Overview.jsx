// Profile Overview tab: category tiles, activity trend, top peers (issue #192).
import { useState } from "react";
import HelpTip from "../../components/HelpTip.jsx";
import { COUNT_ACT, COUNT_COMM, COUNT_LEARN, COUNT_SELF_REPORT, COUNT_SENSE } from "../../lib/help.js";
import { CategoryLine, MemberMark } from "../../components/visual.jsx";
import { bucketIndex, dayTotal, niceMax, percent, stackDay, wipesOf } from "../../lib/profile.js";
import { CATEGORIES, NEUTRAL } from "../../lib/visual.js";
import { memberWithRole } from "../Profile.jsx";

const HELP = { sense: COUNT_SENSE, act: COUNT_ACT, comm: COUNT_COMM, learn: COUNT_LEARN };

/** Self-reports (heartbeat, health) are counted apart from sensing (#348). */
export function selfReportNote(summary) {
  const n = summary?.flow?.self_report || 0;
  if (!n) return null;
  return `${n.toLocaleString()} self-report${n === 1 ? "" : "s"} (heartbeat, health) not counted here`;
}

const NOTES = {
  sense: () => "sensed and perceived records",
  act: (s) => (s.action_success == null ? "no action outcomes yet" : `${percent(s.action_success)} of actions succeeded`),
  comm: () => "messages to and from peers",
  learn: () => "lessons and policies",
};

function Tiles({ summary }) {
  return (
    <ul className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-label="Records by category">
      {CATEGORIES.map((c) => (
        <li key={c.key} className="panel p-3">
          <div className="flex items-center gap-2 text-sm">
            <CategoryLine category={c} />
            <span>{c.label}</span>
            <HelpTip id={`tile-${c.key}`} label={`What ${c.label} counts`}>{HELP[c.key]}</HelpTip>
          </div>
          <p className="mt-1 text-2xl font-bold">{summary.categories[c.key].toLocaleString()}</p>
          <p className="text-xs text-paper-muted">{NOTES[c.key](summary)}</p>
          {c.key === "sense" && selfReportNote(summary) && (
            <p className="flex items-center gap-1 text-xs text-paper-muted">
              {selfReportNote(summary)}
              <HelpTip id="tile-self-report" label="What a self-report is">{COUNT_SELF_REPORT}</HelpTip>
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}

const W = 720;
const H = 220;
const PAD = { l: 44, r: 8, t: 22, b: 28 };

export function Trend({ daily, wipes }) {
  const [asTable, setAsTable] = useState(false);
  const max = niceMax(Math.max(0, ...daily.map(dayTotal)));
  const plotW = W - PAD.l - PAD.r;
  const plotH = H - PAD.t - PAD.b;
  const step = daily.length ? plotW / daily.length : plotW;
  const barW = Math.max(1, step * 0.72);
  const y = (v) => PAD.t + plotH - (v / max) * plotH;
  const labelEvery = Math.max(1, Math.ceil(daily.length / 8));
  const weekly = daily.length > 1 && (Date.parse(daily[1].day) - Date.parse(daily[0].day)) > 86400000 * 1.5;

  return (
    <section aria-labelledby="trend-h" className="mb-6">
      <div className="mb-2 flex items-center justify-between">
        <h2 id="trend-h" className="text-lg font-bold">Activity trend</h2>
        <button type="button" className="btn" aria-pressed={asTable} onClick={() => setAsTable((v) => !v)}>
          {asTable ? "View as chart" : "View as table"}
        </button>
      </div>
      {asTable ? (
        <table className="w-full text-sm">
          <caption className="sr-only">Records per {weekly ? "week" : "day"} by category</caption>
          <thead><tr><th className="text-left">{weekly ? "Week of" : "Day"}</th>
            {CATEGORIES.map((c) => <th key={c.key} className="text-right">{c.label}</th>)}<th className="text-right">Total</th></tr></thead>
          <tbody>
            {daily.map((d) => (
              <tr key={d.day}><td>{d.day}</td>
                {CATEGORIES.map((c) => <td key={c.key} className="text-right">{d[c.key]}</td>)}
                <td className="text-right">{dayTotal(d)}</td></tr>
            ))}
          </tbody>
        </table>
      ) : (
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img"
             aria-label={`Records per ${weekly ? "week" : "day"}, stacked by category. Use View as table for the numbers.`}>
          <text x={12} y={PAD.t + plotH / 2} fill="currentColor" fontSize="11" className="text-paper-muted"
                transform={`rotate(-90 12 ${PAD.t + plotH / 2})`} textAnchor="middle">records</text>
          {[0, max / 2, max].map((v) => (
            <g key={v}>
              <line x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} stroke="#26344F" strokeWidth="1" />
              <text x={PAD.l - 6} y={y(v) + 4} fontSize="11" textAnchor="end" fill="#93A0B8">{Math.round(v)}</text>
            </g>
          ))}
          {daily.map((d, i) => (
            <g key={d.day}>
              {stackDay(d).filter((s) => s.value > 0).map((s) => {
                const c = CATEGORIES.find((x) => x.key === s.key);
                return (
                  <rect key={s.key} x={PAD.l + i * step + (step - barW) / 2} width={barW}
                        y={y(s.y1)} height={Math.max(0, y(s.y0) - y(s.y1))} fill={c.color} opacity="0.85">
                    <title>{`${d.day}: ${s.value} ${c.label}`}</title>
                  </rect>
                );
              })}
              {i % labelEvery === 0 && (
                <text x={PAD.l + i * step + step / 2} y={H - 8} fontSize="10" textAnchor="middle" fill="#93A0B8">
                  {d.day.slice(5)}
                </text>
              )}
            </g>
          ))}
          {wipes.map((w) => {
            const i = bucketIndex(daily, w.ts);
            if (i < 0) return null;
            const x = PAD.l + i * step + step / 2;
            return (
              <g key={w.ts}>
                <line x1={x} x2={x} y1={PAD.t} y2={PAD.t + plotH} stroke={NEUTRAL} strokeDasharray="4 4" />
                <path d={`M${x - 5} ${PAD.t - 10}h10l-5 7z`} fill={NEUTRAL} />
                <text x={x + 7} y={PAD.t - 4} fontSize="10" fill={NEUTRAL}>
                  {`memory wipe${w.generation ? ` · gen ${w.generation}` : ""}`}
                </text>
              </g>
            );
          })}
        </svg>
      )}
    </section>
  );
}

function SplitBar({ counts, max }) {
  const total = CATEGORIES.reduce((s, c) => s + (counts[c.key] || 0), 0);
  return (
    <span className="flex h-1.5 w-40 overflow-hidden rounded bg-ink-line" aria-hidden="true"
          style={{ width: `${Math.max(8, (total / (max || 1)) * 160)}px` }}>
      {CATEGORIES.map((c) => (counts[c.key] ? (
        <span key={c.key} style={{ width: `${(counts[c.key] / total) * 100}%`, background: c.color }} />
      ) : null))}
    </span>
  );
}

function People({ subject, summary, members, swarms, onSelectPeer }) {
  const rows = subject === "member" ? summary.peers : summary.members || [];
  const title = subject === "member" ? "Top peers" : "Most active members";
  const totals = rows.map((r) => CATEGORIES.reduce((s, c) => s + (r[c.key] || 0), 0));
  const max = Math.max(1, ...totals);
  return (
    <section aria-labelledby="people-h">
      <h2 id="people-h" className="mb-2 text-lg font-bold">{title}</h2>
      {rows.length === 0 ? (
        <p className="text-sm text-paper-muted">
          {subject === "member" ? "No exchanges with other members in this range." : "No records in this range."}
        </p>
      ) : (
        <ul className="panel">
          {rows.map((r, i) => {
            const m = memberWithRole((members || []).find((x) => x.name === r.name) || { name: r.name }, swarms);
            const parts = CATEGORIES.filter((c) => r[c.key]).map((c) => `${r[c.key]} ${c.label.toLowerCase()}`);
            return (
              <li key={r.name} className="border-b border-ink-line last:border-b-0">
                <button type="button" onClick={() => onSelectPeer(r.name)}
                        className="flex w-full flex-wrap items-center gap-3 px-3 py-2 text-left hover:bg-ink-hover"
                        aria-label={`${r.name}: ${totals[i]} records (${parts.join(", ")}). Open in Interactions`}>
                  <MemberMark member={m} size={26} />
                  <span className="w-40 font-bold">{r.name}</span>
                  <span className="w-16 text-right text-sm">{totals[i]}</span>
                  <SplitBar counts={r} max={max} />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

export default function Overview({ subject, summary, history, members, swarms, onSelectPeer }) {
  return (
    <>
      <Tiles summary={summary} />
      <Trend daily={summary.daily} wipes={wipesOf(history?.events)} />
      <People subject={subject} summary={summary} members={members} swarms={swarms} onSelectPeer={onSelectPeer} />
    </>
  );
}
