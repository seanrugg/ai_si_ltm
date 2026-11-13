// Member profile Specs tab (#333): hardware, language models, software and notes.
import { useEffect, useState } from "react";
import { api } from "../../api/client.js";
import { useApi } from "../../api/useApi.js";
import HelpTip from "../../components/HelpTip.jsx";
import { ErrorNote, Loading } from "../../components/ui.jsx";
import { localTime, relativeTime } from "../../lib/format.js";
import { SPECS_ABOUT } from "../../lib/help.js";
import { HARDWARE_FIELDS, SOFTWARE_FIELDS, fieldSource, patchFor, rollupRow, show } from "../../lib/specs.js";

const HOW = {
  hardware: "The harness reports hardware on start (LTMSClient.report_specs).",
  models: "Models come from report_specs, or from the cognition report (report_cognition).",
  software: "The harness reports its OS and version on start.",
  notes: "Notes are free text from the device, or set here.",
};

function Reported({ section }) {
  if (!section?.reported_at) return null;
  return <span className="text-xs text-paper-muted" title={localTime(section.reported_at)}> · last reported {relativeTime(section.reported_at)}</span>;
}

function FieldTable({ title, section, fields, sectionKey, editing, onSave }) {
  const [draft, setDraft] = useState({});
  if (!section?.value) {
    return (
      <section className="mb-6" aria-labelledby={`specs-${sectionKey}-h`}>
        <h3 id={`specs-${sectionKey}-h`} className="font-bold">{title}</h3>
        <p className="text-sm text-paper-muted">Not reported. {HOW[sectionKey]}</p>
        {editing && <EditFields fields={fields} sectionKey={sectionKey} draft={draft} setDraft={setDraft} onSave={onSave} />}
      </section>
    );
  }
  return (
    <section className="mb-6" aria-labelledby={`specs-${sectionKey}-h`}>
      <h3 id={`specs-${sectionKey}-h`} className="font-bold">{title}<Reported section={section} /></h3>
      <table className="text-sm">
        <caption className="sr-only">{title}</caption>
        <tbody>
          {fields.filter(([k]) => section.value[k] !== undefined).map(([k, label]) => (
            <tr key={k}>
              <th scope="row" className="pr-4 text-left font-normal text-paper-muted">{label}</th>
              <td className="pr-4">{show(section.value[k])}</td>
              <td className="text-xs text-paper-muted">
                {fieldSource(section, k)}
                {editing && (section.admin_fields || []).includes(k) && (
                  <button type="button" className="btn ml-2" onClick={() => onSave(patchFor(sectionKey, k, null))}>
                    Revert to reported
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {editing && <EditFields fields={fields} sectionKey={sectionKey} draft={draft} setDraft={setDraft} onSave={onSave} />}
    </section>
  );
}

function EditFields({ fields, sectionKey, draft, setDraft, onSave }) {
  return (
    <form className="mt-2 flex flex-wrap items-end gap-2" aria-label={`Override ${sectionKey}`}
          onSubmit={(e) => {
            e.preventDefault();
            const body = {};
            for (const [k, v] of Object.entries(draft)) {
              if (v.trim() !== "") Object.assign(body, { [sectionKey]: { ...(body[sectionKey] || {}), ...patchFor(sectionKey, k, v)[sectionKey] } });
            }
            if (Object.keys(body).length) onSave(body);
            setDraft({});
          }}>
      {fields.map(([k, label]) => (
        <label key={k} className="flex flex-col text-xs text-paper-muted">
          {label}
          <input className="field w-36" value={draft[k] ?? ""} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} />
        </label>
      ))}
      <button type="submit" className="btn btn-primary">Save overrides</button>
    </form>
  );
}

function Models({ section }) {
  const models = section?.value || [];
  return (
    <section className="mb-6" aria-labelledby="specs-models-h">
      <h3 id="specs-models-h" className="font-bold">
        Language models<Reported section={section} />
        {section?.source === "admin" && <span className="ml-2 text-xs text-paper-muted">(set by admin)</span>}
      </h3>
      {models.length === 0 ? (
        <p className="text-sm text-paper-muted">Not reported. {HOW.models}</p>
      ) : (
        <table className="w-full text-sm">
          <caption className="sr-only">Language models</caption>
          <thead className="text-xs text-paper-muted">
            <tr>{["Name", "Size", "Quantization", "Runtime", "On/off board", "Role"].map((h) => <th key={h} scope="col" className="pr-4 text-left">{h}</th>)}</tr>
          </thead>
          <tbody>
            {models.map((m, i) => (
              <tr key={`${m.name}-${i}`}>
                <td className="pr-4">{m.name}</td><td className="pr-4">{show(m.params)}</td><td className="pr-4">{show(m.quant)}</td>
                <td className="pr-4">{show(m.runtime)}</td><td className="pr-4">{show(m.where)}</td><td>{show(m.role)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export function SpecsView({ specs, history, onSave, error }) {
  const [editing, setEditing] = useState(false);
  return (
    <section aria-labelledby="specs-h">
      <h2 id="specs-h" className="mb-3 flex items-center gap-2 text-lg font-bold">
        Spec sheet <HelpTip id="specs-about" label="About the spec sheet">{SPECS_ABOUT}</HelpTip>
        <button type="button" className="btn ml-auto" aria-pressed={editing} onClick={() => setEditing(!editing)}>
          {editing ? "Done" : "Edit"}
        </button>
      </h2>
      <ErrorNote error={error} />
      <FieldTable title="Hardware" section={specs?.hardware} fields={HARDWARE_FIELDS} sectionKey="hardware" editing={editing} onSave={onSave} />
      <Models section={specs?.models} />
      <FieldTable title="Software" section={specs?.software} fields={SOFTWARE_FIELDS} sectionKey="software" editing={editing} onSave={onSave} />
      <section className="mb-6" aria-labelledby="specs-notes-h">
        <h3 id="specs-notes-h" className="font-bold">Notes<Reported section={specs?.notes} /></h3>
        <p className="text-sm">{specs?.notes?.value || <span className="text-paper-muted">Not reported. {HOW.notes}</span>}</p>
      </section>
      <details className="text-sm">
        <summary className="cursor-pointer font-bold">History ({history?.total ?? 0})</summary>
        <ul className="mt-2 space-y-1">
          {(history?.events || []).map((e, i) => (
            <li key={i}>
              <span title={localTime(e.ts)}>{e.ts ? relativeTime(e.ts) : ""}</span>: {(e.sections || []).join(", ")} changed
              by {e.actor}{e.source === "admin" ? " (override)" : ""}
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}

export default function Specs({ name, members }) {
  const id = (members || []).find((m) => m.name === name)?.id;
  if (!id) return <p className="py-8 text-sm text-paper-muted">This member isn&apos;t in the list.</p>;
  return <MemberSpecs id={id} />;
}

function MemberSpecs({ id }) {
  const specs = useApi(`/admin/members/${id}/specs`);
  const history = useApi(`/admin/members/${id}/specs/history`);
  const [error, setError] = useState(null);
  if (specs.loading && !specs.data) return <Loading what="Loading the spec sheet" />;

  async function save(body) {
    setError(null);
    try {
      await api(`/admin/members/${id}/specs`, { method: "PATCH", body });
      specs.reload();
      history.reload();
    } catch (err) {
      setError(err);
    }
  }
  return <SpecsView specs={specs.data?.specs} history={history.data} onSave={save} error={error || specs.error} />;
}

/** Swarm roll-up (#333): one line per member, to compare a swarm at a glance. */
export function SpecsRollup({ swarm, members }) {
  const [rows, setRows] = useState(null);
  const names = (swarm?.members || []).map((m) => m.name);
  const key = names.join(",");
  useEffect(() => {
    let live = true;
    Promise.all(names.map(async (n) => {
      const id = (members || []).find((m) => m.name === n)?.id;
      const res = id ? await api(`/admin/members/${id}/specs`).catch(() => null) : null;
      return rollupRow(n, res?.specs);
    })).then((r) => { if (live) setRows(r); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, members]);
  if (!rows?.length) return null;
  return (
    <table className="mt-4 w-full text-sm">
      <caption className="text-left font-bold">Specs at a glance</caption>
      <thead className="text-xs text-paper-muted">
        <tr>{["Member", "Board", "RAM", "Primary model", "AI runs"].map((h) => <th key={h} scope="col" className="pr-4 text-left">{h}</th>)}</tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.name}><th scope="row" className="pr-4 text-left font-normal">{r.name}</th><td className="pr-4">{r.board}</td>
            <td className="pr-4">{r.ram}</td><td className="pr-4">{r.model}</td><td>{r.where}</td></tr>
        ))}
      </tbody>
    </table>
  );
}
