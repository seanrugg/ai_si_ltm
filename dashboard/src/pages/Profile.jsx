// A member's or a swarm's profile: identity, tabs, legend (issue #192).
import { useEffect, useRef, useState } from "react";
import PingButton from "../components/PingButton.jsx";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client.js";
import { useApi } from "../api/useApi.js";
import { ErrorNote, Loading, StatusBadge } from "../components/ui.jsx";
import { Legend, MemberMark } from "../components/visual.jsx";
import ProxiedBadge from "../components/ProxiedBadge.jsx";
import { localTime, relativeTime } from "../lib/format.js";
import { DEFAULT_TAB, RANGES, nextTab, rangeOf, swarmCount, tabOf, tabsFor } from "../lib/profile.js";
import { KINDS, NEUTRAL, ROLES, kindOf, roleOf } from "../lib/visual.js";
import Overview from "./profile/Overview.jsx";
import Activity from "./profile/Activity.jsx";
import History from "./profile/History.jsx";
import Members from "./profile/Members.jsx";
import Specs from "./profile/Specs.jsx";
import Interactions from "./profile/Interactions.jsx";
import Network from "./profile/Network.jsx";
import Places from "./profile/Places.jsx";
import Senses from "./profile/Senses.jsx";

export const SWARM_PATH = "M10 6a2 2 0 1 0 4 0a2 2 0 1 0-4 0 M3 18a2 2 0 1 0 4 0a2 2 0 1 0-4 0 M17 18a2 2 0 1 0 4 0a2 2 0 1 0-4 0 M11 7.8 6 16.2 M13 7.8l5 8.4 M7 18h10";

// Tabs built so far; the rest say "Coming soon" until their issue merges.
const TAB_VIEWS = { overview: Overview, members: Members, specs: Specs, places: Places, interactions: Interactions, network: Network, activity: Activity,
  senses: Senses, history: History };

export function SwarmMark({ size = 40 }) {
  return (
    <span className="relative inline-flex shrink-0 items-center justify-center rounded-full bg-ink"
          style={{ width: size, height: size, boxShadow: `inset 0 0 0 3px ${NEUTRAL}` }}>
      <svg width={size * 0.58} height={size * 0.58} viewBox="0 0 24 24" fill="none" stroke={NEUTRAL}
           strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
        <path d={SWARM_PATH} />
      </svg>
      <span className="sr-only">Swarm</span>
    </span>
  );
}

/** The member row from the admin list, with its swarm and role merged in. */
export function memberWithRole(member, swarms) {
  if (!member) return null;
  for (const s of swarms || []) {
    const m = (s.members || []).find((x) => x.name === member.name);
    if (m) return { ...member, role: m.role, swarm: s.name };
  }
  return { ...member, role: null, swarm: null };
}

