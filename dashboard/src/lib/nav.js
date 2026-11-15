// Navigation groups, badges and the Get started checklist (#379).
// Pure helpers, so the Shell stays thin and the rules are testable.
import { NAV_ANALYZE, NAV_CONFIGURE, NAV_GOVERN, NAV_OPERATE } from "./help.js";

export const OVERVIEW = { to: "/activity", label: "Overview" };

// URLs never change; only labels and grouping do.
export const GROUPS = [
  {
    key: "operate", label: "Operate", help: NAV_OPERATE, collapsible: false,
    items: [
      { to: "/timeline", label: "Timeline" },
      { to: "/conversation", label: "Conversation" },
      { to: "/members", label: "Members", badge: "members_pending" },
      { to: "/swarms", label: "Swarms" },
      { to: "/directives", label: "Directives", badge: "directive_declines" },
    ],
  },
  {
    key: "analyze", label: "Analyze", help: NAV_ANALYZE, collapsible: false,
    items: [
      { to: "/volume", label: "Volume" },
      { to: "/outcomes", label: "Outcomes" },
      { to: "/lessons", label: "Lessons & policies" },
      { to: "/integrity", label: "Integrity" },
      { to: "/geography", label: "Geography" },
    ],
  },
  {
    key: "govern", label: "Govern", help: NAV_GOVERN, collapsible: true,
    items: [
      { to: "/charter", label: "Charter", badge: "charter_overdue" },
      { to: "/audit-log", label: "Audit log" },
      { to: "/retention", label: "Retention" },
      { to: "/export", label: "Export" },
    ],
  },
  {
    key: "configure", label: "Configure", help: NAV_CONFIGURE, collapsible: true,
    items: [
      { to: "/settings", label: "Settings" },
      { to: "/access", label: "MCP access" },
      { to: "/lrs", label: "LRS endpoints", badge: "lrs_failing" },
      { to: "/datasets", label: "Datasets" },
      { to: "/system", label: "System status" },
    ],
  },
];

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

// What each badge means. `warn` badges show "!" rather than a number.
export const BADGES = {
  members_pending: { warn: false, text: (n) => `${plural(n, "member", "members")} waiting for approval` },
  charter_overdue: { warn: false, text: (n) => `${plural(n, "member", "members")} overdue on the charter` },
  directive_declines: {
    warn: false, text: (n) => `${plural(n, "directive decline", "directive declines")} since you last looked`,
  },
  lrs_failing: { warn: true, text: (n) => `${plural(n, "LRS endpoint", "LRS endpoints")} failing or paused` },
};

/** The badge for one nav item, or null: {count, warn, text}. */
export function itemBadge(item, status) {
  const spec = item.badge && BADGES[item.badge];
  const count = Number(status?.[item.badge]) || 0;
  if (!spec || count <= 0) return null;
  return { count, warn: spec.warn, text: spec.text(count) };
}

/** A collapsed group's header badge: the total, or "!" if any item warns. */
export function groupBadge(group, status) {
  const badges = group.items.map((i) => itemBadge(i, status)).filter(Boolean);
  if (!badges.length) return null;
  return {
    count: badges.reduce((n, b) => n + b.count, 0),
    warn: badges.some((b) => b.warn),
    text: badges.map((b) => b.text).join("; "),
  };
}

const inItem = (pathname, to) => pathname === to || pathname.startsWith(`${to}/`);

/** The key of the group holding this route, or null (Overview, record pages). */
export function groupOf(pathname) {
  const g = GROUPS.find((x) => x.items.some((i) => inItem(pathname, i.to)));
  return g ? g.key : null;
}

// Get started checklist. Each item ticks itself from GET /admin/nav-status `setup`.
export const CHECKLIST = [
  { key: "charter_published", label: "Publish the charter", to: "/charter" },
  { key: "swarm_created", label: "Create a swarm", to: "/swarms" },
  { key: "hub_appointed", label: "Appoint a hub", to: "/swarms" },
  { key: "lrs_added", label: "Add an LRS endpoint", to: "/lrs", optional: true },
  { key: "member_approved", label: "Approve your first member", to: "/members" },
];

/** True once every required checklist item is done; null while unknown. */
export function setupDone(setup) {
  if (!setup) return null;
  return CHECKLIST.every((c) => c.optional || setup[c.key]);
}

// Browser memory. Every access is wrapped: storage can throw (private mode,
// blocked cookies) and the nav must still work.
const GROUPS_KEY = "ltms.nav.groups";
const DISMISS_KEY = "ltms.nav.getStartedDismissed";
const SEEN_KEY = "ltms.nav.directivesSeen";

function read(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // storage unavailable: the choice lasts for this visit only
  }
}

/** Open/closed choices the admin made by hand: {govern?: bool, configure?: bool}. */
export function loadGroupChoices() {
  try {
    const v = JSON.parse(read(GROUPS_KEY) || "{}");
    return v && typeof v === "object" && !Array.isArray(v) ? v : {};
  } catch {
    return {};
  }
}

export function saveGroupChoices(choices) {
  write(GROUPS_KEY, JSON.stringify(choices));
}

export const isChecklistDismissed = () => read(DISMISS_KEY) === "1";
export const dismissChecklist = () => write(DISMISS_KEY, "1");

export const getDirectivesSeen = () => read(SEEN_KEY) || undefined;
export const setDirectivesSeen = (iso) => write(SEEN_KEY, iso);

/**
 * Whether a collapsible group is open: a hand-made choice wins; otherwise open
 * while the fleet isn't set up yet, collapsed once it is (or while unknown).
 */
export function groupOpen(key, choices, done) {
  if (typeof choices[key] === "boolean") return choices[key];
  return done === false;
}
