// The Access page's New token form (#446): pickers instead of free text, a
// HelpTip on every field, and a plain-words preview built from the exact scope
// that is sent. The request body is unchanged: {name, scope, expires_at, owner, purpose}.
import { useId, useState } from "react";
import HelpTip from "./HelpTip.jsx";
import { ErrorNote } from "./ui.jsx";
import {
  TOKEN_EXPIRES, TOKEN_MEMBERS, TOKEN_NAME, TOKEN_OWNER, TOKEN_PAYLOADS, TOKEN_PURPOSE, TOKEN_SWARMS, TOKEN_TYPES,
} from "../lib/help.js";
import {
  EXPIRY_PICKS, FREE_AGENTS, RECORD_TYPES, dateInDays, emptyTokenForm, previewText, tokenBody, tokenFormProblem,
  tokenScope, withPayloads,
} from "../lib/tokenScope.js";
import { KINDS, kindOf } from "../lib/visual.js";

function toggle(list, value) {
  return list.includes(value) ? list.filter((x) => x !== value) : [...list, value];
}

/**
 * A searchable multi-select: a filter box over a list of checkboxes, in a
 * fieldset with a legend. Native controls, so Tab, Space and screen readers
 * work as usual. `options` are {value, label, detail}.
 */
export function PickList({ id, legend, help, helpLabel, options, selected, onChange, emptyText, searchLabel,
  searchable = options.length > 6 }) {
  const [filter, setFilter] = useState("");
  const q = filter.trim().toLowerCase();
  const shown = q ? options.filter((o) => `${o.label} ${o.detail ?? ""}`.toLowerCase().includes(q)) : options;
  return (
    <fieldset className="min-w-0">
      <legend className="mb-1 flex items-center gap-1 text-sm font-bold text-paper-muted">
        {legend} <HelpTip id={`${id}-help`} label={helpLabel}>{help}</HelpTip>
      </legend>
      <p className="mb-1 text-xs text-paper-muted" id={`${id}-state`}>
        {selected.length ? `${selected.length} chosen` : emptyText}
        {selected.length > 0 && (
          <button type="button" className="ml-2 underline" onClick={() => onChange([])}>Clear</button>
        )}
      </p>
      {searchable && (
        <input className="field mb-2 h-9 w-full" type="search" value={filter} aria-label={searchLabel}
               aria-describedby={`${id}-state`} onChange={(e) => setFilter(e.target.value)} placeholder="Search" />
      )}
      <div className="max-h-48 space-y-1 overflow-y-auto pr-1 text-sm">
        {shown.map((o) => (
          <label key={o.value} className="flex cursor-pointer items-baseline gap-2">
            <input type="checkbox" className="accent-led h-4 w-4 shrink-0" checked={selected.includes(o.value)}
                   onChange={() => onChange(toggle(selected, o.value))} />
            <span>{o.label}{o.detail && <span className="ml-1 text-xs text-paper-muted">{o.detail}</span>}</span>
          </label>
        ))}
        {shown.length === 0 && <p className="text-xs text-paper-faint">No match.</p>}
      </div>
    </fieldset>
  );
}