function Tabs({ tab, subject, onSelect }) {
  const refs = useRef({});
  function onKeyDown(e) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    const next = nextTab(tab, e.key, subject);
    onSelect(next);
    refs.current[next]?.focus();
  }
  return (
    <div role="tablist" aria-label="Profile sections" className="mb-3 flex flex-wrap gap-1 border-b border-ink-line"
         onKeyDown={onKeyDown}>
      {tabsFor(subject).map((t) => (
        <button key={t.key} type="button" role="tab" id={`tab-${t.key}`} aria-controls={`panel-${t.key}`}
                aria-selected={tab === t.key} tabIndex={tab === t.key ? 0 : -1}
                ref={(el) => { refs.current[t.key] = el; }}
                onClick={() => onSelect(t.key)}
                className={`-mb-px rounded-t-md border px-3 py-1.5 text-sm ${tab === t.key
                  ? "border-ink-line border-b-ink bg-ink text-paper"
                  : "border-transparent text-paper-muted hover:text-paper"}`}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

function Fact({ label, children }) {
  return (
    <span className="flex flex-col">
      <span className="text-xs text-paper-faint">{label}</span>
      <span className="text-sm">{children}</span>
    </span>
  );
}

export default function Profile({ subject }) {
  const { name } = useParams();
  const [params, setParams] = useSearchParams();
  const range = rangeOf(params.get("range"));
  const tab = tabOf(params.get("tab"), subject);
  const subjectParams = { [subject]: name };

  function setParam(key, value) {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set(key, value);
      return next;
    }, { replace: true });
  }

  const summary = useApi("/admin/profile/summary", { ...subjectParams, hours: range.hours });
  const history = useApi("/admin/profile/history", subjectParams);
  const members = useApi("/admin/members");
  const swarms = useApi("/admin/swarms");

  // Best location (#191). Optional: an older server without it simply shows none.
  const [best, setBest] = useState(null);
  useEffect(() => {
    let live = true;
    setBest(null);
    if (subject !== "member") return undefined;
    api("/admin/locations", { params: { member: name } })
      .then((d) => { if (live) setBest(d?.members?.[0]?.best ?? null); })
      .catch(() => {});
    return () => { live = false; };
  }, [subject, name]);

  const memberRows = members.data?.members ?? members.data ?? [];
  const member = subject === "member"
    ? memberWithRole(memberRows.find((m) => m.name === name), swarms.data) : null;
  const swarm = subject === "swarm" ? (swarms.data || []).find((s) => s.name === name) : null;
  const wipes = (history.data?.events || []).filter((e) => e.action === "memory_wipe");

  const View = TAB_VIEWS[tab];
  const role = member ? roleOf(member) : null;
  const kind = member ? kindOf(member) : null;

  return (
    <>
      <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <nav aria-label="Breadcrumb" className="text-sm text-paper-muted">
          <Link to={subject === "member" ? "/members" : "/swarms"} className="hover:underline">
            {subject === "member" ? "Members" : "Swarms"}
          </Link>
          <span aria-hidden="true"> / </span>
          <span className="text-paper" aria-current="page">{name}</span>
        </nav>
        <div className="flex flex-wrap items-center gap-3">
          {subject === "member" && member?.swarm && (
            <span role="group" aria-label="Profile of" className="flex overflow-hidden rounded-md border border-ink-line text-sm">
              <span className="bg-ink-raised px-3 py-1" aria-current="true">Member</span>
              <Link to={`/swarms/${encodeURIComponent(member.swarm)}/profile?${params}`} className="px-3 py-1 hover:bg-ink-hover">
                Swarm
              </Link>
            </span>
          )}
          <span role="group" aria-label="Time range" className="flex gap-1">
            {RANGES.map((r) => (
              <button key={r.key} type="button" aria-pressed={range.key === r.key}
                      onClick={() => setParam("range", r.key)}
                      className={`rounded-full border px-3 py-1 text-sm ${range.key === r.key
                        ? "border-led text-paper" : "border-ink-line text-paper-muted hover:text-paper"}`}>
                {r.label}
              </button>
            ))}
          </span>
        </div>
      </header>

      <section aria-label="Identity" className="panel mb-4 flex flex-wrap items-center gap-4 p-4">
        {subject === "member"
          ? <MemberMark member={member || {}} size={44} />
          : <SwarmMark size={44} />}
        <div className="mr-4">
          <h1 className="text-xl font-bold">{name} {member?.proxied_by && <ProxiedBadge proxiedBy={member.proxied_by} />}</h1>
          <span className="text-sm text-paper-muted">
            {subject === "member"
              ? `${ROLES[role]?.label ?? "Free agent"} · ${KINDS[kind]?.label ?? ""}`
              : `Swarm · ${swarmCount(swarm?.members)}`}
          </span>
        </div>
        {member && (
          <div className="flex flex-wrap gap-6">
            <Fact label="Status"><StatusBadge status={member.status} /></Fact>
            <Fact label="Memory">
              {member.memory_generation > 0
                ? `generation ${member.memory_generation}${wipes[0] ? `, last wiped ${relativeTime(wipes[0].ts)}` : ""}`
                : "never wiped"}
            </Fact>
            {best && (
              <Fact label="Location">
                {best.label || "unnamed"}{best.room ? `, ${best.room}` : ""}
                <span className="text-paper-muted"> ({best.source})</span>
              </Fact>
            )}
            <Fact label="Last seen">
              <span title={localTime(member.last_seen_at)}>{member.last_seen_at ? relativeTime(member.last_seen_at) : "never"}</span>
            </Fact>
            {member.status === "active" && <Fact label="Ping"><PingButton name={name} /></Fact>}
          </div>
        )}
      </section>

      <Tabs tab={tab} subject={subject} onSelect={(t) => setParam("tab", t)} />
      <div className="mb-4"><Legend /></div>

      <section role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        <ErrorNote error={summary.error} onRetry={summary.reload} />
        {View
          ? (summary.loading && !summary.data
            ? <Loading what="Loading the profile" />
            : summary.data && <View subject={subject} name={name} summary={summary.data} history={history.data}
                                    range={range} members={memberRows} swarms={swarms.data}
                                    onReload={() => { swarms.reload?.(); members.reload?.(); }}
                                    onSelectPeer={(peer) => setParams((prev) => {
                                      const next = new URLSearchParams(prev);
                                      next.set("tab", "interactions");
                                      next.set("peer", peer);
                                      return next;
                                    })} />)
          : <p className="py-8 text-sm text-paper-muted">Coming soon.</p>}
      </section>
    </>
  );
}

export { DEFAULT_TAB };
