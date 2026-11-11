// Remove a swarm (issue #234): choose what happens to its members and its
// records, see what it affects, and type the name to confirm.
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import { SWARM_REMOVE, SWARM_REMOVE_MEMBERS, SWARM_REMOVE_RECORDS } from "../lib/help.js";
import { plural } from "../lib/format.js";
import HelpTip from "./HelpTip.jsx";
import { ErrorNote } from "./ui.jsx";

export default function RemoveSwarmDialog({ name, onClose, onRemoved }) {
  const ref = useRef(null);
  const [preview, setPreview] = useState(null);
  const [members, setMembers] = useState("free");
  const [records, setRecords] = useState("keep");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const base = `/admin/swarms/${encodeURIComponent(name)}`;

  useEffect(() => {
    let live = true;
    api(`${base}/remove-preview`).then((p) => { if (live) setPreview(p); }).catch((e) => { if (live) setError(e); });
    return () => { live = false; };
  }, [base]);

  useEffect(() => {
    const node = ref.current;
    const opener = document.activeElement;
    node?.querySelector("[data-autofocus]")?.focus();
    function onKey(e) {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key !== "Tab" || !node) return;
      const items = [...node.querySelectorAll("button:not([disabled]), input:not([disabled])")];
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      opener?.focus?.();
    };
  }, [onClose]);

  async function remove(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api(`${base}/remove`, { method: "POST", body: { confirm, members, records } });
      onRemoved?.();
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/80 p-4">
      <form ref={ref} role="dialog" aria-modal="true" aria-labelledby="remove-swarm-h" onSubmit={remove}
            className="panel w-full max-w-lg p-5 text-sm">
        <h2 id="remove-swarm-h" className="mb-2 flex items-center gap-2 text-lg font-bold" tabIndex={-1} data-autofocus>
          Remove {name}
          <HelpTip id="remove-swarm-help" label="About removing a swarm">{SWARM_REMOVE}</HelpTip>
        </h2>
        <p className="mb-3" aria-live="polite">
          {preview
            ? `This affects ${plural(preview.members, "member")} and ${plural(preview.records, "record")}.`
            : "Counting members and records…"}
        </p>

        <fieldset className="mb-3">
          <legend className="mb-1 flex items-center gap-2 font-bold">
            Members
            <HelpTip id="remove-swarm-members-help" label="About the member options">{SWARM_REMOVE_MEMBERS}</HelpTip>
          </legend>
          <label className="flex items-center gap-2"><input type="radio" name="members" value="free"
            checked={members === "free"} onChange={() => setMembers("free")} className="accent-led" />
            Make them free agents</label>
          <label className="flex items-center gap-2"><input type="radio" name="members" value="remove"
            checked={members === "remove"} onChange={() => setMembers("remove")} className="accent-led" />
            Remove them from LTMS</label>
          {members === "remove" && (
            <p className="mt-1 text-bad">They are revoked and can&apos;t send records until re-enrolled.</p>
          )}
        </fieldset>

        <fieldset className="mb-3">
          <legend className="mb-1 flex items-center gap-2 font-bold">
            Records
            <HelpTip id="remove-swarm-records-help" label="About the record options">{SWARM_REMOVE_RECORDS}</HelpTip>
          </legend>
          <label className="flex items-center gap-2"><input type="radio" name="records" value="keep"
            checked={records === "keep"} onChange={() => setRecords("keep")} className="accent-led" />
            Keep them</label>
          <label className="flex items-center gap-2"><input type="radio" name="records" value="purge"
            checked={records === "purge"} onChange={() => setRecords("purge")} className="accent-led" />
            Purge records that came through this swarm</label>
          {records === "purge" && (
            <p className="mt-1 text-bad">Deleted records leave tombstones. This can&apos;t be undone.</p>
          )}
        </fieldset>

        <label htmlFor="remove-swarm-confirm" className="mb-1 block">Type <strong>{name}</strong> to confirm</label>
        <input id="remove-swarm-confirm" className="field mb-3 w-48" value={confirm} autoComplete="off"
               onChange={(e) => setConfirm(e.target.value)} />
        <ErrorNote error={error} />
        <div className="mt-2 flex gap-2">
          <button className="btn btn-danger" disabled={busy || confirm !== name}>Remove</button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </div>
  );
}
