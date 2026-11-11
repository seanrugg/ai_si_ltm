// Datasets (#242): the fleet's reference catalog. Admins add, upload, approve
// and retire; members read approved versions (logged, and sent to the LRS).
import { useState } from "react";
import HelpTip from "../components/HelpTip.jsx";
import { api } from "../api/client.js";
import { useApi } from "../api/useApi.js";
import { EmptyState, ErrorNote, Loading, PageHeader, StatusBadge } from "../components/ui.jsx";
import { DATASET_APPROVE, DATASET_LARGE, DATASET_SCOPE } from "../lib/help.js";
import { CHUNKED_ABOVE, FILTERS, filterDatasets, formatBytes, hasPending, sortDatasets, totalSize, uploadFiles } from "../lib/datasets.js";
import { localTime } from "../lib/format.js";

function UploadResults({ results }) {
  if (!results?.length) return null;
  return (
    <ul className="mt-2 text-sm" aria-label="Upload results">
      {results.map((r) => (
        <li key={r.name} className={r.ok ? "text-ok" : "text-bad"}>
          {!r.ok ? `${r.name}: ${r.error}` : r.archive
            ? `Unpacked ${r.name}: ${r.archive.stored.length} stored, ${r.archive.skipped.length} skipped, `
              + `${r.archive.errors.length} errors`
            : `Stored ${r.key}`}
          {r.archive && (r.archive.skipped.length > 0 || r.archive.errors.length > 0) && (
            <ul className="ml-4 list-disc text-paper-muted">
              {r.archive.skipped.map((x) => <li key={`s-${x.key}`}>{x.key}: {x.reason}</li>)}
              {r.archive.errors.map((x) => <li key={`e-${x.key}`}>{x.key}: {x.error}</li>)}
            </ul>
          )}
        </li>
      ))}
    </ul>
  );
}

function FilePicker({ id, datasetId, version, onDone }) {
  const [files, setFiles] = useState([]);
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState(null);
  const [progress, setProgress] = useState(null);

  async function upload() {
    setBusy(true);
    setResults(await uploadFiles(api, datasetId, version, files,
      (name, sent, total) => setProgress({ name, pct: Math.floor((sent / total) * 100) })));
    setProgress(null);
    setBusy(false);
    setFiles([]);
    onDone?.();
  }

  return (
    <div className="mt-3">
      <label htmlFor={id} className="block text-sm text-paper-muted">Files for version {version}</label>
      <input id={id} type="file" multiple className="field"
             onChange={(e) => setFiles(Array.from(e.target.files || []))} />
      <p className="mt-1 text-xs text-warn">{DATASET_LARGE}</p>
      {files.some((f) => f.size > CHUNKED_ABOVE) && (
        <p className="mt-1 text-xs text-paper-muted">
          Files over 25 MB upload in 8 MB chunks and resume if the connection drops. They&apos;re stored whole
          and served as files, without extracted text.
        </p>
      )}
      <button type="button" className="btn btn-primary mt-2" disabled={busy || files.length === 0} onClick={upload}>
        {busy ? "Uploading…" : files.length ? `Upload ${files.length} file${files.length === 1 ? "" : "s"}` : "Upload"}
      </button>
      {progress && (
        <p className="mt-2 text-sm" role="status">
          {progress.name}: <progress max="100" value={progress.pct} aria-label={`Uploading ${progress.name}`} /> {progress.pct}%
        </p>
      )}
      <UploadResults results={results} />
    </div>
  );
}

