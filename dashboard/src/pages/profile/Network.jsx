// Profile Network tab: where members connect from (issues #189-#191, #195, #196).
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../../api/client.js";
import { useApi } from "../../api/useApi.js";
import { ErrorNote, Loading } from "../../components/ui.jsx";
import { HOME_ZOOM } from "../../lib/globe.js";
import { ratesFrom, sitesOf, unplacedOf } from "../../lib/sites.js";
import DataCard, { MemberFacts } from "./DataCard.jsx";
import Globe from "./Globe.jsx";
import HomeView from "./HomeView.jsx";
import NetworkTable from "./NetworkTable.jsx";

const POLL_MS = 10000;

/** Who isn't on the globe, and why (#346). */
export function Unplaced({ rows, geo }) {
  if (!rows.length) return null;
  return (
    <section className="mt-3 rounded-md border border-ink-line p-3 text-sm" aria-labelledby="unplaced-h">
      <h3 id="unplaced-h" className="font-bold">Not on the globe ({rows.length})</h3>
      <ul className="mt-1 space-y-1">
        {rows.map((r) => (
          <li key={r.name}><span className="font-bold">{r.name}</span>: <span className="text-paper-muted">{r.reason}</span></li>
        ))}
      </ul>
      {geo && !geo.enabled && (
        <p className="mt-2 text-xs text-paper-muted">
          Network estimates are off: {geo.reason}. See Settings → Location estimates.
        </p>
      )}
    </section>
  );
}

export default function Network({ subject, name, members }) {
  const params = { [subject]: name };
  const net = useApi("/admin/network", params);
  const locs = useApi("/admin/locations", params);
  const [card, setCard] = useState(null); // { type: "site", site } | { type: "member", name }
  const [mode, setMode] = useState("globe");
  const [view, setViewState] = useState(null);
  const [rates, setRates] = useState({});

  const memberRows = useMemo(() => members || [], [members]);
  const netRows = useMemo(() => net.data?.members || [], [net.data]);
  const locRows = useMemo(() => locs.data?.members || [], [locs.data]);
  const sites = useMemo(() => sitesOf(locRows, memberRows), [locRows, memberRows]);
  const home = sites.find((s) => s.home) || null;
  const server = locs.data?.server && locs.data.server.lat != null ? locs.data.server : null;
  const initial = useMemo(() => ({ lon: home?.lon ?? server?.lon ?? 0, lat: home?.lat ?? server?.lat ?? 20, zoom: 1 }),
    [home, server]);
  const current = view || initial;
  const setView = useCallback((v) => setViewState(v), []);

  // Records per minute per site, from a 10-second poll of the last hour.
  const siteKey = sites.map((s) => s.key).join(",");
  useEffect(() => {
    let live = true;
    async function poll() {
      const out = {};
      await Promise.all(sites.map(async (s) => {
        const q = s.home && subject === "swarm" ? { swarm: name } : { member: s.members[0] };
        try {
          out[s.key] = ratesFrom(await api("/admin/profile/summary", { params: { ...q, hours: 1 } }));
        } catch {
          out[s.key] = ratesFrom(null);
        }
      }));
      if (live) setRates(out);
    }
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => { live = false; clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [siteKey, subject, name]);

  if ((net.loading && !net.data) || (locs.loading && !locs.data)) return <Loading what="Loading the network" />;
  const error = net.error || locs.error;
  if (error) return <ErrorNote error={error} onRetry={() => { net.reload(); locs.reload(); }} />;

  const memberRow = (n) => memberRows.find((m) => m.name === n) || { name: n };
  const siteOf = (n) => sites.find((s) => s.members.includes(n));
  const openMember = (n) => setCard({ type: "member", name: n });

  let cardView = null;
  if (card?.type === "site") {
    cardView = (
      <DataCard title={card.site.label} onClose={() => setCard(null)}
                actions={card.site.home && (
                  <button type="button" className="btn" onClick={() => { setCard(null); setMode("home"); }}>Zoom into home</button>
                )}>
        <p className="mb-2 text-sm text-paper-muted">{card.site.note}</p>
        <ul className="text-sm">
          {card.site.members.map((n) => (
            <li key={n}><button type="button" className="underline" onClick={() => openMember(n)}>{n}</button></li>
          ))}
        </ul>
      </DataCard>
    );
  } else if (card?.type === "member") {
    const site = siteOf(card.name);
    cardView = (
      <DataCard title={card.name} onClose={() => setCard(null)}>
        <MemberFacts member={memberRow(card.name)} net={netRows.find((r) => r.name === card.name)}
                     loc={locRows.find((r) => r.name === card.name)} rates={site ? rates[site.key] : null} />
      </DataCard>
    );
  }

  return (
    <section aria-labelledby="network-h">
      <h2 id="network-h" className="mb-2 text-lg font-bold">Network</h2>
      <div className="relative">
        {mode === "home" && home ? (
          <HomeView site={home} netRows={netRows} locRows={locRows} members={memberRows} rates={rates[home.key]}
                    onSelectMember={openMember}
                    onExit={() => { setMode("globe"); setViewState({ lon: home.lon, lat: home.lat, zoom: HOME_ZOOM }); }} />
        ) : (
          <Globe view={current} setView={(v) => setView(v)} sites={sites} server={server} rates={rates} home={home}
                 onHome={() => setMode("home")} onSelectSite={(s) => setCard({ type: "site", site: s })} />
        )}
        {cardView}
      </div>
      {!server && (
        <p className="mt-2 text-xs text-paper-muted">
          Declare the server&apos;s location (a member location named <code>ltms-server</code>) to draw data flowing to it.
        </p>
      )}
      <Unplaced rows={unplacedOf(locRows)} geo={locs.data?.geo} />
      <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label="Sites and members">
        {sites.map((s) => (
          <button key={s.key} type="button" className="btn" onClick={() => setCard({ type: "site", site: s })}>
            {`Site: ${s.label}`}
          </button>
        ))}
        {netRows.map((r) => (
          <button key={r.name} type="button" className="btn" onClick={() => openMember(r.name)}>{r.name}</button>
        ))}
      </div>
      <NetworkTable netRows={netRows} locRows={locRows} members={memberRows} rates={rates} sites={sites}
                    onSelectMember={openMember} />
    </section>
  );
}
