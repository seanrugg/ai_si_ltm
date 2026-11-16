// Fleet Geography (#382, under Analyze): the profile's Geography view (#405)
// with no profile scope. Every swarm and free agent on one globe, every stream
// to LTMS, a swarm filter, and the table twin in VizFrame's Calm view.
//
// One renderer: this page reuses `useGeographyData`, `<GeographyView>` and the
// table twin from pages/profile/Network.jsx. It adds only the swarm filter
// (which trims the data before the view sees it) and a data card for what was
// touched. Addresses stay in the admin-only "Members and addresses" table;
// the card and narration never carry one.
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useApi } from "../api/useApi.js";
import { ErrorNote, Loading, PageHeader } from "../components/ui.jsx";
import { redactAddresses, sourceText } from "../lib/geography.js";
import { RANGES, rangeOf } from "../lib/profile.js";
import { createReplayClock } from "../lib/replay.js";
import DataCard from "./profile/DataCard.jsx";
import { rangeSpan } from "./profile/Interactions.jsx";
import { GeographyView, serverPlace, useGeographyData } from "./profile/Network.jsx";

const LIVE_RELOAD_MS = 60_000;
const WHOLE_FLEET = {}; // useGeographyData scope: no profile, the whole fleet
export const FREE_AGENTS = "__free";

const profileOf = (name) => `/members/${encodeURIComponent(name)}/profile?tab=geography`;
const swarmProfileOf = (name) => `/swarms/${encodeURIComponent(name)}/profile?tab=geography`;

/**
 * Trim the fleet to one swarm (or the free agents) before GeographyView sees it.
 * `filter`: "" for everything, a swarm name, or FREE_AGENTS.
 * Returns {data, swarms}: the same shapes, with only the chosen members' rows.
 */
export function filterFleet(data, swarms, filter) {
  const list = swarms || [];
  if (!filter) return { data, swarms: list };
  const inSwarm = new Set(list.flatMap((s) => (s.members || []).map((m) => m.name)));
  const kept = filter === FREE_AGENTS ? [] : list.filter((s) => s.name === filter);
  const keep = filter === FREE_AGENTS
    ? (name) => !inSwarm.has(name)
    : (name) => kept.some((s) => (s.members || []).some((m) => m.name === name));
  const locations = data.locations
    ? { ...data.locations, members: (data.locations.members || []).filter((m) => keep(m.name)) }
    : data.locations;
  return {
    swarms: kept,
    data: {
      ...data,
      locations,
      network: (data.network || []).filter((m) => keep(m.name)),
      episodes: Object.fromEntries(Object.entries(data.episodes || {}).filter(([m]) => keep(m))),
    },
  };
}

function MemberLinks({ names }) {
  return (
    <ul className="space-y-0.5">
      {names.map((n) => (
        <li key={n}><Link className="underline" to={profileOf(n)}>{n}</Link></li>
      ))}
    </ul>
  );
}

/** The card for a light (a swarm's site or a free agent) or one member. No addresses. */
export function FleetCard({ selected, data, swarms, onClose }) {
  if (selected.kind === "light") {
    const l = selected.light;
    return (
      <DataCard title={l.place ? `${l.label} · ${l.place}` : l.label} onClose={onClose}
                actions={l.swarm
                  ? <Link className="btn" to={swarmProfileOf(l.swarm)}>Open {l.swarm}&apos;s profile</Link>
                  : <Link className="btn" to={profileOf(l.label)}>Open {l.label}&apos;s profile</Link>}>
        <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1 text-sm">
          <dt className="text-paper-muted">{l.swarm ? "Swarm" : "Free agent"}</dt><dd>{l.label}</dd>
          <dt className="text-paper-muted">Location source</dt><dd>{sourceText(l)}</dd>
          {l.hubs.length > 0 && (<><dt className="text-paper-muted">Hub</dt><dd>{l.hubs.join(", ")}</dd></>)}
          <dt className="text-paper-muted">Records in range</dt><dd>{l.records}</dd>
          <dt className="text-paper-muted">Members here</dt><dd><MemberLinks names={l.members} /></dd>
        </dl>
      </DataCard>
    );
  }
  const name = selected.name;
  const row = (data.locations?.members || []).find((m) => m.name === name);
  const swarm = (swarms || []).find((s) => (s.members || []).some((m) => m.name === name));
  const role = swarm?.members.find((m) => m.name === name)?.role;
  const light = selected.light ?? null;
  let where;
  if (light) where = sourceText(light);
  else if (selected.satellite) where = "cloud agent: a satellite, its place is its provider's";
  else where = `unplaced: ${redactAddresses(row?.unplaced || "No location yet.")}`;
  return (
    <DataCard title={name} onClose={onClose}
              actions={<Link className="btn" to={profileOf(name)}>Open {name}&apos;s profile</Link>}>
      <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className="text-paper-muted">Swarm</dt>
        <dd>{swarm ? <Link className="underline" to={swarmProfileOf(swarm.name)}>{swarm.name}</Link> : "free agent"}</dd>
        {role && (<><dt className="text-paper-muted">Role</dt><dd>{role}</dd></>)}
        <dt className="text-paper-muted">Place</dt>
        <dd>{light?.place || "—"}</dd>
        <dt className="text-paper-muted">Location</dt><dd>{where}</dd>
      </dl>
    </DataCard>
  );
}

