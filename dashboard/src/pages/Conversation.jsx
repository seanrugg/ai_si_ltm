import { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import { useApi } from "../api/useApi.js";
import { api } from "../api/client.js";
import { EmptyState, ErrorNote, Loading, PageHeader } from "../components/ui.jsx";
import { localTime, relativeTime } from "../lib/format.js";

const LIMIT = 200;
const MAX_MEMBERS = 16;

function recipientText(r) {
  const peers = r.peers || [];
  return peers.length === 0 ? "everyone" : peers.join(", ");
}

function RecordRow({ r, twoMember, firstMember }) {
  const payloadText = r.payload && typeof r.payload.text === "string" ? r.payload.text : null;
  const mine = twoMember && firstMember && r.agent === firstMember;
  const side = mine ? "justify-start" : "justify-end";
  const bubble = mine ? "border-ink-line bg-ink-raised" : "border-led/40 bg-led/10";
  return (
    <li key={r.hash} className={`flex ${side} gap-2`}>
      <div className={`max-w-[85%] rounded-lg border px-3 py-2 sm:max-w-md ${bubble}`}>
        <p className="flex flex-wrap items-baseline gap-x-2 text-xs text-paper-muted">
          <span className="font-bold text-paper">{r.agent}</span>
          <span>→ {recipientText(r)}</span>
          <span title={localTime(r.ts)}>{relativeTime(r.ts)}</span>
          {r.from_outside && (
            <span className="ml-2 border border-led/40 rounded px-1.5 py-0.5 text-[10px] text-led">
              from outside
            </span>
          )}
          {r.relayed_by && (
            <span className="ml-2 border border-led/40 rounded px-1.5 py-0.5 text-[10px] text-led">
              relayed by {r.relayed_by}
            </span>
          )}
          <Link to={`/record/${r.agent}/${r.seq}`} className="underline decoration-ink-line hover:decoration-led">
            #{r.seq}
          </Link>
        </p>
        {r.summary && <p className="mt-1 text-sm">{r.summary}</p>}
        {payloadText && <p className="mt-1 text-sm text-paper-muted">“{payloadText}”</p>}
      </div>
    </li>
  );
}

export default function Conversation() {
  const [view, setView] = useState("swarm");

  // Swarm view state
  const [swarm, setSwarm] = useState("");
  const [memberFilters, setMemberFilters] = useState(new Set());
  const [records, setRecords] = useState([]);
  const [cursor, setCursor] = useState(null); // {ts, id} of the oldest record on the page
  const [canLoadMore, setCanLoadMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Members view state
  const [selectedMembers, setSelectedMembers] = useState([]);

  const { data: swarmsData } = useApi("/admin/swarms");
  const { data: membersData } = useApi("/admin/members");
  const swarms = swarmsData || [];
  const memberNames = (membersData?.members || [])
    .filter((m) => m.status === "active" || m.status === "revoked")
    .map((m) => m.name)
    .sort();

  const loadConversation = useCallback(async (beforeCursor, append = false) => {
    setLoading(true);
    setError(null);
    try {
      if (view === "swarm") {
        if (!swarm) {
          setRecords([]);
          setCursor(null);
          setCanLoadMore(false);
          return;
        }
        const p = { swarm, limit: LIMIT };
        if (beforeCursor) p.before = `${beforeCursor.ts},${beforeCursor.id}`;
        const data = await api("/admin/swarm-conversation", { params: p });
        const rows = data.records || [];
        setRecords((prev) => (append ? [...rows, ...prev] : rows));
        if (rows.length) {
          const oldest = rows[rows.length - 1];
          setCursor({ ts: oldest.ts, id: oldest.id });
          setCanLoadMore(rows.length === LIMIT);
        } else {
          setCursor(null);
          setCanLoadMore(false);
        }
      } else {
        if (selectedMembers.length < 2) {
          setRecords([]);
          setCursor(null);
          return;
        }
        const data = await api("/admin/conversation", {
          params: { members: selectedMembers.join(","), limit: LIMIT },
        });
        setRecords(data.records || []);
        setCursor(null);
      }
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, [view, swarm, selectedMembers]);

  useEffect(() => {
    loadConversation(null, false);
  }, [loadConversation]);

  // When there is only one selectable option (just "Free agents") and nothing
  // is chosen yet, preselect it instead of leaving "Select a swarm".
  useEffect(() => {
    if (swarmsData && swarmsData.length === 0 && swarm === "") setSwarm("free");
  }, [swarmsData, swarm]);

  function toggleFilter(name) {
    setMemberFilters((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  function toggleMember(name) {
    setSelectedMembers((prev) => {
      if (prev.includes(name)) return prev.filter((n) => n !== name);
      if (prev.length >= MAX_MEMBERS) return prev;
      return [...prev, name];
    });
  }

  const displayRecords =
    view === "swarm"
      ? records.filter((r) => memberFilters.size === 0 || memberFilters.has(r.agent))
      : records;
  const twoMember = view === "members" && selectedMembers.length === 2;
  const swarmEmpty = view === "swarm" && !swarm;
  const membersEmpty = view === "members" && selectedMembers.length < 2;
  const noMessages = !displayRecords.length && !swarmEmpty && !membersEmpty;

  return (
    <>
      <PageHeader title="Conversation">
        <div role="radiogroup" aria-label="Conversation view" className="flex items-center gap-3">
          <label className="inline-flex items-center gap-1.5 cursor-pointer select-none">
            <input
              type="radio"
              name="view"
              value="swarm"
              checked={view === "swarm"}
              onChange={() => setView("swarm")}
            />
            Swarm
          </label>
          <label className="inline-flex items-center gap-1.5 cursor-pointer select-none">
            <input
              type="radio"
              name="view"
              value="members"
              checked={view === "members"}
              onChange={() => setView("members")}
            />
            Members
          </label>
        </div>
      </PageHeader>

      {view === "swarm" ? (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <label className="text-sm text-paper-muted" htmlFor="swarm-picker">
            Swarm
          </label>
          <select
            id="swarm-picker"
            className="field"
            value={swarm}
            onChange={(e) => setSwarm(e.target.value)}
            aria-label="Choose a swarm"
          >
            <option value="">Select a swarm</option>
            {swarms.map((s) => (
              <option key={s.id} value={s.name}>{s.name}</option>
            ))}
            <option value="free">Free agents</option>
          </select>
          {memberNames.length > 0 && (
            <div className="ml-4 flex flex-wrap gap-2" aria-label="Filter timeline by author">
              <span className="text-xs text-paper-muted">Filter authors:</span>
              {memberNames.map((name) => (
                <button
                  key={name}
                  type="button"
                  onClick={() => toggleFilter(name)}
                  className={`rounded border px-2 py-1 text-xs ${
                    memberFilters.has(name)
                      ? "bg-led border-led text-led"
                      : "border-ink-line text-paper-muted hover:bg-led/10"
                  }`}
                >
                  {name}
                </button>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="mb-4">
          <p className="text-xs text-paper-muted">Select 2–16 members.</p>
          <div className="flex flex-wrap gap-2" aria-label="Select members">
            {memberNames.map((name) => (
              <label key={name} className="cursor-pointer select-none flex items-center gap-2">
                <input
                  type="checkbox"
                  className="accent-led"
                  checked={selectedMembers.includes(name)}
                  onChange={() => toggleMember(name)}
                />
                {name}
              </label>
            ))}
          </div>
          {selectedMembers.length > MAX_MEMBERS && (
            <p className="mt-1 text-xs text-bad">Maximum 16 members.</p>
          )}
        </div>
      )}

      {loading && !records.length && <Loading what="Loading the conversation" />}
      {error && !loading && <ErrorNote error={error} onRetry={() => loadConversation(null, false)} />}
      {!loading && !error && (
        <>
          {swarmEmpty && (
            <EmptyState title="Pick a swarm">
              Choose a swarm from the picker to see its communication timeline.
            </EmptyState>
          )}
          {membersEmpty && (
            <EmptyState title="Pick at least two members">
              See every message between the selected members, oldest first.
            </EmptyState>
          )}
          {noMessages && (
            <EmptyState title={view === "swarm" ? "No messages in this swarm yet" : "No messages between these members"}>
              Communications show up here when a member names a peer in its record.
            </EmptyState>
          )}
          {!swarmEmpty && !membersEmpty && !noMessages && (
            <>
              <ol className="flex flex-col gap-3" aria-label="Conversation timeline, oldest first">
                {displayRecords.map((r) => (
                  <RecordRow key={r.hash} r={r} twoMember={twoMember} firstMember={selectedMembers[0]} />
                ))}
              </ol>
              {view === "swarm" && canLoadMore && (
                <button onClick={() => loadConversation(cursor, true)} disabled={loading} className="btn">
                  Load older
                </button>
              )}
            </>
          )}
        </>
      )}
    </>
  );
}
