// The timeline's grouped Types control (issue #181): one fieldset per group,
// a group checkbox (mixed when partly selected), a checkbox per type, and
// All / None / Default. Selections apply as they change, through `onChange`
// with the serialized `type` param. Escape closes the panel and returns
// focus to the button; a click outside closes it (same pattern as HelpTip).
import { useEffect, useId, useRef, useState } from "react";
import { ALL_TYPES, DEFAULT_TYPES, GROUPS, LABELS, parseTypes, serializeTypes, summary } from "../lib/typeFilter.js";

export default function TypePicker({ value, onChange }) {
  const [open, setOpen] = useState(false);
  const panelId = `type-picker-${useId()}`;
  const root = useRef(null);
  const button = useRef(null);
  const selected = parseTypes(value);

  function apply(next) {
    onChange(serializeTypes(next));
  }

  function toggleType(t) {
    const next = new Set(selected);
    if (next.has(t)) next.delete(t);
    else next.add(t);
    apply(next);
  }

  function toggleGroup(types, select) {
    const next = new Set(selected);
    for (const t of types) {
      if (select) next.add(t);
      else next.delete(t);
    }
    apply(next);
  }

  useEffect(() => {
    if (!open) return undefined;
    function onKey(e) {
      if (e.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    }
    function onDown(e) {
      if (root.current && !root.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  return (
    <span ref={root} className="relative inline-block align-middle">
      <button ref={button} type="button" className="btn"
              aria-expanded={open} aria-controls={open ? panelId : undefined}
              onClick={() => setOpen((v) => !v)}>
        {summary(selected)}
      </button>
      {open && (
        <div id={panelId} role="group" aria-label="Record types"
             className="absolute left-0 top-8 z-20 block w-72 rounded-md border border-ink-line bg-ink p-3 text-sm shadow-lg">
          {GROUPS.map((g) => {
            const selectedCount = g.types.filter((t) => selected.has(t)).length;
            const all = selectedCount === g.types.length;
            const mixed = selectedCount > 0 && !all;
            return (
              <fieldset key={g.label} className="mb-2 border-0 p-0">
                <legend className="mb-1 font-semibold">{g.label}</legend>
                <label className="mb-1 flex cursor-pointer items-center gap-2">
                  <input type="checkbox" className="accent-led"
                         ref={(el) => { if (el) el.indeterminate = mixed; }}
                         checked={all}
                         aria-checked={mixed ? "mixed" : undefined}
                         onChange={() => toggleGroup(g.types, !all)} />
                  <span className="font-medium">{g.label}</span>
                </label>
                <div className="ml-5">
                  {g.types.map((t) => (
                    <label key={t} className="flex cursor-pointer items-center gap-2 py-0.5">
                      <input type="checkbox" className="accent-led"
                             checked={selected.has(t)} onChange={() => toggleType(t)} />
                      {LABELS[t]}
                    </label>
                  ))}
                </div>
              </fieldset>
            );
          })}
          <div className="mt-2 flex gap-2 border-t border-ink-line pt-2">
            <button type="button" className="btn" onClick={() => apply(new Set(ALL_TYPES))}>All</button>
            <button type="button" className="btn" onClick={() => apply(new Set())}>None</button>
            <button type="button" className="btn" onClick={() => apply(new Set(DEFAULT_TYPES))}>Default</button>
          </div>
        </div>
      )}
    </span>
  );
}
