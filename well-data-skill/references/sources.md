# Well-data sources by state

All verified during research 2026-09-18. Access methods vary: CT is the only one with a confirmed keyless JSON API; the rest are confirmed searchable web UIs, with programmatic access unverified unless noted.

| State | System | Records | Access |
|---|---|---|---|
| CT | data.ct.gov "Wells in Connecticut" (Socrata) | ~8,205, reports filed since 2021 | **Keyless SODA JSON API**: dataset `wphv-ux6v` |
| MA | EEA Data Portal + MassGIS Well Location Viewer | ~200,000 WCRs since 1963 | Web UI; locations approximate to parcel |
| PA | PaGWIS via PaGEODE | Updated daily | Web UI + CSV export; open-data portal |
| MI | EGLE Water Well Viewer | Updated daily | Web UI, no account |
| CA | DWR Well Completion Report Map App | n/a | Web UI + downloadable GIS layers |
| DE | DNREC Well Viewer | n/a | Web UI; pin-drop "Set Location" + 1,000-ft contamination review |
| WI | DNR Well Driller Viewer | n/a | Web UI (mobile) |
| FL | DOH EH Water (gis.flhealth.gov/ehwater) | n/a | Web UI |

## National compilations
- **USGWD** (academic, NSF): per-state CSV + shapefile, aquifer names, 12-digit HUCs. HydroShare DOI `10.4211/hs.8b02895f02c14dd1a749bcc5584a5c55`, CC BY. Static snapshot.
- **NGWA Water Well Map** (launched 2025-10-15): 14M+ existing wells. Public UI at WellOwner.org; programmatic API access unverified.

## Other useful layers
- **EPA sole-source aquifers**: interactive map + national GIS layer at `epa.gov/dwssa/map-sole-source-aquifer-locations`; ArcGIS services via EPA DWMAPS. Protection overlay, not a siting guide.
- **USGS 3DEP elevation**: DEMs 30m/10m/3m/1m, free, no account. Services: `viewer.nationalmap.gov/services/`; cloud-optimized GeoTIFFs on AWS.
- **USGS GeoLog Locator**: 7,000+ borehole geophysical logs, map-searchable.

## Key URLs
- CT dataset catalog: https://catalog.data.gov/dataset/wells-in-connecticut
- CT eLicense well reports: https://www.elicense.ct.gov/Lookup/OnlineReports.aspx?ID=340
- USGS API docs: https://api.waterdata.usgs.gov/docs/ogcapi/
- USDA Soil Data Access: https://sdmdataaccess.nrcs.usda.gov/
- Census geocoder: https://geocoding.geo.census.gov/geocoder/
