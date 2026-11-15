// Profile Geography tab (#405; was Network, and folds in Places & movement).
// One tab for "where", at every scale: the globe → a swarm's star system on the
// map → the room-level view (Choreography) under the frame.
//
// Reuse (#382, the fleet-wide Geography page): `useGeographyData(scope)` with
// scope {} loads the whole fleet, and `<GeographyView>` draws it with no
// profile scope. The globe itself is ./Globe.jsx; the data shaping is
// lib/geography.js; the camera and projection are lib/globe.js.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../../api/client.js";
import GeoCredit from "../../components/GeoCredit.jsx";
import VizFrame, { HoloTable } from "../../components/VizFrame.jsx";
import { ErrorNote, Loading } from "../../components/ui.jsx";
import {
  INTO_ZOOM, MAX_ZOOM, MIN_ZOOM, SYSTEM_FADE, createGlobeCamera, systemFade,
} from "../../lib/globe.js";
import {
  buildGeography, countsUpTo, globeTouchables, lightOf, placeRows, redactAddresses,
} from "../../lib/geography.js";
import { createReplayClock } from "../../lib/replay.js";
import { sound } from "../../lib/sound.js";
import { buildScene, eventsOf, touchList } from "../../lib/topology.js";
import { heartbeatBpm } from "../../lib/light.js";
import { RELAY } from "../../lib/visual.js";
import Globe from "./Globe.jsx";
import { rangeSpan, rosterFor, swarmFor } from "./Interactions.jsx";
import NetworkTable from "./NetworkTable.jsx";
import RoomLevel, { PlacesTables } from "./Places.jsx";

const MAX_SHOWN = 24; // members whose records are fetched (as in Topology)
const LIVE_RELOAD_MS = 60_000;
const ZOOM_STEP = 1.6;
const START_ZOOM = 1.6;
const WHOLE_ZOOM = 1;
const LEVEL_POLL_MS = 250;
const now = () => (typeof performance !== "undefined" ? performance.now() : Date.now());

/**
 * Locations, addresses and records for a scope: {swarm}, {member}, or {} for
 * the whole fleet. Returns {loading, error, locations, network, episodes, since, until, reload}.
 */
