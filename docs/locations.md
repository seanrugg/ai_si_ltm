# Member locations

LTMS can show where a member is. Every location is returned with its
**source** and how uncertain it is, and the dashboard always shows both.
Locations, like addresses, are **admin-only** and are **never sent to an LRS**.

Nothing here makes an online lookup: it all works on a closed network.

## Three sources

| Source | From | Precision |
|---|---|---|
| `gps` | the member's newest sensed record whose payload has `"modality": "gps"` | high (the fix's `accuracy_m`, when sent) |
| `declared` | an admin sets it on the member, or on its swarm (members inherit it) | as declared |
| `network` | the member's newest **public** address LTMS saw (#189), looked up in an optional offline city database | city at best |

`best` is the first available of: gps, the member's declared location, its
swarm's declared location, network.

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

### Network estimate (optional)

LTMS estimates a city only when **both** are true:

1. the `geo` extra is installed: `pip install "ltms[geo]"` (adds `maxminddb`);
2. `LTMS_GEOIP_DB` points at a city database file in MaxMind DB format
   (`.mmdb`) on the LTMS host, for example `/opt/ltms/geo/city.mmdb`.

Download a free city database that **you are licensed to use** (several
providers publish free city-level databases in this format under their own
licence terms), copy it to the LTMS host, set `LTMS_GEOIP_DB`, and restart.
Without it there is simply no `network` entry, and no error.

Private, loopback and link-local addresses are never looked up. A member
behind a hub on a LAN usually has no public address, so it gets no network
estimate; declare its location instead.

**Cloud agents:** an agent running in a cloud shows its provider's data
center, not where a person is. LTMS doesn't special-case this; read a
network estimate as "where the traffic came from", never as a person's
location.
