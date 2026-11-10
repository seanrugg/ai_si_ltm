// Accessible in-place help (issue #178). A "?" button toggles a short note.
// Opens on click, Enter or Space (never on hover alone); closes on Escape,
// a second press, or a click outside. Escape returns focus to the button.
import { useEffect, useRef, useState } from "react";

export default function HelpTip({ id, label, children }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef(null);
  const button = useRef(null);
  const noteId = `help-${id}`;

  useEffect(() => {
    if (!open) return undefined;
    function onKey(e) {
      if (e.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    }
    function onDown(e) {
      if (wrap.current && !wrap.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  return (
    <span ref={wrap} className="relative inline-block align-middle">
      <button ref={button} type="button" aria-label={label} aria-expanded={open} aria-controls={noteId}
              onClick={() => setOpen((v) => !v)}
              className="ml-1 inline-flex h-5 w-5 items-center justify-center rounded-full border border-ink-line text-xs font-bold text-paper-muted hover:text-paper focus:outline-none focus-visible:ring-2 focus-visible:ring-led">
        ?
      </button>
      {open && (
        <span id={noteId} role="note"
              className="absolute left-0 top-6 z-20 block w-64 max-w-[80vw] rounded-md border border-ink-line bg-ink p-2 text-xs font-normal leading-snug text-paper shadow-lg">
          {children}
        </span>
      )}
    </span>
  );
}
