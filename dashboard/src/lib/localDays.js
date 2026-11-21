// Calendar days in a time zone, as UTC instants (#394). Pure functions; the
// zone is a parameter so tests don't depend on the machine's zone.

export function browserTimeZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

const FORMATTERS = new Map();
function formatter(timeZone) {
  if (!FORMATTERS.has(timeZone)) {
    FORMATTERS.set(timeZone, new Intl.DateTimeFormat("en-US", {
      timeZone, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    }));
  }
  return FORMATTERS.get(timeZone);
}

function wallClock(ms, timeZone) {
  const p = Object.fromEntries(formatter(timeZone).formatToParts(new Date(ms)).map((x) => [x.type, x.value]));
  return { year: +p.year, month: +p.month, day: +p.day, hour: +p.hour, minute: +p.minute, second: +p.second };
}

// The zone's offset from UTC at instant `ms`, in ms (New York in winter: -5 h).
export function zoneOffsetMs(ms, timeZone) {
  const w = wallClock(ms, timeZone);
  const asUtc = Date.UTC(w.year, w.month - 1, w.day, w.hour, w.minute, w.second);
  return asUtc - (ms - (((ms % 1000) + 1000) % 1000));
}

// The calendar date (YYYY-MM-DD) at instant `ms` in the zone.
export function localDate(ms, timeZone) {
  const w = wallClock(ms, timeZone);
  return `${w.year}-${String(w.month).padStart(2, "0")}-${String(w.day).padStart(2, "0")}`;
}

// YYYY-MM-DD shifted by `days` (plain calendar arithmetic).
export function addDays(date, days) {
  const [y, m, d] = date.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}

// The UTC instant (ms) of local midnight starting `date` in the zone. Two
// passes, so a day that starts or ends a DST change still comes out right.
export function dayStartUtc(date, timeZone) {
  const [y, m, d] = date.split("-").map(Number);
  const wall = Date.UTC(y, m - 1, d);
  let t = wall - zoneOffsetMs(wall, timeZone);
  t = wall - zoneOffsetMs(t, timeZone);
  return t;
}

// { since, until } ISO strings covering local days `from`..`to` (either may be blank).
export function localDayRange(from, to, timeZone) {
  const out = {};
  if (from) out.since = new Date(dayStartUtc(from, timeZone)).toISOString();
  if (to) out.until = new Date(dayStartUtc(addDays(to, 1), timeZone) - 1).toISOString();
  return out;
}
