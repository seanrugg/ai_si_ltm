// Profile Chronology tab (#403; was Activity, #197): the chain from sensing to
// learning as six Holo stat tiles, then the chronograph in a VizFrame. A swarm
// profile shows one small chronograph per member. The old outcome bars live in
// the table twin's "Activity trend" section.
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { Link, useNavigate } from "react-router-dom";
import HelpTip from "../../components/HelpTip.jsx";
import VizFrame, { HoloTable } from "../../components/VizFrame.jsx";
import { CategoryLine } from "../../components/visual.jsx";
import { api } from "../../api/client.js";
import { useApi } from "../../api/useApi.js";
import { COUNT_SELF_REPORT } from "../../lib/help.js";
import { localTime } from "../../lib/format.js";
import { createReplayClock, useReplay } from "../../lib/replay.js";
import { sound } from "../../lib/sound.js";
import { category } from "../../lib/visual.js";
import {
  Chronograph, LANES, drawnEpisodes, episodeRows, laneTouchText, leapSeconds, leapsOf, narrationAt,
  stateAt, stateRows, zoneName,
} from "./Chronograph.jsx";

export const STEPS = [
  { key: "sensed", label: "Sensed", cat: "sense", link: "raw sensor data",
    note: "Readings of the world from a sensor. Heartbeats and health reports are self-reports, counted separately below. Most sensed records are never referenced: they are context. Senses shows which perceptions each sensor fed." },
  { key: "perceived", label: "Perceived", cat: "sense", link: "derived_from sensed",
    note: "Perceptions with no sensed reference may be guesses." },
  { key: "reasoned", label: "Reasoned", cat: "act", link: "because perceived",
    note: "Thoughts that led to no decision show how often it deliberates without acting." },
  { key: "decided", label: "Decided", cat: "act", link: "because perceived",
    note: "Decisions without a later action are commitments that were never carried out." },
  { key: "performed", label: "Performed", cat: "act", link: "because decided",
    note: "Each action links back to the decision that caused it, so a failure can be traced to the perception behind it." },
  { key: "learned", label: "Learned", cat: "learn", link: "because outcomes",
    note: "Lessons and the outcomes they came from." },
];

// Opacity runs from 1 (success) down to 0.35 (failed), so the bars differ without a second color.
export const OUTCOME_OPACITY = { success: 1, partial: 0.87, reflex_stop: 0.74, stuck: 0.61, abandoned: 0.48, failed: 0.35 };

export const NOTHING_READ = "Nothing read yet. Members read fleet datasets during dream cycles; see docs/datasets.md.";
export const NOTHING_ARRIVED = "Nothing has arrived in this range.";
export const GLOW_MS = 2000;
const HOUR_MS = 3600_000;
const MAX_SPAN_MS = 366 * 24 * HOUR_MS; // the episodes endpoint's limit
const LIVE_REFRESH_MS = 60_000;

/**
 * The episodes window for a profile range: the last `hours`, or for All, from
 * the first day the summary has (at most 366 days, the endpoint's limit).
 * {since, until} as ISO strings.
 */
export function rangeWindow(range, summary, nowMs = Date.now()) {
  let since = nowMs - (range?.hours > 0 ? range.hours : 24) * HOUR_MS;
  if (!(range?.hours > 0)) {
    const first = Date.parse(`${summary?.daily?.[0]?.day ?? ""}T00:00:00`);
    since = Number.isFinite(first) ? first : nowMs - MAX_SPAN_MS;
  }
  since = Math.max(since, nowMs - MAX_SPAN_MS);
  if (since >= nowMs) since = nowMs - HOUR_MS;
  return { since: new Date(since).toISOString(), until: new Date(nowMs).toISOString() };
}

// ---------------------------------------------------------------- the chain row

/** True for GLOW_MS after `value` changes (not on first show). */
function useGlow(value) {
  const prev = useRef(value);
  const [on, setOn] = useState(false);
  useEffect(() => {
    if (prev.current === value) return undefined;
    prev.current = value;
    setOn(true);
    const id = setTimeout(() => setOn(false), GLOW_MS);
    return () => clearTimeout(id);
  }, [value]);
  return on;
}