/** Picker options from /admin/members and /admin/swarms (shapes as the API returns them). */
export function memberOptions(members, swarms) {
  const swarmOf = new Map();
  for (const s of swarms || []) for (const m of s.members || []) swarmOf.set(m.name, { swarm: s.name, role: m.role });
  return (members || [])
    .filter((m) => m.status === "active")
    .map((m) => {
      const sw = swarmOf.get(m.name);
      const kind = KINDS[kindOf({ ...m, role: sw?.role })]?.label ?? m.kind;
      return { value: m.name, label: m.name, detail: `${kind} · ${sw ? sw.swarm : "free agent"}` };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
}

export function swarmOptions(swarms) {
  return [
    ...(swarms || []).filter((s) => s.status !== "removed").map((s) => ({ value: s.name, label: s.name }))
      .sort((a, b) => a.label.localeCompare(b.label)),
    { value: FREE_AGENTS, label: "Free agents (no swarm)" },
  ];
}

export default function NewTokenForm({ members, swarms, busy, error, onCreate, onCancel, now }) {
  const fid = useId();
  const [form, setForm] = useState(emptyTokenForm);
  const [problem, setProblem] = useState(null);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const scope = tokenScope(form);
  const allTypes = form.types.length === RECORD_TYPES.length;

  function submit(e) {
    e.preventDefault();
    const why = tokenFormProblem(form);
    setProblem(why);
    if (!why) onCreate(tokenBody(form));
  }

  const text = (key, label, help, placeholder, extra = {}) => (
    <div className="flex flex-col gap-1">
      <span className="flex items-center gap-1 text-sm text-paper-muted">
        <label htmlFor={`${fid}-${key}`}>{label}</label>
        <HelpTip id={`${fid}-${key}-help`} label={`About the token ${key}`}>{help}</HelpTip>
      </span>
      <input id={`${fid}-${key}`} className="field h-11 w-56" value={form[key]} placeholder={placeholder}
             onChange={(e) => set({ [key]: e.target.value })} {...extra} />
    </div>
  );

  return (
    <form onSubmit={submit} noValidate aria-label="New token" className="space-y-5">
      <div className="flex flex-wrap gap-4">
        {text("name", "Name (required)", TOKEN_NAME, "daily-read", { "aria-required": "true", maxLength: 64 })}
        {text("owner", "Owner", TOKEN_OWNER, "Sean (Claude connector)", { maxLength: 128 })}
        {text("purpose", "Purpose", TOKEN_PURPOSE, "Daily read of robot outcomes", { maxLength: 500 })}
      </div>

      <fieldset aria-describedby={`${fid}-types-state`}>
        <legend className="mb-1 flex items-center gap-1 text-sm font-bold text-paper-muted">
          Record types (at least one) <HelpTip id={`${fid}-types-help`} label="About record types">{TOKEN_TYPES}</HelpTip>
        </legend>
        <label className="mb-2 flex cursor-pointer items-center gap-2 text-sm font-semibold">
          <input type="checkbox" className="accent-led h-5 w-5" checked={allTypes}
                 onChange={() => set({ types: allTypes ? [] : [...RECORD_TYPES] })} />
          All types
        </label>
        <div className="grid grid-cols-3 gap-2 text-sm">
          {RECORD_TYPES.map((type) => (
            <label key={type} className="flex cursor-pointer items-center gap-2">
              <input type="checkbox" className="accent-led h-5 w-5" checked={form.types.includes(type)}
                     onChange={() => set({ types: toggle(form.types, type) })} />
              {type}
            </label>
          ))}
        </div>
        <p id={`${fid}-types-state`} className="mt-1 text-xs text-paper-muted">
          {form.types.length ? `${form.types.length} of ${RECORD_TYPES.length} types` : "None chosen yet."}
        </p>
      </fieldset>

      <div className="grid gap-4 md:grid-cols-2">
        <PickList id={`${fid}-members`} legend="Members" help={TOKEN_MEMBERS} helpLabel="About limiting members"
                  options={memberOptions(members, swarms)} selected={form.agents} searchLabel="Search members" searchable
                  onChange={(agents) => set({ agents })} emptyText="None chosen: every member." />
        <PickList id={`${fid}-swarms`} legend="Swarms" help={TOKEN_SWARMS} helpLabel="About limiting swarms"
                  options={swarmOptions(swarms)} selected={form.swarms} searchLabel="Search swarms"
                  onChange={(s) => set({ swarms: s })} emptyText="None chosen: every swarm." />
      </div>

      <div>
        <span className="flex items-center gap-2 text-sm">
          <label className="flex cursor-pointer items-center gap-2">
            <input type="checkbox" className="accent-led h-5 w-5" checked={form.payloads}
                   aria-describedby={form.payloads ? `${fid}-payloads-warn` : undefined}
                   onChange={(e) => setForm((f) => withPayloads(f, e.target.checked, now ? now() : new Date()))} />
            Include payloads
          </label>
          <HelpTip id={`${fid}-payloads-help`} label="About payloads">{TOKEN_PAYLOADS}</HelpTip>
        </span>
        {form.payloads && (
          <p id={`${fid}-payloads-warn`} className="mt-1 text-sm text-warn">
            This token reads full record contents, not just headers. Use an expiry.
          </p>
        )}
      </div>

      <fieldset>
        <legend className="mb-1 flex items-center gap-1 text-sm font-bold text-paper-muted">
          Expires <HelpTip id={`${fid}-expires-help`} label="About token expiry">{TOKEN_EXPIRES}</HelpTip>
        </legend>
        <div className="flex flex-wrap items-center gap-2">
          {EXPIRY_PICKS.map((p) => {
            const value = p.days === null ? "" : dateInDays(p.days, now ? now() : new Date());
            return (
              <button key={p.key} type="button" className="btn h-9" aria-pressed={form.expires === value}
                      onClick={() => set({ expires: value })}>
                {p.label}
              </button>
            );
          })}
          <label htmlFor={`${fid}-expires`} className="sr-only">Expiry date</label>
          <input id={`${fid}-expires`} className="field h-9 w-40" type="date" value={form.expires}
                 onChange={(e) => set({ expires: e.target.value })} />
        </div>
      </fieldset>

      <section aria-labelledby={`${fid}-preview-h`} className="rounded-md border border-led/40 bg-led/5 p-3">
        <h3 id={`${fid}-preview-h`} className="text-sm font-bold">This token</h3>
        <p className="text-sm" aria-live="polite" data-testid="token-preview">{previewText(scope, form.expires)}</p>
      </section>

      {problem && <p className="text-sm font-bold text-bad" role="alert">{problem}</p>}
      <ErrorNote error={error} />
      <div className="flex flex-wrap gap-2">
        <button type="submit" className="btn btn-primary h-11" disabled={busy}>Create token</button>
        <button type="button" className="btn h-11" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}
