// Small shared pieces. Keep pages consistent by reusing these.
import { hashColors, shortHash } from "../lib/format.js";

export function HashStrip({ hash, label, className = "" }) {
  const colors = hashColors(hash);
  if (!colors.length) return null;
  return (
    <span className={`inline-flex items-center gap-2 ${className}`} title={hash}>
      <span className="inline-flex h-3 overflow-hidden rounded-sm" aria-hidden="true">
        {colors.map((c, i) => (
          <span key={i} style={{ background: c }} className="w-1.5" />
        ))}
      </span>
      {label !== false && <span className="hash">{label ?? shortHash(hash)}</span>}
    </span>
  );
}

const STATUS_STYLES = {
  active: "text-ok border-ok/40",
  ok: "text-ok border-ok/40",
  success: "text-ok border-ok/40",
  pending: "text-led border-led/50",
  partial: "text-warn border-warn/40",
  empty: "text-paper-muted border-ink-line",
  revoked: "text-paper-muted border-ink-line",
  rejected: "text-paper-muted border-ink-line",
  abandoned: "text-paper-muted border-ink-line",
  reflex_stop: "text-warn border-warn/40",
  stuck: "text-warn border-warn/40",
  broken: "text-bad border-bad/50",
  failed: "text-bad border-bad/50",
};

const STATUS_TEXT = { reflex_stop: "reflex stop" };

export function StatusBadge({ status }) {
  if (!status) return null;
  return (
    <span className={`inline-block rounded border px-1.5 text-xs ${STATUS_STYLES[status] || "border-ink-line text-paper-muted"}`}>
      {STATUS_TEXT[status] || status}
    </span>
  );
}

export function Loading({ what = "Loading" }) {
  return <p className="py-8 text-sm text-paper-muted" role="status">{what}…</p>;
}

export function ErrorNote({ error, onRetry }) {
  if (!error) return null;
  return (
    <div className="my-4 rounded-md border border-bad/50 bg-bad/10 p-3 text-sm" role="alert">
      <p>{error.message}</p>
      {onRetry && (
        <button className="btn mt-2" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function EmptyState({ title, children }) {
  return (
    <div className="py-12 text-center">
      <p className="text-lg">{title}</p>
      {children && <div className="mx-auto mt-2 max-w-md text-sm text-paper-muted">{children}</div>}
    </div>
  );
}

export function PageHeader({ title, children }) {
  return (
    <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <h1 className="text-xl font-bold">{title}</h1>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </header>
  );
}
