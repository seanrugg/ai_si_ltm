import { useParams } from "react-router-dom";
import { useApi } from "../api/useApi.js";
import { RecordDetail, TombstoneNote } from "../components/RecordRow.jsx";
import { ErrorNote, Loading, PageHeader } from "../components/ui.jsx";
import { localTime } from "../lib/format.js";
import ProxiedBadge from "../components/ProxiedBadge.jsx";

export default function RecordPage() {
  const { agent, seq } = useParams();
  const { data, error, loading, reload } = useApi(`/admin/records/${encodeURIComponent(agent)}/${seq}`);
  const members = useApi("/admin/members");
  const author = (members.data?.members ?? []).find((m) => m.name === agent);
  return (
    <>
      <PageHeader title={`${agent} #${seq}`} />
      {loading && <Loading />}
      <ErrorNote error={error} onRetry={reload} />
      {data?.deleted && (
        <div className="panel p-4">
          <TombstoneNote tombstone={data} />
        </div>
      )}
      {data && !data.deleted && (
        <div className="panel p-4">
          <p className="mb-4 flex flex-wrap items-center gap-2 text-sm text-paper-muted">
            {data.type} at {localTime(data.ts)}
            <ProxiedBadge proxiedBy={author?.proxied_by} />
          </p>
          <RecordDetail record={data} />
        </div>
      )}
    </>
  );
}
