import { useEffect, useRef, useState } from "react";
import HelpTip from "../components/HelpTip.jsx";
import { ADD_SWARM, ROOT_KEY_FORMAT, ROOT_PRIVATE_KEY, ROOT_PUBLIC_KEY } from "../lib/help.js";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import { useApi } from "../api/useApi.js";
import { relativeTime } from "../lib/format.js";
import { swarmCount } from "../lib/profile.js";
import { generateRootKey } from "../lib/swarmKey.js";
import { EmptyState, ErrorNote, Loading, PageHeader } from "../components/ui.jsx";

// The one-time panel for a new root key. It traps focus and can't be dismissed
// until the admin confirms the key is saved; then the key string is dropped.
export function KeyOnceDialog({ swarmName, swarmId, keyString, onDone }) {
  const ref = useRef(null);
  const [saved, setSaved] = useState(false);
  const [copyStatus, setCopyStatus] = useState("");

  useEffect(() => {
    const node = ref.current;
    node?.querySelector("[data-autofocus]")?.focus();
    function onKey(e) {
      if (e.key === "Escape") {
        e.preventDefault(); // closing needs the confirmation
        return;
      }
      if (e.key !== "Tab" || !node) return;
      const items = [...node.querySelectorAll("button:not([disabled]), input:not([disabled]), [tabindex='0']")];
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
    function onUnload(e) {
      e.preventDefault();
      e.returnValue = ""; // the key can't be shown again
    }
    document.addEventListener("keydown", onKey);
    window.addEventListener("beforeunload", onUnload);
    return () => {
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("beforeunload", onUnload);
    };
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(keyString);
      setCopyStatus("Copied to the clipboard.");
    } catch {
      setCopyStatus("Copy didn't work in this browser. Select the key and copy it by hand.");
    }
  }

  function download() {
    const text = `${keyString}\n\nswarm: ${swarmName}\nswarm id: ${swarmId}\n` +
      "This is the swarm's root key. Keep it offline or in a password manager.\n";
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${swarmName}.swarm-key.txt`;
    a.click();
    URL.revokeObjectURL(url);
    setCopyStatus(`Downloaded ${swarmName}.swarm-key.txt.`);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/80 p-4">
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby="key-once-h" aria-describedby="key-once-d"
           className="panel w-full max-w-xl p-5">
        <h2 id="key-once-h" className="mb-2 text-lg font-bold" tabIndex={-1} data-autofocus>
          Root key for {swarmName}
        </h2>
        <p id="key-once-d" className="mb-3 text-sm text-paper-muted">
          This is the swarm&apos;s root key. LTMS does not keep it and cannot show it again. Store it in your
          password manager. Anyone with it can appoint this swarm&apos;s hub.
        </p>
        <div className="mb-1 flex items-center gap-2 text-sm font-bold">
          Private root key
          <HelpTip id="root-private-key" label="About the private root key">{ROOT_PRIVATE_KEY}</HelpTip>
        </div>
        <p className="mb-3 break-all rounded-md border border-led/50 bg-ink p-3 font-mono text-sm select-all"
           aria-label="Root key">
          {keyString}
        </p>
        <p className="mb-3 flex items-center gap-2 text-sm text-paper">
          <span>Save the whole line, including <span className="font-mono">ltms-swarm-key-v1:</span></span>
          <HelpTip id="root-key-format" label="What the parts of the key mean">{ROOT_KEY_FORMAT}</HelpTip>
        </p>
        <div className="mb-3 flex flex-wrap items-center gap-2 text-sm text-paper-muted">
          <span>Swarm ID <span className="break-all font-mono text-paper">{swarmId}</span></span>
          <HelpTip id="root-public-key" label="About the public root key">{ROOT_PUBLIC_KEY}</HelpTip>
        </div>
        <div className="mb-2 flex flex-wrap gap-2">
          <button type="button" className="btn btn-primary" onClick={copy}>Copy</button>
          <button type="button" className="btn" onClick={download}>Download (.txt)</button>
        </div>
        <p className="mb-3 min-h-5 text-sm text-ok" role="status" aria-live="polite">{copyStatus}</p>
        <p className="mb-2 text-sm text-warn">
          Copy this key now and store it offline. It is not shown again. Without it you can&apos;t appoint or replace
          this swarm&apos;s hub.
        </p>
        <p className="mb-2 text-sm text-paper-muted">
          Losing this key means revoking and re-creating the swarm. Keep it in a password manager, with a backup.
        </p>
        <label className="mb-3 flex items-center gap-2 text-sm cursor-pointer">
          <input type="checkbox" className="accent-led" checked={saved} onChange={(e) => setSaved(e.target.checked)} />
          I&apos;ve saved the key
        </label>
        <button type="button" className="btn" disabled={!saved} onClick={onDone}>Done</button>
      </div>
    </div>
  );
}

export function CreateSwarm({ onCreated, initialName = "" }) {
  const [mode, setMode] = useState("generate"); // "generate" | "existing"
  const [name, setName] = useState(initialName);
  const [publicKey, setPublicKey] = useState("");
  const [description, setDescription] = useState("");
  const [home, setHome] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [shown, setShown] = useState(null); // {swarmName, swarmId, keyString}, while the one-time panel is open

  function reset() {
    setName("");
    setPublicKey("");
    setDescription("");
    setHome(false);
  }

  async function register(pub) {
    return api("/admin/swarms", {
      method: "POST",
      body: { name: name.trim(), public_key: pub, description: description.trim() || null, home },
    });
  }

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "generate") {
        const key = generateRootKey();
        const keyString = key.keyString;
        key.seed.fill(0);
        const created = await register(key.publicKey);
        setShown({ swarmName: created.name, swarmId: created.id, keyString });
      } else {
        await register(publicKey.trim());
        onCreated();
      }
      reset();
    } catch (err) {
      setError(err); // a key generated for a failed registration is simply dropped
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mb-8" aria-labelledby="create-h">
      <h2 id="create-h" className="mb-3 text-lg font-bold">Create a swarm</h2>
      <p className="mb-3 text-sm text-paper-muted">
        {mode === "generate"
          ? "Your browser creates the swarm's root key and shows it to you once. Only its public key is sent to LTMS."
          : "Register a swarm whose root key you made with the command line. Only its public key is sent."}
      </p>
      <form onSubmit={submit} className="mb-2 flex flex-wrap items-end gap-2" aria-label="Create swarm"
            autoComplete="off">
        <input className="field w-40" value={name} onChange={(e) => setName(e.target.value)}
               placeholder="Swarm name (echo-base)" aria-label="Swarm name" required />
        {mode === "existing" && (
          <input className="field w-64" value={publicKey} onChange={(e) => setPublicKey(e.target.value)}
                 placeholder="Root public key, 64 hex" aria-label="Public key" required />
        )}
        <input className="field w-64" value={description} onChange={(e) => setDescription(e.target.value)}
               placeholder="Description" aria-label="Description" />
        <label className="flex items-center gap-2 text-sm text-paper-muted cursor-pointer">
          <input type="checkbox" checked={home} onChange={(e) => setHome(e.target.checked)} className="accent-led" />
          Make this the home swarm (only one active home at a time)
        </label>
        <button className="btn btn-primary" disabled={busy}>
          {mode === "generate" ? "Add swarm" : "Register"}
        </button>
        {mode === "generate" && <HelpTip id="add-swarm" label="About adding a swarm">{ADD_SWARM}</HelpTip>}
      </form>
      <button type="button" className="mb-2 text-sm text-paper-muted underline"
              onClick={() => setMode(mode === "generate" ? "existing" : "generate")}>
        {mode === "generate" ? "Already have a key? Register its public key" : "Generate a new key in the browser instead"}
      </button>
      <ErrorNote error={error} />
      {shown && (
        <KeyOnceDialog {...shown} onDone={() => { setShown(null); onCreated(); }} />
      )}
    </section>
  );
}

// Sign a hub appointment in the browser with a pasted root key. Only
// {statement, sig} is sent; the key input is cleared after submit or cancel.
const ACTIVE_FIRST = (a, b) => (a.status === "revoked") - (b.status === "revoked");

/** Rows for the Swarms table (#331), revoked last, then by the chosen column. */
export function sortSwarms(swarms, by = "name") {
  const last = (s) => s.metrics?.last_record_at || "";
  return [...(swarms || [])].sort((a, b) => ACTIVE_FIRST(a, b)
    || (by === "activity" ? last(b).localeCompare(last(a)) : a.name.localeCompare(b.name)));
}

function SwarmRow({ swarm }) {
  const m = swarm.metrics || {};
  const hub = (swarm.members || []).find((x) => x.role === "hub");
  const enc = encodeURIComponent(swarm.name);
  const revoked = swarm.status === "revoked";
  return (
    <tr className={`border-t border-ink-line ${revoked ? "text-paper-muted" : ""}`}>
      <th scope="row" className="px-3 py-2 text-left font-normal">
        <Link to={`/swarms/${enc}/profile`} className="font-bold hover:underline">{swarm.name}</Link>
        {swarm.home && <span className="ml-2 text-xs">(home)</span>}
        {revoked && <span className="ml-2 text-xs">(revoked)</span>}
      </th>
      <td className="px-3 py-2">
        {hub ? <>{hub.name}<span className="block text-xs text-paper-muted">
          {m.hub_last_seen_at ? `seen ${relativeTime(m.hub_last_seen_at)}` : "never seen"}</span></> : "none"}
      </td>
      <td className="px-3 py-2">{swarmCount(swarm.members)}</td>
      <td className="px-3 py-2">{(m.records_24h ?? 0).toLocaleString()} / {(m.records_7d ?? 0).toLocaleString()}</td>
      <td className="px-3 py-2">{m.last_record_at ? relativeTime(m.last_record_at) : "none yet"}</td>
      <td className="px-3 py-2">
        <span className="flex flex-wrap gap-2">
          <Link className="btn" to={`/swarms/${enc}/profile?tab=manage`} aria-label={`Manage ${swarm.name}`}>Manage</Link>
          <Link className="btn" to={`/?swarm=${enc}`} aria-label={`Timeline of ${swarm.name}`}>Timeline</Link>
          <Link className="btn" to={`/volume?swarm=${enc}`} aria-label={`Volume of ${swarm.name}`}>Volume</Link>
        </span>
      </td>
    </tr>
  );
}

export default function Swarms() {
  const { data, error, loading, reload } = useApi("/admin/swarms");
  const [sortBy, setSortBy] = useState("name");
  const swarms = sortSwarms(data || [], sortBy);

  if (loading && !data) return <Loading what="Loading swarms" />;
  if (error) return <ErrorNote error={error} onRetry={reload} />;

  const sortButton = (key, label) => (
    <button type="button" className="underline-offset-2 hover:underline" onClick={() => setSortBy(key)}
            aria-label={`Sort by ${label.toLowerCase()}`}>{label}</button>
  );
  return (
    <>
      <PageHeader title="Swarms" />
      {swarms.length === 0 && (
        <EmptyState title="No swarms yet">
          Create a swarm to start. Your browser generates its root key and shows it once; keep it offline. A
          hub is then appointed by signing with that key, on the swarm&apos;s Manage tab or with{" "}
          <code className="hash">python -m scripts.swarm_key appoint-hub</code>.
        </EmptyState>
      )}
      <CreateSwarm onCreated={reload} />
      {swarms.length > 0 && (
        <div className="panel overflow-x-auto">
          <table className="w-full text-sm">
            <caption className="px-3 py-2 text-left font-bold">Swarms</caption>
            <thead className="text-xs text-paper-muted">
              <tr>
                <th scope="col" className="px-3 py-2 text-left" aria-sort={sortBy === "name" ? "ascending" : "none"}>
                  {sortButton("name", "Swarm")}
                </th>
                <th scope="col" className="px-3 py-2 text-left">Hub</th>
                <th scope="col" className="px-3 py-2 text-left">Members</th>
                <th scope="col" className="px-3 py-2 text-left">Records 24 h / 7 d</th>
                <th scope="col" className="px-3 py-2 text-left" aria-sort={sortBy === "activity" ? "descending" : "none"}>
                  {sortButton("activity", "Last activity")}
                </th>
                <th scope="col" className="px-3 py-2 text-left">Actions</th>
              </tr>
            </thead>
            <tbody>{swarms.map((s) => <SwarmRow key={s.id} swarm={s} />)}</tbody>
          </table>
        </div>
      )}
    </>
  );
}