export default function Geography() {
  const [params, setParams] = useSearchParams();
  const range = rangeOf(params.get("range"));
  const filter = params.get("swarm") || "";
  const setParam = (key, value) => setParams((prev) => {
    const next = new URLSearchParams(prev);
    if (value) next.set(key, value);
    else next.delete(key);
    return next;
  }, { replace: true });

  const membersApi = useApi("/admin/members");
  const swarmsApi = useApi("/admin/swarms");
  const members = membersApi.data?.members ?? membersApi.data ?? [];
  const allSwarms = useMemo(() => swarmsApi.data || [], [swarmsApi.data]);
  const data = useGeographyData(WHOLE_FLEET, range);

  const [clock] = useState(() => {
    const [since, until] = rangeSpan(range);
    return createReplayClock({ start: since, end: until });
  });
  useEffect(() => {
    if (data.since != null) clock.setRange(data.since, data.until);
  }, [clock, data.since, data.until]);
  useEffect(() => {
    const id = setInterval(() => {
      if (clock.getState().mode === "live") data.reload();
    }, LIVE_RELOAD_MS);
    return () => clearInterval(id);
  }, [clock, data.reload]); // eslint-disable-line react-hooks/exhaustive-deps

  const trimmed = useMemo(() => filterFleet(data, allSwarms, filter), [data, allSwarms, filter]);
  const [selected, setSelected] = useState(null);
  const close = useCallback(() => setSelected(null), []);
  useEffect(() => { setSelected(null); }, [filter]);

  const unknownFilter = filter && filter !== FREE_AGENTS && swarmsApi.data && !allSwarms.some((s) => s.name === filter);
  const ready = data.locations && swarmsApi.data && membersApi.data;
  const firstLoad = !ready && (data.loading || swarmsApi.loading || membersApi.loading);

  return (
    <>
      <PageHeader title="Geography">
        <p className="mb-4 max-w-prose text-sm text-paper-muted">
          Where the whole fleet is: every swarm and free agent, and every stream of records to FLTMS.
          Zoom into a swarm to see its star system and hub relays. The Table view lists every site.
        </p>
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm">
            Swarm
            <select className="field" value={filter} onChange={(e) => setParam("swarm", e.target.value)}>
              <option value="">All swarms and free agents</option>
              {allSwarms.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
              <option value={FREE_AGENTS}>Free agents only</option>
            </select>
          </label>
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
      </PageHeader>
      <ErrorNote error={data.error || swarmsApi.error || membersApi.error}
                 onRetry={() => { data.reload(); swarmsApi.reload(); membersApi.reload(); }} />
      {unknownFilter && <p className="mb-2 text-sm text-paper-muted">No swarm named {filter}.</p>}
      {firstLoad && <Loading what="Loading the globe" />}
      {ready && (
        <div className="relative">
          <GeographyView key={filter || "all"} data={trimmed.data} members={members} swarms={trimmed.swarms}
                         clock={clock} onSelect={setSelected} />
          {selected && <FleetCard selected={selected} data={trimmed.data} swarms={trimmed.swarms} onClose={close} />}
        </div>
      )}
      {ready && !serverPlace(data.locations) && (
        <p className="mt-2 text-xs text-paper-muted">
          Declare the server&apos;s location (a member location named <code>ltms-server</code>) to place the FLTMS pyramid on the globe.
        </p>
      )}
    </>
  );
}
