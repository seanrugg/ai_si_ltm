// Profile Activity tab: the chain from sensing to learning (issue #197).
import { useState } from "react";
import HelpTip from "../../components/HelpTip.jsx";
import { COUNT_SELF_REPORT } from "../../lib/help.js";
import { Link } from "react-router-dom";
import { useApi } from "../../api/useApi.js";
import { CategoryLine } from "../../components/visual.jsx";
import { localTime } from "../../lib/format.js";
import { category } from "../../lib/visual.js";

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

export default function Activity({ summary, subject, name, range }) {
  const [step, setStep] = useState("sensed");
  const act = category("act");
  const outcomes = Object.entries(summary.outcomes || {});
  const maxOutcome = Math.max(1, ...outcomes.map(([, n]) => n));
  const current = STEPS.find((s) => s.key === step);
  return (
    <>
      <section aria-labelledby="chain-h" className="mb-6">
        <h2 id="chain-h" className="mb-2 text-lg font-bold">From sensing to learning</h2>
        <div role="group" aria-label="Steps" className="flex flex-wrap gap-2">
          {STEPS.map((s, i) => (
            <span key={s.key} className="flex items-center gap-2">
              {i > 0 && <span aria-hidden="true" className="text-paper-faint">→</span>}
              <button type="button" aria-pressed={step === s.key} onClick={() => setStep(s.key)}
                      className={`panel flex flex-col items-start px-3 py-2 text-left ${step === s.key ? "ring-1 ring-led" : ""}`}>
                <span className="flex items-center gap-2 text-sm"><CategoryLine category={s.cat} width={20} />{s.label}</span>
                <span className="text-xl font-bold">{(summary.flow?.[s.key] ?? 0).toLocaleString()}</span>
                <span className="text-xs text-paper-muted">{s.link}</span>
              </button>
            </span>
          ))}
        </div>
        <p className="mt-3 text-sm" aria-live="polite">{current.note}</p>
        <p className="mt-2 flex items-center gap-1 text-xs text-paper-muted">
          Self-reports (heartbeat, health), not part of this chain: {(summary.flow?.self_report ?? 0).toLocaleString()}
          <HelpTip id="chain-self-report" label="What a self-report is">{COUNT_SELF_REPORT}</HelpTip>
        </p>
      </section>

      <section aria-labelledby="outcomes-h" className="mb-6">
        <h2 id="outcomes-h" className="mb-2 text-lg font-bold">Action outcomes</h2>
        <ul className="space-y-1 text-sm">
          {outcomes.map(([o, n]) => (
            <li key={o} className="flex items-center gap-3">
              <span className="w-24">{o.replace("_", " ")}</span>
              <span className="h-3 rounded" aria-hidden="true"
                    style={{ width: `${Math.max(2, (n / maxOutcome) * 260)}px`, background: act.color, opacity: OUTCOME_OPACITY[o] ?? 0.5 }} />
              <span>{n}</span>
            </li>
          ))}
        </ul>
      </section>

      {subject && name && <Reading subject={subject} name={name} range={range} />}

      <section aria-labelledby="lessons-h">
        <h2 id="lessons-h" className="mb-2 text-lg font-bold">Recent lessons</h2>
        {(summary.lessons || []).length === 0 ? (
          <p className="text-sm text-paper-muted">No lessons in this range.</p>
        ) : (
          <ul className="panel">
            {summary.lessons.map((l) => (
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
    </>
  );
}