function StatTile({ step, value, pressed, onPress }) {
  const glow = useGlow(value);
  return (
    <button type="button" aria-pressed={pressed} onClick={onPress}
            className={`panel flex flex-col items-start px-3 py-2 text-left ${pressed ? "ring-1 ring-led" : ""}`}>
      <span className="flex items-center gap-2 text-[11px] uppercase tracking-[1.6px] text-paper-muted">
        <CategoryLine category={step.cat} width={20} />{step.label}
      </span>
      <span className="font-mono text-xl" data-glow={glow ? "live" : undefined}
            style={glow ? { textShadow: "0 0 8px currentColor, 0 0 16px currentColor" } : undefined}>
        {value.toLocaleString()}
      </span>
      <span className="text-xs text-paper-muted">{step.link}</span>
    </button>
  );
}

export function ChainRow({ flow }) {
  const [step, setStep] = useState("sensed");
  const current = STEPS.find((s) => s.key === step);
  return (
    <section aria-labelledby="chain-h" className="mb-6">
      <h2 id="chain-h" className="mb-2 text-lg font-bold">From sensing to learning</h2>
      <div role="group" aria-label="Steps" className="flex flex-wrap gap-2">
        {STEPS.map((s, i) => (
          <span key={s.key} className="flex items-center gap-2">
            {i > 0 && <span aria-hidden="true" className="text-paper-faint">→</span>}
            <StatTile step={s} value={flow?.[s.key] ?? 0} pressed={step === s.key} onPress={() => setStep(s.key)} />
          </span>
        ))}
      </div>
      <p className="mt-3 text-sm" aria-live="polite">{current.note}</p>
      <p className="mt-2 flex items-center gap-1 text-xs text-paper-muted">
        Self-reports (heartbeat, health), not part of this chain: {(flow?.self_report ?? 0).toLocaleString()}
        <HelpTip id="chain-self-report" label="What a self-report is">{COUNT_SELF_REPORT}</HelpTip>
      </p>
    </section>
  );
}

// ---------------------------------------------------------------- Activity trend (the old bar chart)

