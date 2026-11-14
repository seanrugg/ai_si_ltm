// Accessible in-place help (issues #178, #377). A "?" button toggles a short note.
// Opens on click, Enter or Space (never on hover alone); closes on Escape,
// a second press, a press elsewhere on the page, or focus moving to another
// control. Escape returns focus to the button.
//
// The note is portalled to <body> and positioned with `position: fixed`, so no
// scrolling container clips it and it never widens the page. It opens toward
// whichever side has room and is clamped to the window with an 8 px margin, so
// it stays readable at 200-400% zoom. Pressing a scroll bar or scrolling the
// page does not close it; it follows the button instead. No dependencies.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export const MARGIN = 8;
const GAP = 4;
const MAX_WIDTH = 320; // 20rem

// Pure placement maths, exported for tests.
// `button` is a DOMRect-like {top, bottom, left, right}; `note` is {width, height};
// `view` is {width, height} of the window.
export function placeNote(button, note, view) {
  const width = Math.min(MAX_WIDTH, view.width - 2 * MARGIN);
  const height = note.height;
  let horizontal = "start";
  let left = button.left;
  if (left + width > view.width - MARGIN) {
    horizontal = "end";
    left = button.right - width;
  }
  left = Math.max(MARGIN, Math.min(left, view.width - MARGIN - width));

  let vertical = "bottom";
  let top = button.bottom + GAP;
  if (top + height > view.height - MARGIN) {
    const above = button.top - GAP - height;
    const roomBelow = view.height - MARGIN - (button.bottom + GAP);
    const roomAbove = button.top - GAP - MARGIN;
    if (above >= MARGIN || roomAbove > roomBelow) {
      vertical = "top";
      top = above;
    }
  }
  top = Math.max(MARGIN, Math.min(top, view.height - MARGIN - Math.min(height, view.height - 2 * MARGIN)));
  return { top, left, width, placement: `${vertical}-${horizontal}` };
}

// A press on the page's own scroll bar targets <html>, or lands outside the
// content area (clientX/Y past clientWidth/Height). Those must not close the note.
function isScrollbarPress(e) {
  const root = document.documentElement;
  if (e.target === root) return true;
  if (root.clientWidth > 0 && e.clientX >= root.clientWidth) return true;
  if (root.clientHeight > 0 && e.clientY >= root.clientHeight) return true;
  return false;
}

export default function HelpTip({ id, label, children }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState(null);
  const wrap = useRef(null);
  const button = useRef(null);
  const note = useRef(null);
  const noteId = `help-${id}`;

  const reposition = useCallback(() => {
    if (!button.current || !note.current) return;
    const b = button.current.getBoundingClientRect();
    const n = note.current.getBoundingClientRect();
    setPos(placeNote(b, { width: n.width, height: n.height },
                     { width: window.innerWidth, height: window.innerHeight }));
  }, []);

  useLayoutEffect(() => {
    if (open) reposition();
    else setPos(null);
  }, [open, reposition]);

  useEffect(() => {
    if (!open) return undefined;
    const inside = (target) =>
      (wrap.current && wrap.current.contains(target)) || (note.current && note.current.contains(target));
    function onKey(e) {
      if (e.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    }
    function onDown(e) {
      if (inside(e.target) || isScrollbarPress(e)) return;
      setOpen(false);
    }
    function onFocus(e) {
      if (!inside(e.target)) setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("focusin", onFocus);
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("focusin", onFocus);
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", reposition, true);
    };
  }, [open, reposition]);

  const style = {
    position: "fixed",
    top: pos ? pos.top : 0,
    left: pos ? pos.left : 0,
    width: pos ? pos.width : `min(20rem, calc(100vw - ${2 * MARGIN}px))`,
    maxHeight: "60vh",
    overflowY: "auto",
    visibility: pos ? "visible" : "hidden",
  };

  return (
    <span ref={wrap} className="relative inline-block align-middle">
      <button ref={button} type="button" aria-label={label} aria-expanded={open} aria-controls={noteId}
              onClick={() => setOpen((v) => !v)}
              className="ml-1 inline-flex h-5 w-5 items-center justify-center rounded-full border border-ink-line text-xs font-bold text-paper-muted hover:text-paper focus:outline-none focus-visible:ring-2 focus-visible:ring-led">
        ?
      </button>
      {open && createPortal(
        <span ref={note} id={noteId} role="note" data-placement={pos?.placement} style={style}
              className="z-50 block rounded-md border border-ink-line bg-ink p-2 text-left text-xs font-normal normal-case leading-snug tracking-normal text-paper shadow-lg">
          {children}
        </span>,
        document.body,
      )}
    </span>
  );
}
