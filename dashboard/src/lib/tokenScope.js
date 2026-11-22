// The New token form's scope, preview and expiry (#446). Pure functions; the
// form sends exactly the scope `tokenScope` builds, and the preview reads that
// same object, so what the admin reads is what the token gets.

export const RECORD_TYPES = [
  "action", "communication", "decision", "event", "lesson",
  "perception", "policy", "system", "thought",
];

// The free-agents choice in the Swarms picker; it is sent as `null`.
export const FREE_AGENTS = "\u0000free-agents";

export const EXPIRY_PICKS = [
  { key: "1d", label: "1 day", days: 1 },
  { key: "7d", label: "7 days", days: 7 },
  { key: "30d", label: "30 days", days: 30 },
  { key: "never", label: "Never", days: null },
];

// Ticking Include payloads with no expiry set gives the token this many days.
export const PAYLOAD_DEFAULT_DAYS = 7;

export function emptyTokenForm() {
  return { name: "", owner: "", purpose: "", types: [], agents: [], swarms: [], payloads: false, expires: "" };
}

/** The scope sent to POST /admin/mcp/tokens: types in their usual order; empty pickers mean "all". */
export function tokenScope(form) {
  const scope = { types: RECORD_TYPES.filter((t) => form.types.includes(t)) };
  if (form.agents.length) scope.agents = [...form.agents];
  if (form.swarms.length) scope.swarms = form.swarms.map((s) => (s === FREE_AGENTS ? null : s));
  if (form.payloads) scope.payloads = true;
  return scope;
}

/** The whole request body, as before #446: {name, scope, expires_at, owner, purpose}. */
export function tokenBody(form) {
  return {
    name: form.name.trim(),
    scope: tokenScope(form),
    expires_at: form.expires || null,
    owner: form.owner.trim() || null,
    purpose: form.purpose.trim() || null,
  };
}

/** Why the form can't be sent yet, or null. `types` may be the picker's list or comma-separated text. */
export function tokenFormProblem(form) {
  const types = Array.isArray(form.types) ? form.types
    : String(form.types ?? "").split(",").map((x) => x.trim()).filter(Boolean);
  if (!form.name.trim()) return "Give the token a name.";
  if (!types.length) return "Choose at least one record type: a token with none can read nothing.";
  return null;
}

/** YYYY-MM-DD, `days` after `now`, in the browser's calendar. */
export function dateInDays(days, now = new Date()) {
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** The form after Include payloads is ticked or cleared: ticking sets a 7-day expiry if none is set. */
export function withPayloads(form, on, now = new Date()) {
  const next = { ...form, payloads: on };
  if (on && !form.expires) next.expires = dateInDays(PAYLOAD_DEFAULT_DAYS, now);
  return next;
}

function listText(items, conj = "and") {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} ${conj} ${items[items.length - 1]}`;
}

function dayText(date) {
  const [y, m, d] = date.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

/** "Reads perception records of v3-c7r, with payloads, until 28 Nov 2026." from the scope sent. */
export function previewText(scope, expires) {
  const types = scope.types || [];
  if (!types.length) return "Reads nothing yet: choose at least one record type.";
  const what = types.length === RECORD_TYPES.length ? "records of every type" : `${listText(types)} records`;
  const who = scope.agents?.length ? `of ${listText(scope.agents)}` : "of every member";
  let where = "";
  if (scope.swarms?.length) {
    const names = scope.swarms.map((s) => (s === null ? "free agents" : s));
    where = ` in ${listText(names, "or")}`;
  }
  const payloads = scope.payloads ? "with payloads" : "headers only";
  const until = expires ? `until ${dayText(expires)}` : "with no expiry";
  return `Reads ${what} ${who}${where}, ${payloads}, ${until}.`;
}