export function AddDataset({ onCreated }) {
  const [form, setForm] = useState({ name: "", description: "", licence: "", kind: "upload" });
  const [created, setCreated] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const ds = await api("/admin/datasets", { method: "POST", body: {
        name: form.name.trim(), description: form.description.trim() || null,
        licence: form.licence.trim(), kind: form.kind,
      } });
      setCreated(ds);
      onCreated?.();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  if (created) {
    return (
      <section className="mb-6 rounded-md border border-ink-line bg-ink p-4" aria-label="Upload files">
        <h2 className="text-lg font-bold">Upload into {created.name}</h2>
        <FilePicker id="new-dataset-files" datasetId={created.id} version={1} onDone={onCreated} />
        <button type="button" className="btn mt-3" onClick={() => setCreated(null)}>Done</button>
      </section>
    );
  }

  return (
    <section className="mb-6 rounded-md border border-ink-line bg-ink p-4" aria-labelledby="add-dataset-h">
      <h2 id="add-dataset-h" className="mb-3 text-lg font-bold">Add dataset</h2>
      <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); create(); }}
            aria-label="Add dataset">
        <div>
          <label htmlFor="ds-name" className="block text-sm text-paper-muted">Name</label>
          <input id="ds-name" className="field w-48" value={form.name} onChange={set("name")} required />
        </div>
        <div>
          <label htmlFor="ds-desc" className="block text-sm text-paper-muted">Description</label>
          <input id="ds-desc" className="field w-64" value={form.description} onChange={set("description")} />
        </div>
        <div>
          <label htmlFor="ds-licence" className="block text-sm text-paper-muted">Licence</label>
          <input id="ds-licence" className="field w-48" value={form.licence} onChange={set("licence")} required
                 placeholder="e.g. CC BY 4.0, Internal" />
        </div>
        <div>
          <label htmlFor="ds-kind" className="block text-sm text-paper-muted">Kind</label>
          <select id="ds-kind" className="field" value={form.kind} onChange={set("kind")}>
            <option value="upload">Upload</option>
          </select>
        </div>
        <p className="w-full text-xs text-paper-muted">
          Scope: fleet <HelpTip id="ds-scope" label="About dataset scope">{DATASET_SCOPE}</HelpTip>
        </p>
        <button type="submit" className="btn btn-primary"
                disabled={busy || !form.name.trim() || !form.licence.trim()}>Add dataset</button>
      </form>
      <ErrorNote error={error} />
    </section>
  );
}

function VersionItems({ datasetId, version }) {
  const { data, error, loading } = useApi(
    `/admin/datasets/${encodeURIComponent(datasetId)}/versions/${version}/items`);
  if (loading && !data) return <Loading what="Loading items" />;
  if (error) return <ErrorNote error={error} />;
  const items = data?.items || [];
  if (!items.length) return <p className="text-sm text-paper-muted">No items yet.</p>;
  return (
    <table className="mt-2 w-full text-sm">
      <thead><tr className="text-left text-paper-muted"><th>Key</th><th>Type</th><th>Size</th></tr></thead>
      <tbody>
        {items.map((it) => (
          <tr key={it.key}><td className="font-mono">{it.key}</td><td>{it.media_type}</td>
            <td>{formatBytes(it.size_bytes)}</td></tr>
        ))}
      </tbody>
    </table>
  );
}

