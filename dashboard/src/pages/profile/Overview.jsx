// Profile Overview tab: category tiles, self-report heartbeat (#406), activity trend, top peers (issue #192).
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import HelpTip from "../../components/HelpTip.jsx";
import { COUNT_ACT, COUNT_COMM, COUNT_LEARN, COUNT_SELF_REPORT, COUNT_SENSE } from "../../lib/help.js";
import { CategoryLine, MemberMark } from "../../components/visual.jsx";
import { bucketIndex, dayTotal, niceMax, percent, stackDay, wipesOf } from "../../lib/profile.js";
import { CATEGORIES, NEUTRAL, kindOf } from "../../lib/visual.js";
import { memberWithRole } from "../Profile.jsx";

const HELP = { sense: COUNT_SENSE, act: COUNT_ACT, comm: COUNT_COMM, learn: COUNT_LEARN };

const NOTES = {
  sense: () => "sensed and perceived records",
  act: (s) => (s.action_success == null ? "no action outcomes yet" : `${percent(s.action_success)} of actions succeeded`),
  comm: () => "messages to and from peers",
  learn: () => "lessons and policies",
};

// Self-reports (#406): the member's pulse gets its own tile, apart from Sensing (#348).
export const SELF = { key: "self", label: "Self-reports", color: "#6aa8ff" }; // cat-self

/**
 * Sensing no longer carries a self-report footnote (#406): the Self-reports tile says it.
 * Kept as a function so callers and tests have one place to ask.
 */
export function selfReportNote() {
  return null;
}

/** Heart rate for the median self-report interval: 30 s or slower → 48 bpm, 10 s or faster → 60 (#406). */
export function bpmOf(intervalS) {
  if (intervalS == null || !(intervalS > 0)) return null;
  return Math.round(Math.min(60, Math.max(48, 66 - 0.6 * intervalS)));
}

