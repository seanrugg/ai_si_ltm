// Ask a member for a heartbeat (#356), and show whether it answered.
import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import HelpTip from "./HelpTip.jsx";
import { PING_ABOUT } from "../lib/help.js";
import { pingState } from "../lib/ping.js";

const POLL_MS = 5000;
const STOP_AFTER_MS = 10 * 60 * 1000;

export default function PingButton({ name }) {
  const [status, setStatus] = useState(null);
  const [polling, setPolling] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let live = true;
    api(`/admin/members/${encodeURIComponent(name)}/ping`).then((s) => { if (live) setStatus(s); }).catch(() => {});
    return () => { live = false; };
  }, [name]);

  useEffect(() => {
    if (!polling) return undefined;
    const started = Date.now();
    const id = setInterval(async () => {
      try {
        const s = await api(`/admin/members/${encodeURIComponent(name)}/ping`);
        setStatus(s);
        if (s.answered_at || Date.now() - started > STOP_AFTER_MS) setPolling(false);
      } catch {
        setPolling(false);
      }
    }, POLL_MS);
    return () => clearInterval(id);
  }, [polling, name]);

  async function ping() {
    setError(null);
    try {
      setStatus(await api(`/admin/members/${encodeURIComponent(name)}/ping`, { method: "POST" }));
      setPolling(true);
    } catch (err) {
      setError(err);
    }
  }

  const st = pingState(status);
  return (
    <span className="flex flex-wrap items-center gap-2 text-sm">
      <button type="button" className="btn" onClick={ping} disabled={polling}>
        {polling ? "Pinging…" : "Ping"}
      </button>
      <HelpTip id={`ping-${name}`} label="About ping">{PING_ABOUT}</HelpTip>
      {st.state !== "none" && <span role="status" className="text-paper-muted">{st.text}</span>}
      {error && <span role="alert" className="text-warn">{error.message}</span>}
    </span>
  );
}
