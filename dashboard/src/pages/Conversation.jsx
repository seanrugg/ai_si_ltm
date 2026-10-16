import { Link, useSearchParams } from "react-router-dom";
import { useApi } from "../api/useApi.js";
import { EmptyState, ErrorNote, Loading, PageHeader } from "../components/ui.jsx";
import { localTime, relativeTime } from "../lib/format.js";

function Thread({ a, b }) {
  const { data, error, loading, reload } = useApi("/admin/conversation", { member_a: a, member_b: b, limit: 200 });
  const records = data?.records || [];
  if (loading && !data) return <Loading what="Loading the conversation" />;
  if (error) return <ErrorNote error={error} onRetry={reload} />;
  if (!records.length) {
    return (
      <EmptyState title={`No messages between ${a} and ${b}`}>
        Communications show up here when one member names the other in its peers.
      </EmptyState>
    );
  }
  return (
    <ol className="flex flex-col gap-3" aria-label={`Conversation between ${a} and ${b}, oldest first`}>
      {records.map((r) => {
        const mine = r.agent === a;
        const text = r.payload && typeof r.payload.text === "string" ? r.payload.text : null;
        return (
          <li key={r.hash} className={`flex ${mine ? "justify-start" : "justify-end"}`}>
            <div className={`max-w-[85%] rounded-lg border px-3 py-2 sm:max-w-md ${mine ? "border-ink-line bg-ink-raised" : "border-led/40 bg-led/10"}`}>
              <p className="flex flex-wrap items-baseline gap-x-2 text-xs text-paper-muted">
                <span className="font-bold text-paper">{r.agent}</span>
                <span title={localTime(r.ts)}>{relativeTime(r.ts)}</span>
                <Link className="underline decoration-ink-line hover:decoration-led" to={`/record/${r.agent}/${r.seq}`}>#{r.seq}</Link>
              </p>
              {r.summary && <p className="mt-1 text-sm">{r.summary}</p>}
              {text && <p className="mt-1 text-sm text-paper-muted">“{text}”</p>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export default function Conversation() {
  const [params, setParams] = useSearchParams();
  const a = params.get("a") || "";
  const b = params.get("b") || "";
  const { data, error, reload } = useApi("/admin/members");
  const names = (data?.members || []).filter((m) => m.status === "active" || m.status === "revoked").map((m) => m.name).sort();

  const pick = (key, value) => {
    const next = { a, b, [key]: value };
    setParams(Object.fromEntries(Object.entries(next).filter(([, v]) => v)));
  };

  return (
    <>
      <PageHeader title="Conversation" />
      <ErrorNote error={error} onRetry={reload} />
      <div className="mb-6 flex flex-wrap items-center gap-2">
        <select className="field" value={a} onChange={(e) => pick("a", e.target.value)} aria-label="First member">
          <option value="">First member</option>
          {names.map((n) => <option key={n} disabled={n === b}>{n}</option>)}
        </select>
        <span className="text-sm text-paper-muted">and</span>
        <select className="field" value={b} onChange={(e) => pick("b", e.target.value)} aria-label="Second member">
          <option value="">Second member</option>
          {names.map((n) => <option key={n} disabled={n === a}>{n}</option>)}
        </select>
      </div>
      {a && b && a !== b ? (
        <Thread a={a} b={b} />
      ) : (
        <EmptyState title="Pick two members">
          See every message between them, oldest first. The first member's messages appear on the left.
        </EmptyState>
      )}
    </>
  );
}
