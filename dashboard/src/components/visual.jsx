// Small building blocks for the visual language (issue #184). Icons are
// decorative (aria-hidden); text next to them carries the meaning.
import { CATEGORIES, KINDS, ROLES, category as categoryByKey, kindOf, roleOf } from "../lib/visual.js";

export const KIND_PATHS = {
  physical: "M7 8h10a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2z M12 8V4 M9.5 13h.01 M14.5 13h.01 M9 16.5h6",
  hybrid: "M6 10h8a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2z M10 10V7 M7.5 14h.01 M12.5 14h.01 M19 3l.9 1.8 1.8.9-1.8.9L19 8.4l-.9-1.8-1.8-.9 1.8-.9z",
  agent: "M8 6 3 12l5 6 M16 6l5 6-5 6 M13.5 4.5l-3 15",
  hub: "M9.5 12a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0 M12 9.5V4 M12 14.5V20 M9.8 13.3 5 16 M14.2 10.7 19 8 M9.8 10.7 5 8 M14.2 13.3 19 16",
};

export function KindIcon({ kind, color = "currentColor", size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="1.8"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d={KIND_PATHS[kind] ?? KIND_PATHS.physical} />
    </svg>
  );
}

export function MemberMark({ member, size = 28 }) {
  const role = roleOf(member);
  const kind = kindOf(member);
  const ring = Math.max(2, Math.min(3, Math.round(size / 12)));
  return (
    <span className="relative inline-flex shrink-0 items-center justify-center rounded-full bg-ink"
          style={{ width: size, height: size, boxShadow: `inset 0 0 0 ${ring}px ${ROLES[role].color}` }}
          data-role={role} data-kind={kind}>
      <KindIcon kind={kind} color={ROLES[role].color} size={Math.round(size * 0.58)} />
      <span className="sr-only">{`${ROLES[role].label} · ${KINDS[kind].label}`}</span>
    </span>
  );
}

export function CategoryLine({ category, width = 28 }) {
  const c = typeof category === "string" ? categoryByKey(category) : category;
  if (!c) return null;
  return (
    <svg width={width} height="8" viewBox={`0 0 ${width} 8`} aria-hidden="true" focusable="false">
      <line x1="1" y1="4" x2={width - 1} y2="4" stroke={c.color} strokeWidth="2.5"
            strokeDasharray={c.dash} strokeLinecap="round" />
    </svg>
  );
}

function LegendGroup({ title, children }) {
  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <span className="font-semibold text-paper-muted">{title}</span>
      {children}
    </span>
  );
}

export function Legend() {
  return (
    <div role="group" aria-label="Legend"
         className="flex flex-wrap items-center gap-x-6 gap-y-2 text-xs">
      <LegendGroup title="Role">
        {Object.entries(ROLES).map(([key, r]) => (
          <span key={key} className="flex items-center gap-1.5">
            <span aria-hidden="true" className="inline-block h-3 w-3 rounded-full"
                  style={{ boxShadow: `inset 0 0 0 2px ${r.color}` }} />
            {r.label}
          </span>
        ))}
      </LegendGroup>
      <LegendGroup title="Kind">
        {Object.entries(KINDS).map(([key, k]) => (
          <span key={key} className="flex items-center gap-1.5" title={k.note}>
            <KindIcon kind={key} size={14} />
            {k.label}
          </span>
        ))}
      </LegendGroup>
      <LegendGroup title="Data">
        {CATEGORIES.map((c) => (
          <span key={c.key} className="flex items-center gap-1.5">
            <CategoryLine category={c} width={24} />
            {c.label}
          </span>
        ))}
      </LegendGroup>
    </div>
  );
}
