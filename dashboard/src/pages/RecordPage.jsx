import { Link, useParams } from "react-router-dom";
import { useApi } from "../api/useApi.js";
import { RecordDetail, TombstoneNote } from "../components/RecordRow.jsx";
import { ErrorNote, Loading, PageHeader } from "../components/ui.jsx";
import { localTime } from "../lib/format.js";
import ProxiedBadge from "../components/ProxiedBadge.jsx";
import { RecordBreadcrumb } from "../components/RecordLink.jsx";
import { timelinePath } from "../lib/origin.js";

export default function RecordPage() {
  const { agent, seq } = useParams();
  const { data, error, loading, reload } = useApi(`/admin/records/${encodeURIComponent(agent)}/${seq}`);
  const members = useApi("/admin/members");
  const author = (members.data?.members ?? []).find((m) => m.name === agent);
  return (
    <>
      <RecordBreadcrumb agent={agent} seq={seq} page="record" />
      <PageHeader title={`${agent} #${seq}`}>
        <Link className="btn" to={`/members/${encodeURIComponent(agent)}/profile`}>{agent}&apos;s profile</Link>
        <Link className="btn" to={timelinePath(agent, seq)}>View in timeline</Link>
      </PageHeader>
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
