// Spec sheet helpers (#333).

export const HARDWARE_FIELDS = [
  ["board", "Board"], ["cpu", "CPU"], ["cores", "Cores"], ["ram_mib", "RAM (MiB)"], ["storage_gib", "Storage (GiB)"],
  ["accelerators", "Accelerators"], ["sensors", "Sensors"], ["actuators", "Actuators"], ["power", "Power"],
];
export const SOFTWARE_FIELDS = [["os", "Operating system"], ["harness", "Harness"], ["harness_version", "Harness version"]];
export const NUMERIC = new Set(["cores", "ram_mib", "storage_gib"]);
export const LISTS = new Set(["accelerators", "sensors", "actuators"]);

/** "set by admin" / "reported by device", for one field of a section. */
export function fieldSource(section, key) {
  if (!section?.value || section.value[key] === undefined) return null;
  return (section.admin_fields || []).includes(key) ? "set by admin" : "reported by device";
}

export function show(v) {
  if (v === undefined || v === null || v === "") return "—";
  return Array.isArray(v) ? (v.length ? v.join(", ") : "—") : String(v);
}

/** The PATCH body for one edited field: a value, or null to clear the override. */
export function patchFor(section, key, raw) {
  if (raw === null) return { [section]: { [key]: null } };
  let value = raw.trim();
  if (NUMERIC.has(key)) value = value === "" ? null : Number(value);
  else if (LISTS.has(key)) value = value.split(",").map((x) => x.trim()).filter(Boolean);
  return { [section]: { [key]: value } };
}

/** One line per member for the swarm roll-up: board, RAM, primary model, where its AI runs. */
export function rollupRow(name, specs) {
  const hw = specs?.hardware?.value || {};
  const models = specs?.models?.value || [];
  const primary = models[0];
  return {
    name,
    board: hw.board || "—",
    ram: hw.ram_mib ? `${(hw.ram_mib / 1024).toFixed(1)} GiB` : "—",
    model: primary ? [primary.name, primary.quant].filter(Boolean).join(" ") : "—",
    where: primary?.where || "—",
  };
}
