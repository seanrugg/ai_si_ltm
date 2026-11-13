// Admin ping (#356): what state a ping is in, in words.

const secs = (a, b) => Math.max(0, Math.round((new Date(b) - new Date(a)) / 1000));

function span(s) {
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${Math.round(s / 3600)} h`;
}

/** {state, text} for a ping status from GET /admin/members/{name}/ping. */
export function pingState(p, now = new Date().toISOString()) {
  if (!p?.requested_at) return { state: "none", text: "Not pinged." };
  if (p.answered_at) {
    return { state: "answered", text: `Answered in ${span(secs(p.requested_at, p.answered_at))}: ${p.answer?.summary ?? "a record"}.` };
  }
  const waited = span(secs(p.requested_at, now));
  if (p.delivered_at) return { state: "delivered", text: `Delivered ${span(secs(p.requested_at, p.delivered_at))} after the request; no answer yet (${waited}).` };
  return { state: "waiting", text: `Waiting for the member or its hub to pick it up (${waited}).` };
}
