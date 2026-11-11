// Declare a location for a member, a swarm or the server (issue #228).
import { useState } from "react";
import { api } from "../api/client.js";
import HelpTip from "./HelpTip.jsx";
import { ErrorNote } from "./ui.jsx";

export const LOCATION_HELP = "Locations are admin-only and never sent to an LRS.";

function num(v) {
  return v === "" || v == null ? null : Number(v);
}

/** The body for PUT /admin/locations/...: trimmed text, numbers or null. */
export function locationBody(draft) {
  return { label: draft.label.trim(), room: draft.room.trim() || null, lat: num(draft.lat), lon: num(draft.lon) };
}

export default function LocationForm({ subjectType, name, current, onDone, title }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState({
    label: current?.label ?? "", room: current?.room ?? "", lat: current?.lat ?? "", lon: current?.lon ?? "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const id = `loc-${subjectType}-${name}`;
  const path = `/admin/locations/${subjectType}/${encodeURIComponent(name)}`;
  const shown = current ? [current.label, current.room].filter(Boolean).join(", ") : "not set";

  async function send(method, body) {
    setBusy(true);
    setError(null);
    try {
      await api(path, { method, body });
      setOpen(false);
      onDone?.();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }
  const field = (key) => ({ id: `${id}-${key}`, value: draft[key], onChange: (e) => setDraft({ ...draft, [key]: e.target.value }) });

  return (
    <div className="mt-2 text-sm">
      <span className="text-paper-muted">{title || "Location"}: <span className="text-paper">{shown}</span></span>
      <button type="button" className="btn ml-2 py-0.5" aria-expanded={open} aria-controls={`${id}-form`}
              onClick={() => setOpen((v) => !v)}>{open ? "Cancel" : "Edit"}</button>
      {open && (
        <form id={`${id}-form`} className="mt-2 flex flex-wrap items-end gap-2" aria-label={`Location for ${name}`}
              onSubmit={(e) => { e.preventDefault(); send("PUT", locationBody(draft)); }}>
          <label className="flex flex-col text-xs text-paper-muted" htmlFor={`${id}-label`}>Label
            <input className="field w-40" required maxLength={80} {...field("label")} /></label>
          <label className="flex flex-col text-xs text-paper-muted" htmlFor={`${id}-room`}>Room
            <input className="field w-28" maxLength={40} {...field("room")} /></label>
          <label className="flex flex-col text-xs text-paper-muted" htmlFor={`${id}-lat`}>Latitude
            <input className="field w-28" type="number" step="any" min={-90} max={90} {...field("lat")} /></label>
          <label className="flex flex-col text-xs text-paper-muted" htmlFor={`${id}-lon`}>Longitude
            <input className="field w-28" type="number" step="any" min={-180} max={180} {...field("lon")} /></label>
          <button className="btn btn-primary" disabled={busy}>Save</button>
          {current && <button type="button" className="btn" disabled={busy} onClick={() => send("DELETE")}>Clear</button>}
          <HelpTip id={`${id}-help`} label="About locations">{LOCATION_HELP}</HelpTip>
          <p className="w-full text-xs text-paper-muted">Rooms group members in the Home view. Coordinates place the site on the globe.</p>
          <ErrorNote error={error} />
        </form>
      )}
    </div>
  );
}