export function DatasetDetail({ dataset, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [retiring, setRetiring] = useState(false);
  const [confirm, setConfirm] = useState("");
  const [shown, setShown] = useState(null);
  const id = encodeURIComponent(dataset.id);
  const retired = dataset.status === "retired";

  async function act(path) {
    setBusy(true);
    setError(null);
    try {
      await api(path, { method: "POST" });
      onChanged?.();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const versions = [...(dataset.versions || [])].reverse();
  const pending = versions.find((v) => v.status === "pending");

  return (
    <div className="mt-3 border-t border-ink-line pt-3">
      {dataset.description && <p className="mb-2 text-sm">{dataset.description}</p>}
      <ul className="flex flex-col gap-3" aria-label={`Versions of ${dataset.name}`}>
        {versions.map((v) => (
          <li key={v.version}>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-bold">v{v.version}</span>
              <StatusBadge status={v.status} />
              <span>{v.item_count} items · {formatBytes(v.size_bytes)}</span>
              {v.approved_at && <span className="text-paper-muted">approved {localTime(v.approved_at)}</span>}
              <button type="button" className="btn" onClick={() => setShown(shown === v.version ? null : v.version)}>
                {shown === v.version ? "Hide items" : "Items"}
              </button>
              {v.status === "pending" && !retired && (
                <>
                  <button type="button" className="btn btn-primary" disabled={busy || v.item_count === 0}
                          onClick={() => act(`/admin/datasets/${id}/versions/${v.version}/approve`)}>
                    Approve version {v.version}
                  </button>
                  <HelpTip id={`ds-approve-${dataset.id}-${v.version}`} label="About approving">{DATASET_APPROVE}</HelpTip>
                </>
              )}
            </div>
            {shown === v.version && <VersionItems datasetId={dataset.id} version={v.version} />}
            {v.status === "pending" && !retired && (
              <FilePicker id={`ds-files-${dataset.id}-${v.version}`} datasetId={dataset.id} version={v.version}
                          onDone={onChanged} />
            )}
          </li>
        ))}
      </ul>
      {!retired && (
        <div className="mt-3 flex flex-wrap items-end gap-2">
          {!pending && (
            <button type="button" className="btn" disabled={busy}
                    onClick={() => act(`/admin/datasets/${id}/versions`)}>New version</button>
          )}
          {!retiring ? (
            <button type="button" className="btn" disabled={busy} onClick={() => setRetiring(true)}>Retire</button>
          ) : (
            <div className="flex flex-wrap items-end gap-2" role="group" aria-label="Confirm retire">
              <p className="w-full text-sm text-warn">
                Members can no longer read a retired dataset. Its read log and the lessons that cite it are kept.
              </p>
              <div>
                <label htmlFor={`retire-${dataset.id}`} className="block text-sm text-paper-muted">
                  Confirm by typing {dataset.name}
                </label>
                <input id={`retire-${dataset.id}`} className="field" value={confirm}
                       onChange={(e) => setConfirm(e.target.value)} />
              </div>
              <button type="button" className="btn btn-danger" disabled={busy || confirm !== dataset.name}
                      onClick={() => act(`/admin/datasets/${id}/retire`)}>Retire {dataset.name}</button>
              <button type="button" className="btn" onClick={() => { setRetiring(false); setConfirm(""); }}>Cancel</button>
            </div>
          )}
        </div>
      )}
      <ErrorNote error={error} />
    </div>
  );
}

export default function Datasets() {
  const { data, error, loading, reload } = useApi("/admin/datasets");
  const [filter, setFilter] = useState("all");
  const [open, setOpen] = useState(null);

  if (loading && !data) return <Loading what="Loading datasets" />;
  if (error) return <ErrorNote error={error} onRetry={reload} />;
  const all = sortDatasets(data?.datasets || []);
  const list = filterDatasets(all, filter);

  return (
    <>
      <PageHeader title="Datasets">
        <div role="group" aria-label="Filter datasets" className="flex gap-1">
          {FILTERS.map((f) => (
            <button key={f} type="button" className={`btn ${filter === f ? "btn-primary" : ""}`}
                    aria-pressed={filter === f} onClick={() => setFilter(f)}>
              {f[0].toUpperCase() + f.slice(1)}
            </button>
          ))}
        </div>
      </PageHeader>
      <p className="mb-4 text-sm text-paper-muted">
        Reference material for the whole fleet. Members read approved versions; every read is logged and sent
        to the LRS. LTMS doesn&apos;t train on it.
      </p>
      <AddDataset onCreated={reload} />
      {list.length === 0 ? (
        <EmptyState title={all.length ? "No datasets match this filter" : "No datasets yet"}>
          Add a dataset, upload its files, then approve the version so members can read it.
        </EmptyState>
      ) : (
        <ul className="panel" aria-label="Datasets">
          {list.map((ds) => (
            <li key={ds.id} className="border-b border-ink-line p-3 last:border-b-0">
              <div className="flex flex-wrap items-center gap-3">
                <button type="button" className="font-bold underline-offset-2 hover:underline"
                        aria-expanded={open === ds.id} onClick={() => setOpen(open === ds.id ? null : ds.id)}>
                  {ds.name}
                </button>
                <StatusBadge status={ds.status === "retired" ? "retired" : hasPending(ds) ? "pending" : "approved"} />
                <span className="text-sm">
                  {ds.current_version != null ? `v${ds.current_version}` : "no approved version"}
                </span>
                <span className="text-sm text-paper-muted">
                  {(ds.versions || []).find((v) => v.version === ds.current_version)?.item_count ?? 0} items ·{" "}
                  {formatBytes(totalSize(ds))} · {ds.licence}
                </span>
              </div>
              {open === ds.id && <DatasetDetail dataset={ds} onChanged={reload} />}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
