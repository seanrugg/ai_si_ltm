// Plain SVG chart components for the activity view (issue #74).
// Zero runtime dependencies: inline SVG only.
import { stack, typeColor, TYPE_GROUPS } from "../lib/activity.js";

const BAR_WIDTH = 180;
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

export function StackedBar({ byType, total, onSegment, label }) {
  const segments = stack(byType);
  const barTotal = total || segments.reduce((sum, s) => sum + s.count, 0);
  return (
    <div className="relative" style={{ minHeight: TOUCH_HEIGHT }} aria-label={label}>
      <div className="flex" style={{ height: BAR_HEIGHT }}>
        {segments.map((s) => {
          const width = barTotal ? Math.max(4, (s.count / barTotal) * BAR_WIDTH) : 4;
          return (
            <button
              key={s.type}
              onClick={() => onSegment && onSegment(s.type)}
              aria-label={`${s.type}: ${s.count} records`}
              title={`${s.type}: ${s.count} records`}
              className="flex-none rounded-sm"
              style={{ backgroundColor: s.color, minWidth: 4, width }}
            />
          );
        })}
      </div>
    </div>
  );
}

export function TypeLegend({ counts, onType }) {
  return (
    <div className="flex flex-wrap gap-3 text-xs leading-6">
      {TYPE_GROUPS.map((g) => (
        <div key={g.group} className="flex flex-col gap-1">
          <span className="text-paper-faint">{g.group}</span>
          <div className="flex flex-wrap gap-2">
            {g.types.map((type) => {
              const count = counts[type] ?? 0;
              return (
                <button
                  key={type}
                  onClick={() => onType && onType(type)}
                  className="flex items-center gap-1.5 rounded border border-ink-line px-2 py-0.5"
                  style={{ backgroundColor: typeColor(type), color: "#0F1828" }}
                >
                  <span
                    aria-hidden="true"
                    style={{
                      width: 8,
                      height: 8,
          borderRadius: 2,
          backgroundColor: typeColor(type),
          display: "inline-block",
                    }}
                  />
                  {type} {count}
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
