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

// Files above this go up in resumable chunks (#245); the server's text limit.
export const CHUNKED_ABOVE = 25 * 1024 * 1024;

const itemsPath = (datasetId, version) =>
  `/admin/datasets/${encodeURIComponent(datasetId)}/versions/${version}/items`;

// Resumable upload: start, PUT each chunk with Content-Range, finish. A chunk
// refused with wrong_offset (the server already has more, or less) resumes
// from the server's received_bytes. onProgress(sent, total).
export async function uploadChunked(api, datasetId, version, file, onProgress = () => {}) {
  const base = `${itemsPath(datasetId, version)}/uploads`;
  const up = await api(base, { method: "POST", body: { filename: file.name, size: file.size } });
  const size = up.chunk_bytes;
  let sent = up.received_bytes || 0;
  let retries = 0;
  while (sent < file.size) {
    const end = Math.min(sent + size, file.size);
    try {
      const res = await api(`${base}/${up.upload_id}`, {
        method: "PUT", body: file.slice(sent, end),
        headers: { "Content-Range": `bytes ${sent}-${end - 1}/${file.size}` },
      });
      sent = res.received_bytes;
      retries = 0;
    } catch (err) {
      if (retries >= 3) throw err;
      retries += 1;
      sent = (await api(`${base}/${up.upload_id}`)).received_bytes; // resume where the server is
    }
    onProgress(sent, file.size);
  }
  const done = await api(`${base}/${up.upload_id}/finish`, { method: "POST" });
  return done.item;
}

// Upload files one per request, so each file gets its own result and one bad
// file doesn't stop the rest. Big files use the resumable path.
// Returns [{name, ok, key?, error?}].
export async function uploadFiles(api, datasetId, version, files, onProgress = () => {}) {
  const results = [];
  for (const file of files) {
    try {
      let key;
      if (file.size > CHUNKED_ABOVE) {
        key = (await uploadChunked(api, datasetId, version, file, (s, t) => onProgress(file.name, s, t))).key;
      } else {
        const body = new FormData();
        body.append("files", file, file.name);
        const res = await api(itemsPath(datasetId, version), { method: "POST", body });
        const first = res?.items?.[0];
        key = first?.key ?? file.name;
        if (first?.archive) {
          results.push({ name: file.name, ok: true, key, archive: first.archive });
          continue;
        }
      }
      results.push({ name: file.name, ok: true, key });
    } catch (err) {
      results.push({ name: file.name, ok: false, error: err.message || String(err) });
    }
  }
  return results;
}
