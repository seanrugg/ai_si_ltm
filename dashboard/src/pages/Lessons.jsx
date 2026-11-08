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
        <ul className="panel">{records.map((r) => <RecordRow key={r.hash} record={r} status={r.status} />)}</ul>
      ) : (
        <p className="text-sm text-paper-muted">{empty}</p>
      )}
    </section>
  );
}

export default function Lessons() {
  const { data: lessonsData, error: lessonsError, loading: lessonsLoading, reload: reloadLessons } = useApi(
    "/admin/lessons", { limit: 100 }
  );
  const { data: policiesData, error: policiesError, loading: policiesLoading, reload: reloadPolicies } = useApi(
    "/admin/policies", { status: "all" }
  );

  const loading = lessonsLoading || policiesLoading;
  const error = lessonsError || policiesError;
  const reload = () => { reloadLessons(); reloadPolicies(); };

  const lessons = lessonsData?.lessons ?? [];
  const policies = policiesData?.policies ?? [];
  const proposals = policies.filter((p) => p.status === "proposed");
  const inForce = policies.filter((p) => p.status === "in_force");
  const retired = policies.filter((p) => p.status === "retired");

  return (
    <>
      <PageHeader title="Lessons and policies">
        <button className="btn" onClick={reload} disabled={loading}>Refresh</button>
      </PageHeader>
      <p className="mb-6 max-w-prose text-sm text-paper-muted">
        Policies are rules members follow. Proposed policies are listed for comment and are not yet rules; an enacted
        policy stays in force until another enacted policy supersedes or revokes it. Lessons are what members concluded
        from experience; open one to see the records it refers to.
      </p>
      {loading && !lessonsData && !policiesData && <Loading />}
      <ErrorNote error={error} onRetry={reload} />
      {( !loading || lessonsData || policiesData ) && (
        <>
          <Section id="proposals-h" title="Proposals" records={proposals}
                   empty="No proposed policies yet. A member needs the policy permission (Members page) to propose one." />
          <Section id="policies-h" title="Policies" records={[...inForce, ...retired]}
                   empty="No policies yet. A member needs the policy permission (Members page) to write one." />
          <Section id="lessons-h" title="Lessons" records={lessons}
                   empty="No lessons yet. Members write them after noticing a pattern, such as repeated reflex stops." />
        </>
      )}
    </>
  );
}
