---
name: "well-data"
description: "Pull public groundwater and well data for a US location: geocode an address, find nearby well completion records, list USGS monitoring sites, and query USDA soil properties. Use when siting or screening a residential water well, or answering 'could I put a well here?'."
---

# Well Data

## Purpose
Assemble the public data behind a residential well screening: where neighbors' wells are, how deep they go, what they yield, plus nearby USGS monitoring sites and USDA soil properties. Every source below is free and keyless, verified live 2026-09-18.

## Tooling
Python with geo stack: `python3` (geopandas 1.1.4, rasterio 1.5.1, requests).
Helper CLI: `bin/wellscreen.py "<address>" [--radius-mi N]` — geocodes, pulls CT wells in radius, counts USGS well sites, prints JSON.
Behavior contract (2026-09-30 fixes): **Connecticut only** — the Census match's structured state is verified; a non-CT address gets a `not_in_connecticut` error (exit 2), never a screen built from CT wells. Known CT village/hamlet mailing names (Moodus, Hazardville, Broad Brook, …) fall back to their parent town, and any fallback or renaming is disclosed in the output (`geocode_fallback` / `locality_note`) — never silent. Network calls retry 3× with backoff; persistent failure prints a structured `data_source_unavailable` error (exit 3) instead of a traceback; no geocode match exits 1 with a `no_geocode_match` error. Well counts are **unique properties** (`ct_wells_found`) with the raw state-filing count alongside (`ct_filings_found`) — duplicate filings for one property are merged — and every average carries its denominator (`ct_avg_*_based_on`, e.g. "9 of 11 unique properties reported a yield"). A null average means nobody reported the value; it is never estimated.

### 1. Geocode — US Census (no key)
```
https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?address={urlencoded}&benchmark=2020&format=json
```
`result.addressMatches[0].coordinates` → `x` = longitude, `y` = latitude. No match → ask the user for lat/lng or a cleaner address.

### 2. Nearby well records — Connecticut (Socrata SODA, no key)
Dataset `wphv-ux6v` ("Wells in Connecticut", ~8,205 rows, reports filed since 2021-01-01).
`latitude`/`longitude` are **text** fields — do not do numeric `$where` on them. `bin/wellscreen.py` caches the dataset under `data/` and filters locally; refresh the cache when older than 30 days.
Useful fields: `well_address`, `well_city`, `date_of_boringabandonment`, `casing_length` (ft), `casing_into_bedrock` (ft), `yield_test_result` (reported yield, gpm on standard tests), `yield_test_type`, `permit_links`, `map_links`.
Coverage gap: CT open data starts 2021. Older wells live in DCP/eLicense records, not this dataset — say so when results look thin.
Other states: `references/sources.md`.

### 3. USGS monitoring sites (OGC API, no key)
```
https://api.waterdata.usgs.gov/ogcapi/v0/collections/monitoring-locations/items?bbox={minx,miny,maxx,maxy}&limit=100
```
bbox order is **lng,lat**. Filter `properties.site_type == "Well"` client-side. Do not use legacy WaterServices (decommissioned Q1 2027).

### 4. USDA soils (SOAP, no key)
POST `https://sdmdataaccess.nrcs.usda.gov/Tabular/SDMTabularService.asmx`
Header: `SOAPAction: http://SDMDataAccess.nrcs.usda.gov/Tabular/SDMTabularService.asmx/RunQuery`
Body:
```xml
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>
<RunQuery xmlns="http://SDMDataAccess.nrcs.usda.gov/Tabular/SDMTabularService.asmx">
<Query>SELECT TOP 5 mukey, muname FROM mapunit</Query>
</RunQuery></soap:Body></soap:Envelope>
```
Response is a .NET DataSet diffgram — parse `NewDataSet/Table` rows. To go from a lat/lng to a `mukey`, use the Web Soil Survey spatial service (endpoint not yet verified — confirm before relying on it).

## Auth
None. All sources above are public and keyless. If one starts requiring a key, document it here.

## Operating Rules
1. Never invent well records. Every depth/yield figure comes from a live query or the local cache (which records its fetch date).
2. State the coverage limits with the numbers: CT open data starts 2021; locations are approximate (parcel-level in some states).
3. This screens prospects. It does not pick a drill spot — a licensed driller does that on site.
