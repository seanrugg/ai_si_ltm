// Declare a location for a member, a swarm or the server (issue #228).
import { useState } from "react";
import { api } from "../api/client.js";
import { localTime } from "../lib/format.js";
import GeoCredit from "./GeoCredit.jsx";
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

/** "±20 km", or metres under a kilometre, for a hover title; null when unknown. */
export function accuracyText(km) {
  if (typeof km !== "number" || !Number.isFinite(km)) return null;
  return km < 1 ? `±${Math.round(km * 1000)} m` : `±${Math.round(km)} km`;
}

/**
 * A gps or network estimate (#399): its label, then "(gps)" or
 * "(network estimate · <address>, via <hub>)". The address is the one that was
 * looked up (the hub's when relayed); one the retention prune removed is said so.
 */
export function EstimateText({ estimate }) {
  const network = estimate.source === "network";
  const acc = accuracyText(estimate.accuracy_km);
  return (
    <>
      <span title={acc ?? undefined}>{estimate.label || "unnamed"}</span>{" "}
      <span className="text-paper-muted">
        ({network ? "network estimate" : "gps"}
        {network && (estimate.ip
          ? <> · <span className="hash" title={estimate.ip_seen_at ? `last seen ${localTime(estimate.ip_seen_at)}` : undefined}>{estimate.ip}</span></>
          : " · address no longer kept")}
        {estimate.via_hub ? `, via ${estimate.via_hub}` : ""}
        {estimate.inherited_from ? `, from ${estimate.inherited_from}` : ""})
      </span>
    </>
  );
}

/**
 * ``inherited``: a declared location this subject gets from its swarm (#285), shown when it has none of its own.
 * ``suggestion``: a network estimate to pre-fill the form with; it is never saved without the admin pressing Save.
 * ``estimate``: a gps or network entry shown when nothing is declared or inherited (#399), with ``geo``
 * for the DB-IP credit; ``credit={false}`` leaves that credit to the page, for lists that show it once;
 * ``unplaced``: why there is no location at all, shown after "not set".
 */
export default function LocationForm({ subjectType, name, current, onDone, title, inherited, suggestion, prompt,
  estimate, geo, unplaced, credit = true }) {
  const [open, setOpen] = useState(false);
  const seed = current ?? suggestion ?? null;
  const [draft, setDraft] = useState({
    label: seed?.label ?? "", room: current?.room ?? "", lat: seed?.lat ?? "", lon: seed?.lon ?? "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const id = `loc-${subjectType}-${name}`;
  const path = `/admin/locations/${subjectType}/${encodeURIComponent(name)}`;
  const shown = current ? [current.label, current.room].filter(Boolean).join(", ")
    : inherited ? `${[inherited.label, inherited.room].filter(Boolean).join(", ")} (from ${inherited.inherited_from})`
      : estimate ? <EstimateText estimate={estimate} />
        : "not set";
  const showEstimate = !current && !inherited && estimate;

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
      {!current && !inherited && !estimate && unplaced && <span className="ml-2 text-xs text-paper-muted">{unplaced}</span>}
      {!current && prompt && <span className="ml-2 text-xs text-paper-muted">{prompt}</span>}
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
          {!current && suggestion && (
            <p className="w-full text-xs text-warn">
              Suggested from {suggestion.source === "gps" ? "a GPS fix" : "a member\u2019s network address"} ({suggestion.label}); check it before saving.
            </p>
          )}
          <p className="w-full text-xs text-paper-muted">Rooms group members in the Home view. Coordinates place the site on the globe.</p>
          <ErrorNote error={error} />
        </form>
      )}
      {credit && showEstimate && estimate.source === "network" && <GeoCredit geo={geo} className="mt-1" />}
    </div>
  );
}
