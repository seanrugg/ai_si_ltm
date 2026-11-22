// Where a record or trace page was opened from (#393), so its breadcrumb can
// lead back there with the same filters. Pure functions; tested in origin.test.js.
//
// Links to /record/... and /decision-trace/... carry `?from=<path?query>`, the
// page they were clicked on. On a record or trace page itself, links keep the
// original `from`, so Timeline → Record → Trace still leads back to Timeline.
import { GROUPS, OVERVIEW } from "./nav.js";
import { DEFAULT_TAB, tabOf, tabsFor } from "./profile.js";

export const FROM = "from";

const RECORD_PAGE = /^\/(record|decision-trace)\//;

export function isRecordPage(pathname) {
  return RECORD_PAGE.test(pathname || "");
}

/** A `from` value we are willing to link to: an in-app path, not a record page. Else null. */
export function safeOrigin(from) {
  if (typeof from !== "string" || !from.startsWith("/") || from.startsWith("//") || from.includes("\\")) return null;
  if ([...from].some((c) => c.charCodeAt(0) < 0x20) || from.length > 2000) return null;
  const [pathname, query = ""] = from.split("?");
  if (isRecordPage(pathname)) return null;
  if (pathname === "/" && !query) return null; // the bare root only redirects to Overview
  return from;
}

/** The origin a link clicked on this page should carry. */
export function originOf(pathname, search) {
  if (isRecordPage(pathname)) return safeOrigin(new URLSearchParams(search).get(FROM));
  return safeOrigin(`${pathname}${search || ""}`);
}

/** `to` with `?from=origin` added (or `to` unchanged when there is no origin). */
export function withOrigin(to, origin) {
  if (!origin) return to;
  const [path, query = ""] = to.split("?");
  const params = new URLSearchParams(query);
  params.set(FROM, origin);
  return `${path}?${params.toString()}`;
}

export function recordPath(agent, seq) {
  return `/record/${encodeURIComponent(agent)}/${seq}`;
}

export function tracePath(agent, seq) {
  return `/decision-trace/${encodeURIComponent(agent)}/${seq}`;
}

export function timelinePath(agent, focusSeq) {
  const q = new URLSearchParams({ agent });
  if (focusSeq !== undefined && focusSeq !== null) q.set("focus", String(focusSeq));
  return `/timeline?${q.toString()}`;
}

const NAV_LABELS = new Map([[OVERVIEW.to, OVERVIEW.label], ...GROUPS.flatMap((g) => g.items.map((i) => [i.to, i.label]))]);

/** Breadcrumb steps for an origin: [{label, to}]. Falls back to the member's Timeline. */
export function originCrumbs(from, agent) {
  const origin = safeOrigin(from);
  if (origin) {
    const [pathname, query = ""] = origin.split("?");
    const params = new URLSearchParams(query);
    if (pathname === "/timeline" || pathname === "/") {
      const who = params.get("agent");
      return [{ label: who ? `Timeline (${who})` : "Timeline", to: origin.startsWith("/?") ? `/timeline?${query}` : origin }];
    }
    const profile = pathname.match(/^\/(members|swarms)\/([^/]+)\/profile$/);
    if (profile) {
      const [, kind, encoded] = profile;
      const name = decodeURIComponent(encoded);
      const subject = kind === "members" ? "member" : "swarm";
      const key = tabOf(params.get("tab") || DEFAULT_TAB, subject);
      const tab = tabsFor(subject).find((t) => t.key === key);
      return [
        { label: kind === "members" ? "Members" : "Swarms", to: `/${kind}` },
        { label: name, to: `/${kind}/${encoded}/profile` },
        { label: tab.label, to: origin },
      ];
    }
    const label = NAV_LABELS.get(pathname);
    if (label) return [{ label, to: origin }];
  }
  return [{ label: `Timeline (${agent})`, to: `/timeline?agent=${encodeURIComponent(agent)}` }];
}

/** The whole breadcrumb for a record (`page: "record"`) or trace (`page: "trace"`) page. */
export function recordCrumbs({ agent, seq, from, page }) {
  const origin = safeOrigin(from);
  const crumbs = originCrumbs(origin, agent);
  if (page === "trace") {
    crumbs.push({ label: `Record #${seq}`, to: withOrigin(recordPath(agent, seq), origin) });
    crumbs.push({ label: "Trace" });
  } else {
    crumbs.push({ label: `Record #${seq}` });
  }
  return crumbs;
}
