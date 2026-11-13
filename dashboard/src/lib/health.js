// Machine health self-reports (#349): the hub's `jetson-health` readings.

/** Health reports, oldest first, from timeline records. */
export function healthReports(records) {
  return (records || [])
    .filter((r) => r?.payload?.sensor === "jetson-health" && (r.tags || []).includes("health"))
    .sort((a, b) => String(a.ts).localeCompare(String(b.ts)));
}

/** The hottest zone's max per report, for a sparkline. */
export function hottestSeries(reports) {
  return reports.map((r) => {
    const zones = Object.values(r.payload?.temp_c || {}).map((z) => z.max).filter((v) => typeof v === "number");
    return zones.length ? Math.max(...zones) : null;
  }).filter((v) => v != null);
}

/** Rows for the latest report: [label, value] with units, skipping what wasn't read. */
export function latestRows(report) {
  const p = report?.payload || {};
  const rows = [];
  const mml = (o, unit) => (o ? `${o.last}${unit} (window ${o.min}–${o.max}${unit})` : null);
  for (const [zone, t] of Object.entries(p.temp_c || {}).sort()) rows.push([`Temperature, ${zone}`, mml(t, " °C")]);
  if (p.cpu_pct) rows.push(["CPU", mml(p.cpu_pct, "%")]);
  if (p.gpu_load_pct) rows.push(["GPU load", mml(p.gpu_load_pct, "%")]);
  if (p.ram_used_mib) rows.push(["Memory used", `${mml(p.ram_used_mib, " MiB")}${p.ram_total_mib ? ` of ${p.ram_total_mib} MiB` : ""}`]);
  if (p.disk_used_pct) rows.push(["Disk used", mml(p.disk_used_pct, "%")]);
  if (p.power_mode) rows.push(["Power mode", p.power_mode]);
  if (p.oc_events && Object.keys(p.oc_events).length) {
    rows.push(["Over-current counters", Object.entries(p.oc_events).sort().map(([k, v]) => `${k} ${v}`).join(", ")]);
  }
  rows.push(["Throttled in this window", p.throttled ? "yes" : "no"]);
  for (const [name, state] of Object.entries(p.services || {}).sort()) rows.push([`Service ${name}`, state]);
  return rows.filter(([, v]) => v != null);
}
