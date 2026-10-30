// Plain SVG chart components for the activity view (issue #74).
// Zero runtime dependencies: inline SVG only.
import { stack, typeColor, TYPE_GROUPS } from "../lib/activity.js";

const BAR_HEIGHT = 28;

// Touch target: the bar is the visible part of a >= 44 px tall hit area.
const TOUCH_HEIGHT = 44;

export function Sparkline({ series, label }) {
  const width = 120;
  const height = BAR_HEIGHT;
  const max = Math.max(0, ...series);
  const flat = max === 0 || series.length === 0;
  const points = series
    .map((v, i) => {
      const x = (i / Math.max(series.length - 1, 1)) * width;
      const y = flat ? height / 2 : height - (v / max) * height;
      return `${x},${y}`;
    })
    .join(" ");
  // Ensure at least two points so an empty or all-zero series draws a flat line.
  const line = points || `0,${height / 2} ${width},${height / 2}`;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} className="block">
      <polyline points={line} fill="none" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}

export function StackedBar({ byType, onSegment, label, active = null }) {
  // Segments share the width in proportion to their counts (responsive, no fixed
  // pixel width). Each segment is a full-height button, so the whole >= 44 px
  // area is clickable; the colored bar inside it is the visible part.
  const segments = stack(byType);
  return (
    <div role="group" aria-label={label} className="flex w-full" style={{ height: TOUCH_HEIGHT }}>
      {segments.map((s) => (
        <button
          type="button"
          key={s.type}
          onClick={() => onSegment && onSegment(s.type)}
          aria-label={`${s.type}: ${s.count} records`}
          aria-pressed={onSegment ? active === s.type : undefined}
          title={`${s.type}: ${s.count} records`}
          className="flex items-center px-px focus-visible:outline focus-visible:outline-2 focus-visible:outline-led"
          style={{ flex: `${s.count} 1 0`, minWidth: 4 }}
        >
          <span
            aria-hidden="true"
            className="block w-full rounded-sm"
            style={{
              height: BAR_HEIGHT,
              backgroundColor: s.color,
              opacity: active && active !== s.type ? 0.4 : 1,
            }}
          />
        </button>
      ))}
    </div>
  );
}

export function TypeLegend({ counts, onType, active = null }) {
  // Neutral button with a colored swatch: the type name and count are text in the
  // normal paper color (4.5:1), so color is never the only cue.
  return (
    <div className="flex flex-wrap gap-3 text-sm">
      {TYPE_GROUPS.map((g) => (
        <div key={g.group} className="flex flex-col gap-1">
          <span className="text-xs text-paper-faint">{g.group}</span>
          <div className="flex flex-wrap gap-2">
            {g.types.map((type) => (
              <button
                type="button"
                key={type}
                onClick={() => onType && onType(type)}
                aria-pressed={onType ? active === type : undefined}
                className={`flex min-h-[44px] items-center gap-2 rounded border px-3 ${
                  active === type ? "border-led bg-ink-hover" : "border-ink-line bg-ink-raised"
                }`}
              >
                <span aria-hidden="true" className="inline-block h-3 w-3 rounded-sm" style={{ backgroundColor: typeColor(type) }} />
                <span>{type}</span>
                <span className="font-mono text-paper-muted">{counts[type] ?? 0}</span>
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