/** "12 s ago", "2 min ago", "3 h ago", "2 d ago". */
export function agoText(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

/**
 * The live heartbeat from the summary's ``self_report_last`` and ``self_report_interval_s``.
 * state: "beating"; "slowing" when the last report is over 2 intervals old; "quiet" after
 * 4 missed intervals; "reported" with one report (no interval to judge by); null with none.
 */
export function pulseOf(last, intervalS, now = Date.now()) {
  if (!last) return null;
  const age = Math.max(0, (now - Date.parse(last)) / 1000);
  const bpm = bpmOf(intervalS);
  let state = "reported";
  if (bpm != null) state = age > 4 * intervalS ? "quiet" : age > 2 * intervalS ? "slowing" : "beating";
  return { state, bpm, age };
}

/** The tile's live line: "beating · 48 bpm · last 12 s ago", "slowing · last 2 min ago", "quiet · last 9 min ago". */
export function pulseText(p) {
  if (!p) return "no self-reports yet";
  const last = `last ${agoText(p.age)}`;
  if (p.state === "beating") return `beating · ${p.bpm} bpm · ${last}`;
  if (p.state === "reported") return last;
  return `${p.state} · ${last}`;
}

/** The header's Heartbeat fact: "● 48 bpm", "slowing" or "quiet"; null when there is nothing to say. */
export function heartbeatFact(p) {
  if (!p || p.state === "reported") return null;
  return p.state === "beating" ? `● ${p.bpm} bpm` : p.state;
}

/** Swarm: how many of its current members are beating now. */
export function membersBeating(rows, now = Date.now()) {
  const list = rows || [];
  const n = list.filter((m) => pulseOf(m.last, m.interval_s, now)?.state === "beating").length;
  return { n, m: list.length, text: `${n} of ${list.length} member${list.length === 1 ? "" : "s"} beating` };
}

/** Current time, ticking once a second, so "last 12 s ago" stays live. */
export function useNow(ms = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

export function prefersReducedMotion() {
  return typeof window !== "undefined" && !!window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

const BEAT_STYLE = `
@keyframes ltms-lubdub { 0% { transform: scale(1); } 10% { transform: scale(1.08); } 20% { transform: scale(1); }
  30% { transform: scale(1.05); } 42%, 100% { transform: scale(1); } }
.ltms-beat { animation: ltms-lubdub var(--beat, 1.25s) ease-out infinite; transform-origin: center; }
@media (prefers-reduced-motion: reduce) { .ltms-beat { animation: none; } }`;

/** A cat-self dot: lub-dub at the heart rate while beating, still with reduced motion, dimmed when quiet. */
export function BeatDot({ pulse, size = 10 }) {
  const beating = pulse?.state === "beating" && !prefersReducedMotion();
  return (
    <span aria-hidden="true" data-beat={beating ? "beating" : "still"}
          className={`inline-block shrink-0 rounded-full ${beating ? "ltms-beat" : ""}`}
          style={{ width: size, height: size, background: SELF.color,
                   opacity: pulse?.state === "quiet" ? 0.35 : 1,
                   ...(beating ? { "--beat": `${(60 / pulse.bpm).toFixed(3)}s` } : {}) }} />
  );
}

/** The header's Heartbeat fact value, beside Status (#406). */
export function HeartbeatValue({ summary }) {
  const now = useNow();
  const pulse = pulseOf(summary?.self_report_last, summary?.self_report_interval_s, now);
  const text = heartbeatFact(pulse);
  if (!text) return null;
  return <span className={pulse.state === "beating" ? "" : "text-paper-muted"}>{text}</span>;
}

function SelfReportTile({ subject, summary, member }) {
  const now = useNow();
  const [params] = useSearchParams();
  const swarm = subject === "swarm";
  const pulse = pulseOf(summary.self_report_last, summary.self_report_interval_s, now);
  const beating = swarm && summary.self_report_members ? membersBeating(summary.self_report_members, now) : null;
  // Swarm: the dot beats while any member beats, at the swarm's own rate.
  const dotPulse = beating
    ? { state: beating.n ? "beating" : pulse?.state === "quiet" ? "quiet" : "still", bpm: pulse?.bpm ?? 48 }
    : pulse;
  const health = new URLSearchParams(params);
  health.set("tab", "senses");
  return (
    <li className="panel p-3">
      <div className="flex items-center gap-2 text-sm">
        <span aria-hidden="true" className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: SELF.color }} />
        <span>{SELF.label}</span>
        <HelpTip id="tile-self" label="What Self-reports counts">{COUNT_SELF_REPORT}</HelpTip>
      </div>
      <p className="mt-1 font-mono text-[32px] leading-tight">{(summary.flow?.self_report ?? 0).toLocaleString()}</p>
      <p className="text-xs text-paper-muted">heartbeat and health reports</p>
      <p className="flex items-center gap-2 text-xs text-paper-muted" data-testid="pulse">
        <BeatDot pulse={dotPulse} />
        <span>{beating ? beating.text : pulseText(pulse)}</span>
      </p>
      {!swarm && member && kindOf(member) === "hub" && (
        <Link to={`?${health}`} className="text-xs text-led hover:underline">Machine health</Link>
      )}
    </li>
  );
}

function Tiles({ subject, summary, member }) {
  return (
    <ul className="mb-6 grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))" }}
        aria-label="Records by category">
      <style>{BEAT_STYLE}</style>
      {CATEGORIES.map((c) => (
        <li key={c.key} className="panel p-3">
          <div className="flex items-center gap-2 text-sm">
            <CategoryLine category={c} />
            <span>{c.label}</span>
            <HelpTip id={`tile-${c.key}`} label={`What ${c.label} counts`}>{HELP[c.key]}</HelpTip>
          </div>
          <p className="mt-1 text-2xl font-bold">{summary.categories[c.key].toLocaleString()}</p>
          <p className="text-xs text-paper-muted">{NOTES[c.key](summary)}</p>
        </li>
      ))}
      <SelfReportTile subject={subject} summary={summary} member={member} />
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

export default function Overview({ subject, name, summary, history, members, swarms, onSelectPeer }) {
  const member = subject === "member"
    ? memberWithRole((members || []).find((m) => m.name === name) || null, swarms) : null;
  return (
    <>
      <Tiles subject={subject} summary={summary} member={member} />
      <Trend daily={summary.daily} wipes={wipesOf(history?.events)} />
      <People subject={subject} summary={summary} members={members} swarms={swarms} onSelectPeer={onSelectPeer} />
    </>
  );
}
