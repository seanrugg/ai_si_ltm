# Member locations

LTMS can show where a member is. Every location is returned with its
**source** and how uncertain it is, and the dashboard always shows both.
Locations, like addresses, are **admin-only** and are **never sent to an LRS**
or included in exports. That includes the IP address behind a network estimate.

Nothing here makes an online lookup: it all works on a closed network.

## Three sources

| Source | From | Precision |
|---|---|---|
| `gps` | the member's newest sensed record whose payload has `"modality": "gps"` | high (the fix's `accuracy_m`, when sent) |
| `declared` | an admin sets it on the member, or on its swarm (members inherit it) | as declared |
| `network` | the member's newest **public** address LTMS saw (#189), looked up in an optional offline city database | city at best |

`best` is the first available of: gps, the member's declared location, its
swarm's declared location, network.

### Members behind a hub, and members with nothing (#346)

A member on a local network (a robot behind the swarm's hub) only ever shows
LTMS a private address, which can't be located. So:

- **Via hub.** If its own address can't be located, it takes the network
  estimate of the hub that relayed its newest record (`via_hub` in the entry).
- **Swarm estimate.** If it still has nothing, and its swarm has no declared
  home, it inherits the swarm's network estimate: the hub's, else the first
  member's (`inherited_from: "swarm <name> (network estimate from <member>)"`).
  Declaring the swarm's home replaces this.
- **Unplaced.** A member with no position gets an `unplaced` sentence saying
  why (only a LAN address seen, estimates off, never connected directly). The
  Geography tab gathers these in its "Unplaced" ring and table, with any
  address taken out of the reason.

### The address behind a network estimate (#399)

A `network` entry carries `ip`, the "seen" address that was looked up, and
`ip_seen_at`, when LTMS last saw it, so an admin can check the estimate. A
relayed estimate (`via_hub`) carries **the hub's** address. Declared and GPS
entries carry no address. Estimates are worked out live from the addresses
still kept, so an address removed by the address-retention prune is no longer
shown; the Members page then says "estimate (address no longer kept)".

`GET /api/admin/locations` also returns `geo`: `{enabled, path, reason}`, shown
on Settings → Location estimates, so it's clear when estimates are off.

### GPS

Send GPS fixes as sensed records (a `perception` tagged `telemetry`; see
[sensing.md](sensing.md)) with `"modality": "gps"` and samples whose `v` is
`{"lat", "lon"}`:

```json
{"kind": "sensor", "sensor": "gps", "modality": "gps",
 "samples": [{"t": 0, "v": {"lat": 0.0, "lon": 0.0, "accuracy_m": 5}}]}
```

### Declared

On the dashboard, or with the API:

```
PUT /api/admin/locations/member/<name>   {"label": "<building>", "room": "<room>", "lat": <lat>, "lon": <lon>}
PUT /api/admin/locations/swarm/<swarm>   {"label": "<site>"}
DELETE /api/admin/locations/member/<name>
```

`lat` and `lon` are optional (give both or neither). Changes are audited
(`location_change`).

The LTMS server's own location is a declared member entry named
`ltms-server` (no member needs that name): the dashboard's globe draws it as
the site every member's data flows to.

```
PUT /api/admin/locations/member/ltms-server   {"label": "<server room>", "lat": <lat>, "lon": <lon>}
```

### Network estimate (optional)

LTMS estimates a city from a member's public address using a **city database
file kept on the LTMS server** (MaxMind DB format, `.mmdb`). Nothing is ever
looked up online: member addresses never leave the server.

**Turn it on in the dashboard (#388):** Settings → Location estimates.

- **Turn on network estimates (download DB-IP City Lite).** LTMS downloads the
  current monthly [DB-IP IP to City Lite](https://db-ip.com/db/download/ip-to-city-lite)
  file (CC BY 4.0, no account or key) from `download.db-ip.com` only, over
  https, with size and time limits. It decompresses it with a cap, checks that
  it opens as a city database, and swaps it into `<data_dir>/geoip/city.mmdb`.
  DB-IP only learns that this server fetched the file.
- **Monthly refresh.** In download mode a scheduled job fetches a new file every
  `LTMS_GEOIP_REFRESH_DAYS` (default 30; `0` turns it off). A file that fails
  the checks is never installed: the previous one stays, and the panel shows
  the error.
- **Upload** an `.mmdb` or `.mmdb.gz` for closed networks or another
  provider's file: download it by hand, then upload it and say who provides it
  and under what licence. Uploaded files aren't refreshed.
- **Turn off** stops estimates. The file is kept unless you delete it (the
  panel asks first).

Every change is in the audit log (`geoip_update`, `geoip_off`).

**Attribution:** while DB-IP's file is in use, "IP geolocation by DB-IP" (a link
to https://db-ip.com) is shown in the Location estimates panel, on the Network
view and on member cards that show a network estimate, as its licence asks.

**Environment override:** `LTMS_GEOIP_DB` still works. When it is set, it wins,
and the dashboard shows "set by the server's environment" with its controls
off. `maxminddb` is a base dependency; `pip install "ltms[geo]"` still works
and adds nothing.

Without a database there is simply no `network` entry, and no error.

Private, loopback and link-local addresses are never looked up. A member
behind a hub on a LAN usually has no public address, so it gets no network
estimate; declare its location instead.

**Cloud agents:** an agent running in a cloud shows its provider's data
center, not where a person is. LTMS doesn't special-case this; read a
network estimate as "where the traffic came from", never as a person's
location.
