import { useParams } from "react-router-dom";
import { useApi } from "../api/useApi.js";
import { RecordDetail } from "../components/RecordRow.jsx";
import { ErrorNote, Loading, PageHeader } from "../components/ui.jsx";
import { localTime } from "../lib/format.js";

export default function RecordPage() {
  const { agent, seq } = useParams();
  const { data, error, loading, reload } = useApi(`/admin/records/${encodeURIComponent(agent)}/${seq}`);
  return (
    <>
      <PageHeader title={`${agent} #${seq}`} />
      {loading && <Loading />}
      <ErrorNote error={error} onRetry={reload} />
      {data && (
        <div className="panel p-4">
          <p className="mb-4 text-sm text-paper-muted">
            {data.type} at {localTime(data.ts)}
          </p>
          <RecordDetail record={data} />
        </div>
      )}
    </>
  );
}
