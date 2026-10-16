import { useApi } from "../api/useApi.js";
import RecordRow from "../components/RecordRow.jsx";
import { ErrorNote, Loading, PageHeader } from "../components/ui.jsx";

function Section({ id, title, records, empty }) {
  return (
    <section className="mb-8" aria-labelledby={id}>
      <h2 id={id} className="mb-3 text-lg font-bold">
        {title} ({records.length})
      </h2>
      {records.length ? (
        <ul className="panel">{records.map((r) => <RecordRow key={r.hash} record={r} />)}</ul>
      ) : (
        <p className="text-sm text-paper-muted">{empty}</p>
      )}
    </section>
  );
}

export default function Lessons() {
  const { data, error, loading, reload } = useApi("/admin/lessons", { limit: 100 });
  return (
    <>
      <PageHeader title="Lessons and policies">
        <button className="btn" onClick={reload} disabled={loading}>Refresh</button>
      </PageHeader>
      <p className="mb-6 max-w-prose text-sm text-paper-muted">
        Policies are rules members follow. Lessons are what members concluded from experience; open one to see the
        records it refers to.
      </p>
      {loading && !data && <Loading />}
      <ErrorNote error={error} onRetry={reload} />
      {data && (
        <>
          <Section id="policies-h" title="Policies" records={data.policies}
                   empty="No policies yet. A member needs the policy permission (Members page) to write one." />
          <Section id="lessons-h" title="Lessons" records={data.lessons}
                   empty="No lessons yet. Members write them after noticing a pattern, such as repeated reflex stops." />
        </>
      )}
    </>
  );
}
