// Rename a swarm (issue #231): an inline form with a confirm step, because
// the swarm's MQTT topics change with its name.
import { useState } from "react";
import { api } from "../api/client.js";
import { SWARM_RENAME } from "../lib/help.js";
import HelpTip from "./HelpTip.jsx";
import { ErrorNote } from "./ui.jsx";

export default function RenameSwarm({ name, onDone }) {
  const [open, setOpen] = useState(false);
  const [next, setNext] = useState(name);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const id = `rename-${name}`;

  function close() {
    setOpen(false);
    setConfirming(false);
    setNext(name);
    setError(null);
  }

  async function rename() {
    setBusy(true);
    setError(null);
    try {
      await api(`/admin/swarms/${encodeURIComponent(name)}`, { method: "PATCH", body: { name: next.trim() } });
      close();
      onDone?.();
    } catch (err) {
      setError(err);
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return <button type="button" className="btn py-0.5 text-sm" onClick={() => setOpen(true)}>Rename</button>;
  }
  return (
    <form className="mt-2 flex flex-wrap items-center gap-2 text-sm" aria-label={`Rename ${name}`}
          onSubmit={(e) => { e.preventDefault(); if (confirming) rename(); else setConfirming(true); }}>
      <label htmlFor={id} className="text-paper-muted">New name</label>
      <input id={id} className="field w-44" value={next} required
             onChange={(e) => { setNext(e.target.value); setConfirming(false); }} />
      <HelpTip id={`${id}-help`} label="About renaming a swarm">{SWARM_RENAME}</HelpTip>
      {confirming && (
        <p className="w-full text-warn" role="alert">
          {`Rename ${name} to ${next.trim()}? `}{SWARM_RENAME}
        </p>
      )}
      <button className="btn btn-primary" disabled={busy || !next.trim() || next.trim() === name}>
        {confirming ? "Yes, rename" : "Rename"}
      </button>
      <button type="button" className="btn" onClick={close}>Cancel</button>
      <ErrorNote error={error} />
    </form>
  );
}
