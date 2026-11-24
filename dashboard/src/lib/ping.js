// Admin ping (#356): what state a ping is in, in words, and (#384) how this
// member can answer: polling, on use, none or unknown.

const secs = (a, b) => Math.max(0, Math.round((new Date(b) - new Date(a)) / 1000));

function span(s) {
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${Math.round(s / 3600)} h`;
}

/** The answer mode from a ping status: "polling" | "on_use" | "none" | "unknown". */
export function modeOf(p) {
  const m = p?.mode?.mode;
  return m === "polling" || m === "on_use" || m === "none" ? m : "unknown";
}

/** Why ping is off for a `none` member, as visible text (508: not just a greyed-out button). */
export function offReason(p) {
  if (modeOf(p) !== "none") return null;
  return `Ping is off: this member can't answer pings (${p?.mode?.reason || "its spec sheet says so"}). Check its last activity instead.`;
}

const SOURCE = {
  declared: "The member says so in its spec sheet.",
  admin: "An admin set this on its spec sheet.",
};

/** Plain-language help for the ping tooltip: how this member can answer, and where that comes from. */
export function pingHelp(p) {
  const mode = modeOf(p);
  const source = p?.mode?.source;
  let text;
  if (mode === "polling") {
    text = "This member runs all the time and checks for pings itself or through its hub, about every 30 s, "
      + "so it usually answers within a minute or two. No answer after a few minutes means it's down or offline.";
  } else if (mode === "on_use") {
    text = "This member only runs when someone uses it, such as a chat assistant. It answers with its next record, "
      + "which may be hours away. No answer doesn't mean it's broken.";
  } else if (mode === "none") {
    text = `This member can't answer pings: ${p?.mode?.reason || "its spec sheet says so"}. Check its last activity instead.`;
  } else {
    text = "FLTMS doesn't know how this member checks in. It may answer within a minute, or only when it's next used. "
      + "Set how it answers under Specs, Software, Ping.";
  }
  if (source === "inferred") {
    text += mode === "polling" ? " Inferred from its regular heartbeats." : " Inferred: it's a chat assistant without heartbeats.";
  } else if (SOURCE[source]) {
    text += ` ${SOURCE[source]}`;
  }
  return text;
}

/** {state, text} for a ping status from GET /admin/members/{name}/ping. */
export function pingState(p, now = new Date().toISOString()) {
  if (!p?.requested_at) return { state: "none", text: "Not pinged." };
  if (p.answered_at) {
    return { state: "answered", text: `Answered in ${span(secs(p.requested_at, p.answered_at))}: ${p.answer?.summary ?? "a record"}.` };
  }
  const waited = span(secs(p.requested_at, now));
  const onUse = modeOf(p) === "on_use";
  if (p.delivered_at) {
    const after = span(secs(p.requested_at, p.delivered_at));
    if (p.delivered_via === "hub") {
      return { state: "delivered", text: `Passed to the hub ${after} after the request; the member answers with its next record (${waited}).` };
    }
    if (p.delivered_via === "member") {
      return {
        state: "delivered",
        text: onUse
          ? `Picked up by the member ${after} after the request; it answers with its next record, when next used (${waited}).`
          : `Picked up by the member ${after} after the request; no answer yet (${waited}).`,
      };
    }
    return { state: "delivered", text: `Delivered ${after} after the request; no answer yet (${waited}).` };
  }
  if (onUse) return { state: "waiting", text: `Waiting: answers when next used (${waited}).` };
  return { state: "waiting", text: `Waiting for the member or its hub to pick it up (${waited}).` };
}
