// Get started checklist (#379): shown in the nav while a new fleet isn't set up.
// Each step links to its page and ticks itself from server state
// (GET /admin/nav-status `setup`). The per-member walkthrough after approving a
// device stays in GuidedSetup on the Members page; this list points there.
import { Link } from "react-router-dom";
import HelpTip from "./HelpTip.jsx";
import { GET_STARTED } from "../lib/help.js";
import { CHECKLIST } from "../lib/nav.js";

export default function GetStarted({ setup, onDismiss, onPick }) {
  const done = CHECKLIST.filter((c) => setup[c.key]).length;
  return (
    <section aria-labelledby="get-started-h" className="mb-3 rounded-md border border-ink-line p-3 text-sm">
      <h2 id="get-started-h" className="flex items-center text-xs font-bold uppercase tracking-wide text-paper-muted">
        Get started
        <HelpTip id="nav-get-started" label="About Get started">{GET_STARTED}</HelpTip>
      </h2>
      <p className="mt-1 text-xs text-paper-muted">{done} of {CHECKLIST.length} done</p>
      <ul className="mt-2 flex flex-col gap-1">
        {CHECKLIST.map((c) => (
          <li key={c.key} className="flex items-start gap-2">
            {/* A tick or an empty box: shape, not colour alone. */}
            <span aria-hidden="true" className={setup[c.key] ? "text-ok" : "text-paper-muted"}>
              {setup[c.key] ? "✓" : "□"}
            </span>
            <Link to={c.to} onClick={onPick}
                  className={`hover:underline ${setup[c.key] ? "text-paper-muted line-through" : "text-paper"}`}>
              {c.label}{c.optional && " (optional)"}
              <span className="sr-only">{setup[c.key] ? ": done" : ": not done"}</span>
            </Link>
          </li>
        ))}
      </ul>
      <button type="button" className="mt-2 text-xs text-paper-muted underline hover:text-paper" onClick={onDismiss}>
        Dismiss checklist
      </button>
    </section>
  );
}
