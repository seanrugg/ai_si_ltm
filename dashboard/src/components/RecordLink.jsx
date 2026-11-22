// Links to a record or its trace that remember where they were clicked (#393),
// and the breadcrumb those pages show.
import { Link, useLocation } from "react-router-dom";
import { originOf, recordCrumbs, recordPath, tracePath, withOrigin } from "../lib/origin.js";

/** The `from` this page passes on to record and trace links. */
export function useOrigin() {
  const { pathname, search } = useLocation();
  return originOf(pathname, search);
}

/** A <Link> to `/record/agent/seq` (or the trace, with `trace`) that carries `?from=`. */
export default function RecordLink({ agent, seq, trace = false, children, ...rest }) {
  const origin = useOrigin();
  const to = withOrigin(trace ? tracePath(agent, seq) : recordPath(agent, seq), origin);
  return <Link to={to} {...rest}>{children}</Link>;
}

/** "Timeline (v3-c7r) / Record #5 / Trace": a nav with the current page marked (508). */
export function RecordBreadcrumb({ agent, seq, page }) {
  const origin = useOrigin();
  const crumbs = recordCrumbs({ agent, seq, from: origin, page });
  return (
    <nav aria-label="Breadcrumb" className="mb-3 text-sm">
      <ol className="flex flex-wrap items-center gap-x-1.5 text-paper-muted">
        {crumbs.map((c, i) => (
          <li key={`${i}:${c.label}`} className="flex items-center gap-x-1.5">
            {i > 0 && <span aria-hidden="true" className="text-paper-faint">/</span>}
            {c.to ? (
              <Link to={c.to} className="underline decoration-ink-line hover:text-paper hover:decoration-led">{c.label}</Link>
            ) : (
              <span aria-current="page" className="text-paper">{c.label}</span>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}