export function ActivityTrend({ summary }) {
  const act = category("act");
  const outcomes = Object.entries(summary?.outcomes || {});
  const maxOutcome = Math.max(1, ...outcomes.map(([, n]) => n));
  const days = summary?.daily || [];
  return (
    <section aria-label="Activity trend" className="mt-4">
      <HoloTable caption="Activity trend: action outcomes"
                 columns={[
                   { key: "outcome", label: "Outcome" },
                   { key: "bar", label: "Share", render: (r) => (
                     <span className="inline-block h-3 rounded" aria-hidden="true"
                           style={{ width: `${Math.max(2, (r.n / maxOutcome) * 200)}px`, background: act.color,
                                    opacity: OUTCOME_OPACITY[r.id] ?? 0.5 }} />
                   ) },
                   { key: "n", label: "Actions", numeric: true },
                 ]}
                 rows={outcomes.map(([o, n]) => ({ id: o, outcome: o.replace("_", " "), n }))} />
      {days.length > 0 && (
        <div className="mt-3">
          <HoloTable caption="Activity trend: records by day"
                     columns={[
                       { key: "day", label: "Day" },
                       { key: "sense", label: "Sensing", numeric: true },
                       { key: "act", label: "Deciding & acting", numeric: true },
                       { key: "comm", label: "Communicating", numeric: true },
                       { key: "learn", label: "Learning & rules", numeric: true },
                     ]}
                     rows={days.map((d) => ({ id: d.day, ...d }))} />
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- What it read, lessons

// "What it read" (#243): datasets read, then dream cycles with the lessons each wrote.
export function WhatItRead({ reading, swarm = false }) {
  const datasets = reading?.datasets || [];
  const cycles = reading?.cycles || [];
  return (
    <section aria-labelledby="read-h" className="mb-6">
      <h2 id="read-h" className="mb-2 text-lg font-bold">What it read</h2>
      {datasets.length === 0 && cycles.length === 0 ? (
        <p className="text-sm text-paper-muted">{NOTHING_READ}</p>
      ) : (
        <>
          {datasets.length > 0 && (
            <table className="mb-4 w-full text-sm" aria-label="Datasets read">
              <thead>
                <tr className="text-left text-paper-muted">
                  <th className="font-normal">Dataset</th><th className="font-normal">Reads</th>
                  <th className="font-normal">Items</th><th className="font-normal">Last read</th>
                </tr>
              </thead>
              <tbody>
                {datasets.map((d) => (
                  <tr key={d.dataset}>
                    <td>{d.name}</td><td>{d.reads.toLocaleString()}</td><td>{d.items.toLocaleString()}</td>
                    <td>{d.last_read ? localTime(d.last_read) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {cycles.length > 0 && (
            <>
              <h3 className="mb-1 text-sm font-bold">Dream cycles</h3>
              <ul className="panel" aria-label="Dream cycles">
                {cycles.map((c) => (
                  <li key={c.cycle} className="border-b border-ink-line px-3 py-2 last:border-b-0">
                    <p className="text-sm">
                      {swarm && <span className="mr-2 font-bold">{c.agent}</span>}
                      {c.started ? localTime(c.started) : "start not recorded"}
                      <span className="ml-2 text-paper-muted">
                        {c.ended ? `${c.items_read ?? 0} items read · ${c.lessons.length} lesson${c.lessons.length === 1 ? "" : "s"}`
                          : "not finished"}
                      </span>
                    </p>
                    {c.lessons.length > 0 && (
                      <ul className="ml-4 mt-1 list-disc text-sm">
                        {c.lessons.map((l) => (
                          <li key={`${l.agent}:${l.seq}`}>
                            <Link to={`/record/${encodeURIComponent(l.agent)}/${l.seq}`} className="underline hover:text-paper">
                              {l.summary || `${l.agent}:${l.seq}`}
                            </Link>
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </section>
  );
}

function Reading({ subject, name, range }) {
  const { data, error } = useApi("/admin/profile/reading", { [subject]: name, hours: range?.hours });
  // An older server without the endpoint just shows nothing.
  if (error) return null;
  return <WhatItRead reading={data} swarm={subject === "swarm"} />;
}

function Lessons({ lessons }) {
  return (
    <section aria-labelledby="lessons-h">
      <h2 id="lessons-h" className="mb-2 text-lg font-bold">Recent lessons</h2>
      {(lessons || []).length === 0 ? (
        <p className="text-sm text-paper-muted">No lessons in this range.</p>
      ) : (
        <ul className="panel">
          {lessons.map((l) => (
            <li key={`${l.agent}:${l.seq}`} className="border-b border-ink-line px-3 py-2 last:border-b-0">
              <Link to={`/record/${encodeURIComponent(l.agent)}/${l.seq}`} className="underline hover:text-paper">
                {l.summary || `${l.agent}:${l.seq}`}
              </Link>
              <span className="ml-2 text-xs text-paper-muted">{`${l.agent} · ${localTime(l.ts)}`}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- the clock and the window

/** The clock's mode only, so the page re-renders when Live/Replay changes, not every tick. */
function useClockMode(clock) {
  return useSyncExternalStore(clock.subscribe, () => clock.getState().mode, () => clock.getState().mode);
}

/** The episodes window for the range, moved forward every minute while Live. */
function useWindow(range, summary, clock) {
  const mode = useClockMode(clock);
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    setNowMs(Date.now());
    if (mode !== "live") return undefined;
    const id = setInterval(() => setNowMs(Date.now()), LIVE_REFRESH_MS);
    return () => clearInterval(id);
  }, [mode, range?.key]);
  const firstDay = summary?.daily?.[0]?.day;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const win = useMemo(() => rangeWindow(range, summary, nowMs), [range?.key, range?.hours, firstDay, nowMs]);
  useEffect(() => {
    clock.setRange(Date.parse(win.since), Date.parse(win.until));
  }, [clock, win.since, win.until]);
  return win;
}

/** The hand in data seconds (from `since`), kept inside the data's range. */
export function handSeconds(t, data) {
  const since = Date.parse(data?.since ?? "");
  if (!Number.isFinite(since)) return 0;
  return Math.min(data.duration_s ?? 0, Math.max(0, (t - since) / 1000));
}

// ---------------------------------------------------------------- member

export function MemberTwin({ data, hand, summary }) {
  const sinceMs = Date.parse(data.since);
  const zone = zoneName(sinceMs + hand * 1000);
  const at = new Date(sinceMs + hand * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit" });
  return (
    <div className="space-y-3">
      <HoloTable caption={`State at the hand · ${at} (${zone})`}
                 columns={[
                   { key: "lane", label: "Lane", render: (r) => <b>{r.lane}</b> },
                   { key: "state", label: "State" },
                   { key: "count", label: "Episodes this range", numeric: true },
                 ]}
                 rows={stateRows(data, hand)} isLive={(r) => r.live} />
      <HoloTable caption="Episodes up to the hand"
                 columns={[
                   { key: "start", label: `Start (${zone})`, numeric: true },
                   { key: "lane", label: "Lane" },
                   { key: "state", label: "State" },
                   { key: "length", label: "Length", numeric: true },
                   { key: "detail", label: "Detail" },
                 ]}
                 rows={episodeRows(data, hand, sinceMs)} isLive={(r) => r.live} />
      <ActivityTrend summary={summary} />
    </div>
  );
}

/** A ring's touch-note key for the shared engine: the same ring always plays the same pitch. */
export function ringNote(laneKey) {
  return `ring:${laneKey}`;
}

function MemberChronology({ name, summary, range, clock }) {
  const win = useWindow(range, summary, clock);
  const { data, error, loading, reload } = useApi("/admin/profile/episodes", { member: name, since: win.since, until: win.until });
  const state = useReplay(clock);
  const leapList = useMemo(() => (data ? leapsOf(data) : []), [data]);
  const sinceMs = Date.parse(data?.since ?? "");
  const hand = handSeconds(state.t, data);
  const narration = data ? narrationAt(data, hand, name, sinceMs) : null;
  const empty = data && !data.count;

  const touchables = data ? LANES.map((l) => ({
    // VizFrame plays the shared touch note, sound.note(note) (#428): each ring its own pitch.
    id: l.key, label: `${l.name} ring`, narration: laneTouchText(l.key, data, hand), note: ringNote(l.key),
  })) : [];

  return (
    <VizFrame title="Chronology" clock={clock}
              narration={empty ? { text: NOTHING_ARRIVED, key: win.since } : narration}
              touchables={touchables}
              legend={{ categories: ["sense"], selfReport: true, extra: <LaneLegend /> }}
              renderTable={(frame) => (data ? <MemberTwin data={data} hand={handSeconds(frame.t, data)} summary={summary} />
                : <ActivityTrend summary={summary} />)}>
      {(frame) => (
        <div className="p-3">
          {error && <p className="text-sm text-paper-muted">Could not load episodes. <button type="button" className="underline" onClick={reload}>Retry</button></p>}
          {loading && !data && <p className="py-8 text-center text-sm text-paper-muted">Loading the chronograph…</p>}
          {data && (
            <Chronograph data={data} leapList={leapList} hand={handSeconds(frame.t, data)} sinceMs={sinceMs}
                         animate={frame.animate} leapS={leapSeconds(frame.mode, frame.speed)}
                         soundEngine={sound} member={name}
                         onLane={(k) => {
                           frame.narrate(laneTouchText(k, data, handSeconds(frame.t, data)));
                           sound.note(ringNote(k));
                         }} />
          )}
        </div>
      )}
    </VizFrame>
  );
}

function LaneLegend() {
  return (
    <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
      {LANES.filter((l) => l.key !== "behavior").map((l) => (
        <span key={l.key} className="inline-flex items-center gap-1">
          {l.name}:
          {Object.entries(l.colors).map(([s, c]) => (
            <span key={s} className="inline-flex items-center gap-1">
              <span aria-hidden="true" className="inline-block h-2 w-2 rounded-full" style={{ background: c }} />{s}
            </span>
          ))}
        </span>
      ))}
      <span>Past lit, future dim; the live episode is bright up to the hand.</span>
    </span>
  );
}

// ---------------------------------------------------------------- swarm (small multiples)

/** Episodes for each member, {name: data | {error}}. */
function useMembersEpisodes(names, win) {
  const [byName, setByName] = useState({});
  const key = names.join("|");
  useEffect(() => {
    let live = true;
    for (const n of names) {
      api("/admin/profile/episodes", { params: { member: n, since: win.since, until: win.until } })
        .then((d) => { if (live) setByName((prev) => ({ ...prev, [n]: d })); })
        .catch((error) => { if (live) setByName((prev) => ({ ...prev, [n]: { error } })); });
    }
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, win.since, win.until]);
  return byName;
}

export function memberChronologyPath(name, range) {
  const q = new URLSearchParams({ tab: "chronology", ...(range?.key ? { range: range.key } : {}) });
  return `/members/${encodeURIComponent(name)}/profile?${q}`;
}

/** The swarm's table twin: one row per member, with its state at the hand. */
export function swarmRows(names, byName, t) {
  return names.map((n) => {
    const d = byName[n];
    if (!d || d.error) return { id: n, name: n, upTo: null, total: null, now: d?.error ? "could not load" : "loading" };
    const hand = handSeconds(t, d);
    const total = LANES.reduce((s, l) => s + drawnEpisodes(d, l.key).length, 0);
    const upTo = LANES.reduce((s, l) => s + drawnEpisodes(d, l.key).filter((e) => e[0] <= hand).length, 0);
    const now = LANES.map((l) => [l.name, stateAt(drawnEpisodes(d, l.key), hand)]).filter(([, e]) => e)
      .map(([ln, e]) => `${ln}: ${e[2]}`).join(", ");
    return { id: n, name: n, upTo, total, now: now || "idle", live: Boolean(now) };
  });
}

function SmallMultiple({ name, data, frame, range }) {
  const navigate = useNavigate();
  const leapList = useMemo(() => (data && !data.error ? leapsOf(data) : []), [data]);
  const open = () => navigate(memberChronologyPath(name, range));
  return (
    <figure className="flex flex-col items-center" data-member={name}>
      {data && !data.error ? (
        <div role="presentation" onClick={open} className="cursor-pointer">
          <Chronograph data={data} leapList={leapList} small size={180} hand={handSeconds(frame.t, data)}
                       sinceMs={Date.parse(data.since)} animate={frame.animate} leapS={leapSeconds(frame.mode, frame.speed)} />
        </div>
      ) : (
        <div className="flex h-[180px] w-[180px] items-center justify-center text-xs text-paper-muted">
          {data?.error ? "Could not load" : "Loading…"}
        </div>
      )}
      <figcaption className="mt-1 text-sm text-paper">{name}</figcaption>
    </figure>
  );
}

function SwarmChronology({ name, summary, range, clock, swarms }) {
  const win = useWindow(range, summary, clock);
  const navigate = useNavigate();
  const swarm = (swarms || []).find((s) => s.name === name);
  const names = useMemo(() => (swarm?.members || []).map((m) => m.name).sort((a, b) => a.localeCompare(b)),
    [swarm]);
  const byName = useMembersEpisodes(names, win);
  const touchables = names.map((n) => ({
    id: n, label: n, narration: `Opening ${n}'s chronology`, onTouch: () => navigate(memberChronologyPath(n, range)),
  }));
  return (
    <VizFrame title="Chronology" clock={clock} touchables={touchables}
              narration={names.length ? null : "This swarm has no members yet."}
              legend={{ categories: ["sense"], selfReport: true, extra: <LaneLegend /> }}
              renderTable={(frame) => (
                <div className="space-y-3">
                  <HoloTable caption="Members"
                             columns={[
                               { key: "name", label: "Member", render: (r) => <Link to={memberChronologyPath(r.name, range)} className="underline">{r.name}</Link> },
                               { key: "now", label: "At the hand" },
                               { key: "upTo", label: "Episodes up to the hand", numeric: true, render: (r) => r.upTo ?? "—" },
                               { key: "total", label: "Episodes this range", numeric: true, render: (r) => r.total ?? "—" },
                             ]}
                             rows={swarmRows(names, byName, frame.t)} isLive={(r) => r.live} />
                  <ActivityTrend summary={summary} />
                </div>
              )}>
      {(frame) => (
        <div className="flex flex-wrap justify-center gap-4 p-3">
          {names.map((n) => <SmallMultiple key={n} name={n} data={byName[n]} frame={frame} range={range} />)}
        </div>
      )}
    </VizFrame>
  );
}

// ---------------------------------------------------------------- the tab

export default function Activity({ summary, subject, name, range, swarms }) {
  const [clock] = useState(() => createReplayClock());
  const swarm = subject === "swarm";
  return (
    <>
      <ChainRow flow={summary.flow} />
      <section aria-label="Chronology" className="mb-6">
        {swarm
          ? <SwarmChronology name={name} summary={summary} range={range} clock={clock} swarms={swarms} />
          : <MemberChronology name={name} summary={summary} range={range} clock={clock} />}
      </section>
      {subject && name && <Reading subject={subject} name={name} range={range} />}
      <Lessons lessons={summary.lessons} />
    </>
  );
}