export function useGeographyData(scope, range) {
  const [reloadKey, setReloadKey] = useState(0);
  const [state, setState] = useState({ loading: true, error: null, locations: null, network: [], episodes: {} });
  const key = JSON.stringify([scope, range?.hours ?? null, reloadKey]);
  useEffect(() => {
    let live = true;
    const [since, until] = rangeSpan(range);
    const iso = (t) => new Date(t).toISOString();
    setState((s) => ({ ...s, loading: true, error: null }));
    Promise.all([
      api("/admin/locations", { params: scope }),
      api("/admin/network", { params: scope }).catch(() => null),
    ]).then(async ([locations, network]) => {
      const names = (locations?.members || []).map((m) => m.name).slice(0, MAX_SHOWN);
      const eps = await Promise.all(names.map((m) => api("/admin/profile/episodes",
        { params: { member: m, since: iso(since), until: iso(until) } }).then((d) => [m, d]).catch(() => [m, null])));
      if (!live) return;
      setState({
        loading: false, error: null, since, until, locations, network: network?.members ?? [],
        episodes: Object.fromEntries(eps.filter(([, d]) => d)),
      });
    }).catch((error) => {
      if (live) setState((s) => ({ ...s, loading: false, error }));
    });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const reload = useCallback(() => setReloadKey((k) => k + 1), []);
  return { ...state, reload };
}

/** The server's place, when declared: {lon, lat, label}. */
export function serverPlace(locations) {
  const s = locations?.server;
  return s && s.lat != null && s.lon != null ? { lon: Number(s.lon), lat: Number(s.lat), label: s.label || null } : null;
}

/** The twin's Unplaced table rows: name and reason (addresses taken out). */
export function unplacedRows(geo) {
  return geo.unplaced.map((u) => ({ id: u.name, name: u.name, reason: u.reason }));
}

/** The table twin: Places, Unplaced, and Members and addresses. */
export function GeographyTables({ geo, server, events, t, locations, network, members, extra }) {
  const rows = placeRows(geo, server, countsUpTo(events, t));
  const unplaced = unplacedRows(geo);
  return (
    <div className="space-y-4">
      <HoloTable caption="Places" rows={rows}
                 columns={[{ key: "site", label: "Site", render: (r) => <strong>{r.site}</strong> },
                   { key: "members", label: "Members" },
                   { key: "source", label: "Location source" },
                   { key: "records", label: "Records to LTMS up to the hand", numeric: true,
                     render: (r) => (r.records == null ? "—" : r.records) }]} />
      <HoloTable caption={`Unplaced (${unplaced.length})`} rows={unplaced}
                 columns={[{ key: "name", label: "Member", render: (r) => <strong>{r.name}</strong> },
                   { key: "reason", label: "Why it isn't on the globe" }]} />
      {unplaced.length === 0 && <p className="text-sm text-paper-muted">Every member has a place.</p>}
      {locations?.geo && !locations.geo.enabled && (
        <p className="text-xs text-paper-muted">
          Network estimates are off: {redactAddresses(locations.geo.reason)}. See Settings → Location estimates.
        </p>
      )}
      {extra}
      <NetworkTable netRows={network} locRows={locations?.members} members={members} />
    </div>
  );
}

/**
 * The Geography frame: the globe, its touch list and its table twin.
 *   data        useGeographyData(...)
 *   members     `/admin/members` rows;  swarms  `/admin/swarms`
 *   clock       a replay clock (the frame drives it)
 *   focusName   a member to centre and focus (member profile), or null
 *   startInside start zoomed into the focus member's (or the only swarm's) light (`?tab=places`)
 *   extraTables more tables for the twin (the profile's Records by place, Location sources)
 *   defaultCalm passed to VizFrame
 */
export function GeographyView({ data, members, swarms, clock, focusName = null, startInside = false, extraTables, defaultCalm }) {
  const events = useMemo(() => Object.entries(data.episodes || {})
    .flatMap(([m, ep]) => eventsOf(m, ep)).sort((a, b) => a.t - b.t), [data.episodes]);
  const counts = useMemo(() => countsUpTo(events), [events]);
  const geo = useMemo(() => buildGeography({ locations: data.locations, swarms, members, counts }),
    [data.locations, swarms, members, counts]);
  const server = useMemo(() => serverPlace(data.locations), [data.locations]);
  const heartbeats = useMemo(() => Object.fromEntries(Object.entries(data.episodes || {})
    .map(([m, ep]) => [m, ep?.heartbeat?.interval_s ?? null])), [data.episodes]);

  // A light's star system: its swarm (or the free agent alone), the same scene as Topology.
  const sceneFor = useMemo(() => {
    const cache = new Map();
    return (light) => {
      if (!light) return null;
      const key = light.swarm ?? `free:${light.label}`;
      if (!cache.has(key)) {
        const swarm = light.swarm ? (swarms || []).find((s) => s.name === light.swarm) : null;
        const roster = swarm ? rosterFor("swarm", swarm.name, swarm, members) : rosterFor("member", light.label, null, members);
        const names = new Set(roster.map((m) => m.name));
        const episodes = Object.fromEntries(Object.entries(data.episodes || {}).filter(([m]) => names.has(m)));
        cache.set(key, buildScene({
          roster, network: data.network, episodes, focus: focusName && names.has(focusName) ? focusName : null,
        }));
      }
      return cache.get(key);
    };
  }, [swarms, members, data.episodes, data.network, focusName]);

  const homeLight = (focusName && lightOf(geo, focusName)) || geo.lights[0] || null;
  const [camera] = useState(() => {
    const at = homeLight ?? server;
    const inside = startInside && homeLight;
    return createGlobeCamera({ lon: at?.lon ?? 0, lat: at?.lat ?? 20, zoom: inside ? INTO_ZOOM : START_ZOOM },
      typeof performance !== "undefined" ? performance.now() : 0);
  });
  const [focus, setFocus] = useState(() => (startInside && homeLight ? { lightId: homeLight.id } : null));
  const animate = useRef(true);
  const moveTo = (target) => camera.moveTo(target, now(), { instant: !animate.current });

  // The touch list follows the level: the globe, or inside a swarm's system.
  const [zoom, setZoom] = useState(camera.view.zoom);
  useEffect(() => {
    // Stepping here too keeps eased moves going when the canvas isn't drawing (no 2D context).
    const id = setInterval(() => {
      const v = camera.step(now(), { animate: animate.current });
      setZoom((z) => (Math.abs(z - v.zoom) > 0.01 ? v.zoom : z));
    }, LEVEL_POLL_MS);
    return () => clearInterval(id);
  }, [camera]);
  const insideLight = systemFade(zoom) > 0.5
    ? (focus?.lightId ? geo.lights.find((l) => l.id === focus.lightId) : null) ?? homeLight : null;
  const insideScene = insideLight ? sceneFor(insideLight) : null;

  const into = (light) => {
    setFocus({ lightId: light.id });
    moveTo({ lon: light.lon, lat: light.lat, zoom: INTO_ZOOM });
  };
  const wholeGlobe = () => {
    setFocus(null);
    moveTo({ zoom: WHOLE_ZOOM });
  };
  const zoomBy = (f) => {
    if (f < 1 && camera.view.zoom * f < SYSTEM_FADE[0]) setFocus(null);
    moveTo({ zoom: camera.view.zoom * f });
  };
  const goUp = () => {
    if (camera.view.zoom >= SYSTEM_FADE[0]) {
      setFocus(null);
      moveTo({ zoom: START_ZOOM });
    } else wholeGlobe();
  };

  const homes = [];
  for (const l of geo.lights) if (l.swarm && !homes.some((h) => h.swarm === l.swarm)) homes.push(l);
  const controls = [
    { id: "zoom-in", label: "Zoom in", narration: "Zoom in", onTouch: () => zoomBy(ZOOM_STEP), disabled: zoom >= MAX_ZOOM },
    { id: "zoom-out", label: "Zoom out", narration: "Zoom out", onTouch: () => zoomBy(1 / ZOOM_STEP), disabled: zoom <= MIN_ZOOM },
    ...homes.map((l) => ({ id: `into:${l.swarm}`, label: `Into ${l.swarm}`, narration: `Into ${l.swarm}: its star system on the map.`,
      onTouch: () => into(l) })),
    { id: "whole", label: "Whole globe", narration: "The whole globe.", onTouch: wholeGlobe },
  ];
  const lights = insideScene
    ? [{ id: "ltms", label: "LTMS", narration: globeTouchables(geo, server)[0].narration, note: 7 },
      ...touchList(insideScene, {
        skipLtms: true,
        bpm: (p) => (p.heartbeat?.interval_s ? heartbeatBpm(p.heartbeat.interval_s) : null),
      }).map((t) => ({ ...t, note: t.degree }))]
    : globeTouchables(geo, server).map((t) => {
      const light = geo.lights.find((l) => l.id === t.id);
      return light ? { ...t, onTouch: () => into(light) } : t;
    });
  const touchables = [...controls.filter((c) => !c.disabled), ...lights];

  // A click on the canvas: the same as the light's button.
  const touchRef = useRef(null);
  touchRef.current = (id, frame) => {
    const item = lights.find((t) => t.id === id);
    if (!item) return;
    frame.narrate(item.narration ?? item.label);
    item.onTouch?.(item);
  };

  return (
    <VizFrame title="Geography" clock={clock} touchables={touchables} onEscape={goUp} defaultCalm={defaultCalm}
              onCalmChange={(calm) => { animate.current = !calm; }}
              legend={{
                extra: (
                  <>
                    <span className="inline-flex items-center gap-1.5">
                      <svg width="18" height="18" aria-hidden="true">
                        <circle cx="9" cy="9" r="7" fill="none" stroke={RELAY} strokeDasharray="2 3" />
                      </svg>
                      Estimated place (network): a disc, never a point
                    </span>
                    <GeoCredit geo={data.locations?.geo} className="ml-auto" />
                  </>
                ),
              }}
              renderTable={(frame) => (
                <GeographyTables geo={geo} server={server} events={events} t={frame.t} locations={data.locations}
                                 network={data.network} members={members} extra={extraTables} />
              )}>
      {(frame) => {
        animate.current = frame.animate;
        return (
          <Globe geo={geo} server={server} events={events} sceneFor={sceneFor} heartbeats={heartbeats} frame={frame}
                 camera={camera} focus={focus} audible={focusName ?? homeLight?.hubs?.[0] ?? null}
                 onTouch={(id) => {
                   sound.note(id);
                   touchRef.current(id, frame);
                 }} />
        );
      }}
    </VizFrame>
  );
}

/** The profile tab. */
export default function Geography({ subject, name, range, members, swarms, summary }) {
  const [params] = useSearchParams();
  const swarm = swarmFor(subject, name, swarms);
  const scope = useMemo(() => (swarm ? { swarm: swarm.name } : { [subject]: name }), [swarm, subject, name]);
  const data = useGeographyData(scope, range);
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

  const focusName = subject === "member" ? name : null;
  const entries = data.locations?.members?.find((m) => m.name === name)?.all ?? [];
  const firstLoad = data.loading && !data.locations;
  if (firstLoad) return <Loading what="Loading the globe" />;
  return (
    <>
      <ErrorNote error={data.error} onRetry={data.reload} />
      {data.locations && (
        <GeographyView data={data} members={members} swarms={swarms} clock={clock} focusName={focusName}
                       startInside={params.get("tab") === "places"}
                       extraTables={<PlacesTables places={summary?.places} entries={entries} subject={subject} />} />
      )}
      {data.locations && !serverPlace(data.locations) && (
        <p className="mt-2 text-xs text-paper-muted">
          Declare the server&apos;s location (a member location named <code>ltms-server</code>) to place the LTMS pyramid on the globe.
        </p>
      )}
      <div className="mt-6">
        <RoomLevel subject={subject} name={name} members={members} swarms={swarms} />
      </div>
    </>
  );
}
