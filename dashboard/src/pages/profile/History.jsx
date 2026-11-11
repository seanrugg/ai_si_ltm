// Profile History tab: membership, learning and admin events, newest first (issue #197).
import { localTime } from "../../lib/format.js";
import { historyMarker } from "../../lib/profile.js";
import { NEUTRAL, ROLES, category } from "../../lib/visual.js";

export default function History({ history }) {
  const events = history?.events || [];
  const opts = { ROLES, NEUTRAL, learnColor: category("learn").color };
  return (
    <section aria-labelledby="history-h">
      <h2 id="history-h" className="mb-1 text-lg font-bold">History</h2>
      <p className="mb-3 text-xs text-paper-muted">
        Membership events take the color of the role joined, lessons the Learning &amp; rules color, and admin events are neutral.
      </p>
      {events.length === 0 ? (
        <p className="text-sm text-paper-muted">Nothing recorded yet.</p>
      ) : (
        <ol className="panel">
          {events.map((e, i) => {
            const m = historyMarker(e, opts);
            return (
              <li key={`${e.ts}-${i}`} className="flex items-start gap-3 border-b border-ink-line px-3 py-2 last:border-b-0">
                <span className="w-44 shrink-0 text-xs text-paper-muted">{localTime(e.ts)}</span>
                <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full"
                      style={{ boxShadow: `inset 0 0 0 2px ${m.color}` }} data-icon={m.icon}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={m.color} strokeWidth="2"
                       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d={m.iconPath} /></svg>
                </span>
                <span>
                  <span className="font-bold">{e.title}</span>
                  {e.detail && <span className="ml-2 text-sm text-paper-muted">{e.detail}</span>}
                </span>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
