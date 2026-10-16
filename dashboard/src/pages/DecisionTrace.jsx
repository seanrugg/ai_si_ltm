import { Link, useParams } from "react-router-dom";
import { useApi } from "../api/useApi.js";
import RecordRow, { RecordDetail } from "../components/RecordRow.jsx";
import { ErrorNote, Loading, PageHeader } from "../components/ui.jsx";

function Section({ title, records, empty }) {
  return (
    <section className="mb-6" aria-label={title}>
      <h2 className="mb-2 text-sm font-bold text-paper-muted">{title}</h2>
      {records.length ? (
        <ul className="panel">{records.map((r) => <RecordRow key={r.hash} record={r} />)}</ul>
      ) : (
        <p className="text-sm text-paper-faint">{empty}</p>
      )}
    </section>
  );
}

export default function DecisionTrace() {
  const { agent, seq } = useParams();
  const { data, error, loading, reload } = useApi("/admin/decision-trace", { agent, seq });

  return (
    <>
      <PageHeader title={`Trace: ${agent} #${seq}`}>
        <Link className="btn" to={`/record/${agent}/${seq}`}>Open record</Link>
      </PageHeader>
      {loading && !data && <Loading what="Following the references" />}
      <ErrorNote error={error} onRetry={reload} />
      {data && (
        <>
          <Section title="Based on" records={data.inputs} empty="This record doesn't reference anything." />
          <section className="mb-6" aria-label="This record">
            <h2 className="mb-2 text-sm font-bold text-paper-muted">This record</h2>
            <div className="panel border-led/40 p-4">
              <p className="mb-3">
                <span className="font-bold">{data.root.summary || "(no summary)"}</span>
                <span className="ml-2 text-sm text-paper-muted">{data.root.type}</span>
              </p>
              <RecordDetail record={data.root} />
            </div>
          </section>
          <Section title="Led to" records={data.children} empty="Nothing references this record yet." />
          <Section title="Then" records={data.grandchildren} empty="No follow-ups after that." />
        </>
      )}
    </>
  );
}
