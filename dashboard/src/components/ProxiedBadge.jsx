// A proxied member (#311): its key is held by a sidecar on a bridge host, so its
// signatures prove the sidecar's host signed, not the device. Shown wherever the
// member or its records appear.
export function proxiedNote(proxiedBy) {
  return `Signed for it by ${proxiedBy}, not by the device itself.`;
}

export default function ProxiedBadge({ proxiedBy }) {
  if (!proxiedBy) return null;
  const note = proxiedNote(proxiedBy);
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-warn/60 px-2 py-0.5 text-xs text-warn"
          title={note}>
      Proxied<span className="sr-only">: {note}</span>
    </span>
  );
}
