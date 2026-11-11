# Third-party data and assets in the dashboard

Bundled at build time; the built dashboard makes no requests to external hosts.

| Package | What we use | License |
|---|---|---|
| [world-atlas](https://github.com/topojson/world-atlas) | `land-110m.json`, the world outline on the Network tab's globe | ISC |
| [Natural Earth](https://www.naturalearthdata.com/) | the source data behind world-atlas | public domain |
| [topojson-client](https://github.com/topojson/topojson-client) | converts the TopoJSON outline to GeoJSON | ISC |
| `@fontsource/*` (Atkinson Hyperlegible, JetBrains Mono, Bebas Neue, DM Sans, DM Mono) | fonts, bundled | SIL Open Font License 1.1 |
