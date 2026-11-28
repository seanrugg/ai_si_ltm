// Growth tab (#343): the growth ledger (#342) for a member or a swarm, and the
// member's mind controls. Built from what exists: the ledger endpoints, Sparkline,
// HoloTable (the chart's table twin), HelpTip and the Specs tab's edit pattern.
import { useEffect, useState } from "react";
import { api } from "../../api/client.js";
import { useApi } from "../../api/useApi.js";
import { Sparkline } from "../../components/charts.jsx";
import HelpTip from "../../components/HelpTip.jsx";
import { HoloTable } from "../../components/VizFrame.jsx";
import { EmptyState, ErrorNote, Loading } from "../../components/ui.jsx";
import { localTime, relativeTime } from "../../lib/format.js";
import { BUDGET_FIELDS, STATUS_TEXT, budgetPatch, daysFor, headlines, ratioText, reasonsText, trendRows }
  from "../../lib/growth.js";
import { GROWTH_ABOUT, GROWTH_TREND, MIND_BUDGET, MIND_PAUSE } from "../../lib/help.js";

export function Headlines({ ledger }) {
  return (
    <dl className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {headlines(ledger).map((h) => (
        <div key={h.key} className="rounded border border-ink-line p-3">
          <dt className="text-xs text-paper-muted">{h.label}</dt>
          <dd className="text-2xl font-bold" data-key={h.key}>{h.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Trend({ daily }) {
  const rows = trendRows(daily);
  if (!rows.length) return null;
  const records = rows.map((r) => r.records);
  const verified = rows.map((r) => r.verified);
  const totalVerified = verified.reduce((a, b) => a + b, 0);
  const totalRecords = records.reduce((a, b) => a + b, 0);
  return (
    <section className="mb-6" aria-labelledby="growth-trend-h">
      <h3 id="growth-trend-h" className="font-bold">
        Verified lessons and records written <HelpTip id="growth-trend" label="About the trend">{GROWTH_TREND}</HelpTip>
      </h3>
      <p className="mb-2 text-sm text-paper-muted">
        {totalVerified} verified {totalVerified === 1 ? "lesson" : "lessons"} and {totalRecords} records
        over {rows.length} {rows.length === 1 ? "day" : "days"}.
      </p>
      <div className="flex flex-wrap gap-6 text-sm">
        <div className="text-led">
          <div className="text-paper-muted">Records written per day</div>
          <Sparkline series={records} label={`Records written per day, ${rows[0].day} to ${rows.at(-1).day}`} />
        </div>
        <div className="text-ok">
          <div className="text-paper-muted">Lessons verified per day</div>
          <Sparkline series={verified} label={`Lessons verified per day, ${rows[0].day} to ${rows.at(-1).day}`} />
        </div>
      </div>
      <details className="mt-2 text-sm">
        <summary className="cursor-pointer text-paper-muted">The trend as a table</summary>
        <HoloTable caption="Records written, lessons proposed and lessons verified per day"
                   rowKey={(r) => r.day} rows={rows}
                   columns={[{ key: "day", label: "Day" }, { key: "records", label: "Records", numeric: true },
                             { key: "lessons", label: "Lessons proposed", numeric: true },
                             { key: "verified", label: "Lessons verified", numeric: true }]} />
      </details>
    </section>
  );
}

export function LessonList({ lessons }) {
  if (!lessons?.length) return null;
  return (
    <section className="mb-6" aria-labelledby="growth-lessons-h">
      <h3 id="growth-lessons-h" className="font-bold">Lessons and their reviews</h3>
      <HoloTable caption="This member's lessons, newest first, with each review" rowKey={(r) => r.key} rows={lessons}
                 columns={[
                   { key: "summary", label: "Lesson", render: (r) => (
                     <span title={localTime(r.ts)}>{r.summary}{r.promoted ? " (in a policy)" : ""}</span>) },
                   { key: "status", label: "Status", render: (r) => STATUS_TEXT[r.status] ?? r.status },
                   { key: "reviews", label: "Reviews", render: (r) => (r.reviews?.length
                     ? (
                       <ul>
                         {r.reviews.map((v) => (
                           <li key={v.key} title={v.reasons}>
                             {v.by} ({v.family ?? "family unknown"}): {v.verdict}
                             {v.family && r.family && v.family === r.family ? " · same family, not counted" : ""}
                           </li>
                         ))}
                       </ul>)
                     : "none") },
                 ]} />
    </section>
  );
}

export function RecentCycles({ cycles }) {
  if (!cycles?.length) return null;
  return (
    <section className="mb-6" aria-labelledby="growth-cycles-h">
      <h3 id="growth-cycles-h" className="font-bold">Recent mind cycles</h3>
      <HoloTable caption="The latest mind cycles: what was considered, written and refused" rowKey={(r) => r.key}
                 rows={cycles}
                 columns={[
                   { key: "ts", label: "When", render: (r) => <span title={localTime(r.ts)}>{relativeTime(r.ts)}</span> },
                   { key: "mode", label: "Mode", render: (r) => (r.paused ? "paused" : `${r.mode ?? "?"} (${r.brain ?? "?"})`) },
                   { key: "considered", label: "Considered", numeric: true },
                   { key: "written", label: "Written", numeric: true },
                   { key: "gated", label: "Refused by reason", render: (r) => reasonsText(r.gated) },
                 ]} />
    </section>
  );
}

export function MindControls({ settings, onSave, busy }) {
  const [draft, setDraft] = useState({});
  const [error, setError] = useState(null);
  useEffect(() => { setDraft({ ...(settings?.budget || {}) }); }, [settings]);
  if (!settings) return null;
  const paused = settings.paused;
  return (
    <section className="mb-6 rounded border border-ink-line p-3" aria-labelledby="growth-mind-h">
      <h3 id="growth-mind-h" className="font-bold">Mind controls</h3>
      <p className="mb-2 text-sm" role="status">
        The mind is <strong>{paused ? "paused" : "running"}</strong>. Changes take effect at its next wake.
      </p>
      <div className="mb-3 flex items-center gap-2">
        <button type="button" className="btn" disabled={busy} onClick={() => onSave({ paused: !paused })}>
          {paused ? "Resume the mind" : "Pause the mind"}
        </button>
        <HelpTip id="mind-pause" label="About pausing">{MIND_PAUSE}</HelpTip>
      </div>
      <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => {
        e.preventDefault();
        const { body, error: bad } = budgetPatch(draft);
        setError(bad ?? null);
        if (body) onSave(body);
      }}>
        {BUDGET_FIELDS.map(([k, label]) => (
          <label key={k} className="flex flex-col text-sm">
            <span className="text-paper-muted">{label}</span>
            <input className="field w-36" inputMode="numeric" name={k} placeholder="device setting"
                   value={draft[k] ?? ""} onChange={(e) => setDraft((d) => ({ ...d, [k]: e.target.value }))} />
          </label>
        ))}
        <button type="submit" className="btn btn-primary" disabled={busy}>Save budget</button>
        <HelpTip id="mind-budget" label="About budgets">{MIND_BUDGET}</HelpTip>
      </form>
      {error && <p role="alert" className="mt-2 text-sm text-warn">{error}</p>}
    </section>
  );
}

export function SwarmMembers({ members }) {
  if (!members?.length) return null;
  return (
    <section className="mb-6" aria-labelledby="growth-members-h">
      <h3 id="growth-members-h" className="font-bold">Members by verified lessons</h3>
      <HoloTable caption="Each member's verified lessons and learning ratio" rowKey={(r) => r.name} rows={members}
                 columns={[{ key: "name", label: "Member" },
                           { key: "verified_lessons", label: "Verified lessons", numeric: true },
                           { key: "learning_ratio", label: "Learning ratio", render: (r) => ratioText(r.learning_ratio) }]} />
    </section>
  );
}

export function GrowthView({ ledger, subject, settings, onSave, busy }) {
  const empty = !ledger.lessons?.proposed && !ledger.cycles?.count && !ledger.reviews_given?.total;
  return (
    <div>
      <p className="mb-4 text-sm text-paper-muted">
        {GROWTH_ABOUT} <span>Model families: {ledger.families?.length ? ledger.families.join(", ") : "none reported"}.</span>
      </p>
      {subject === "member" && <MindControls settings={settings} onSave={onSave} busy={busy} />}
      {empty
        ? <EmptyState title="No mind activity in this range">Lessons, reviews and mind cycles show here once this
            {subject === "member" ? " member's mind" : " swarm's minds"} start writing them.</EmptyState>
        : (
          <>
            <Headlines ledger={ledger} />
            <Trend daily={ledger.daily} />
            {subject === "swarm" && <SwarmMembers members={ledger.members} />}
            <LessonList lessons={ledger.lesson_list} />
            <RecentCycles cycles={ledger.recent_cycles} />
          </>)}
    </div>
  );
}

export default function Growth({ subject, name, range, members }) {
  const id = subject === "member" ? (members || []).find((m) => m.name === name)?.id : null;
  const days = daysFor(range);
  const path = subject === "member" ? (id ? `/admin/members/${id}/growth` : null) : `/admin/swarms/${encodeURIComponent(name)}/growth`;
  const ledger = useApi(path ?? "/admin/growth", { days });
  const [settings, setSettings] = useState(null);
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState(null);

  useEffect(() => {
    let live = true;
    setSettings(null);
    if (!id) return undefined;
    api(`/admin/members/${id}/mind`).then((s) => { if (live) setSettings(s); }).catch(() => {});
    return () => { live = false; };
  }, [id]);

  async function save(body) {
    setBusy(true);
    setSaveError(null);
    try {
      setSettings(await api(`/admin/members/${id}/mind`, { method: "PATCH", body }));
    } catch (e) {
      setSaveError(e);
    } finally {
      setBusy(false);
    }
  }

  if (subject === "member" && !id) return <Loading what="Finding the member" />;
  if (ledger.loading && !ledger.data) return <Loading what="Working out the growth ledger" />;
  return (
    <>
      <ErrorNote error={ledger.error || saveError} onRetry={ledger.reload} />
      {ledger.data && <GrowthView ledger={ledger.data} subject={subject} settings={settings} onSave={save} busy={busy} />}
    </>
  );
}
