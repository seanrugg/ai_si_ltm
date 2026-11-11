// The data card for a site or member on the Network tab (issues #195, #196).
// A dialog: focus moves in on open and back to the opener on close.
import { useEffect, useRef } from "react";
import { CategoryLine, MemberMark } from "../../components/visual.jsx";
import { relativeTime } from "../../lib/format.js";
import { CATEGORIES, KINDS, ROLES, kindOf } from "../../lib/visual.js";

export function MemberFacts({ member, net, loc, rates }) {
  const role = ROLES[net?.role] ? net.role : "free";
  const kind = kindOf({ ...member, role });
  const best = loc?.best;
  const max = Math.max(0.01, ...CATEGORIES.map((c) => rates?.[c.key] || 0));
  return (
    <div className="text-sm">
      <div className="mb-2 flex items-center gap-2">
        <MemberMark member={{ ...member, role }} size={28} />
        <span className="font-bold">{member.name}</span>
        <span className="text-paper-muted">{`${ROLES[role].label} · ${KINDS[kind].label}`}</span>
      </div>
      <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1">
        <dt className="text-paper-muted">Path</dt><dd>{net?.path ?? "—"}</dd>
        <dt className="text-paper-muted">Seen by LTMS</dt><dd>{net?.seen?.ip ?? "—"}</dd>
        <dt className="text-paper-muted">Reported LAN</dt>
        <dd>{net?.reported?.length ? net.reported.map((a) => `${a.ip} (${a.source})`).join(", ") : "—"}</dd>
        <dt className="text-paper-muted">Location</dt>
        <dd>{best ? `${[best.label, best.room].filter(Boolean).join(", ") || "unnamed"} (${best.source})` : "unknown"}</dd>
        <dt className="text-paper-muted">Last seen</dt>
        <dd>{member.last_seen_at ? relativeTime(member.last_seen_at) : "never"}</dd>
      </dl>
      {rates && (
        <ul className="mt-2 space-y-1" aria-label="Records per minute, last hour">
          {CATEGORIES.map((c) => (
            <li key={c.key} className="flex items-center gap-2">
              <CategoryLine category={c} width={18} />
              <span className="w-32">{c.label}</span>
              <span className="h-1.5 rounded" aria-hidden="true"
                    style={{ width: `${Math.max(2, ((rates[c.key] || 0) / max) * 80)}px`, background: c.color }} />
              <span>{(rates[c.key] || 0).toFixed(1)}/min</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function DataCard({ title, onClose, children, actions }) {
  const ref = useRef(null);
  const opener = useRef(typeof document !== "undefined" ? document.activeElement : null);
  useEffect(() => {
    ref.current?.querySelector("button, a, [tabindex]")?.focus();
    const back = opener.current;
    function onKey(e) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      back?.focus?.();
    };
  }, [onClose]);
  return (
    <div ref={ref} role="dialog" aria-modal="true" aria-labelledby="data-card-h"
         className="panel absolute right-3 top-3 z-20 w-80 max-w-[90%] p-3 shadow-lg">
      <div className="mb-2 flex items-center justify-between">
        <h3 id="data-card-h" className="font-bold">{title}</h3>
        <button type="button" className="btn px-2 py-0.5" onClick={onClose} aria-label="Close">×</button>
      </div>
      {children}
      {actions && <div className="mt-3 flex gap-2">{actions}</div>}
    </div>
  );
}
