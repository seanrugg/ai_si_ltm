// Datasets page helpers (#242): ordering, filtering and the upload flow.

export const FILTERS = ["all", "pending", "approved"];

// A dataset needs attention when any version is waiting for approval.
export function hasPending(ds) {
  return ds.status === "pending" || (ds.versions || []).some((v) => v.status === "pending");
}

// Pending first, then by name; retired last.
export function sortDatasets(list) {
  const rank = (ds) => (ds.status === "retired" ? 2 : hasPending(ds) ? 0 : 1);
  return [...list].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
}

export function filterDatasets(list, filter) {
  if (filter === "pending") return list.filter(hasPending);
  if (filter === "approved") return list.filter((ds) => ds.current_version != null && ds.status !== "retired");
  return list;
}

export function latestVersion(ds) {
  const vs = ds.versions || [];
  return vs.length ? vs[vs.length - 1] : null;
}

export function totalSize(ds) {
  const v = (ds.versions || []).find((x) => x.version === ds.current_version) || latestVersion(ds);
  return v ? v.size_bytes : 0;
}

export function formatBytes(n) {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let x = n;
  while (x >= 1024 && i < units.length - 1) { x /= 1024; i += 1; }
  return `${x < 10 && i > 0 ? x.toFixed(1) : Math.round(x)} ${units[i]}`;
}

// Upload files one per request, so each file gets its own result and one bad
// file doesn't stop the rest. Returns [{name, ok, key?, error?}].
export async function uploadFiles(api, datasetId, version, files) {
  const results = [];
  for (const file of files) {
    const body = new FormData();
    body.append("files", file, file.name);
    try {
      const res = await api(`/admin/datasets/${encodeURIComponent(datasetId)}/versions/${version}/items`,
        { method: "POST", body });
      results.push({ name: file.name, ok: true, key: res?.items?.[0]?.key ?? file.name });
    } catch (err) {
      results.push({ name: file.name, ok: false, error: err.message || String(err) });
    }
  }
  return results;
}
