#!/usr/bin/env python3
"""Screen a location for residential well prospects.

Geocodes an address (US Census, keyless), finds CT well completion records
within a radius (Socrata, keyless, cached locally), and counts USGS
monitoring well sites in the bounding box (OGC API, keyless).

Connecticut only: the geocoded match is verified against the structured
state in the Census response (never substring matching). A non-CT address
gets a plain "Connecticut only" error, never a screen built from CT wells.

Village names: CT villages/hamlets that Census doesn't know (Moodus) or
silently renames (Hazardville -> Enfield) are handled with a village ->
parent-town fallback, and any substitution or renaming is stated in the
output, never done silently.

Network calls retry a few times with backoff. If a source still doesn't
answer, the tool prints a clean structured error (JSON) instead of a
stack trace.

Exit codes: 0 ok, 1 no geocode match, 2 address not in Connecticut,
3 a public data source was unavailable after retries.

Usage: wellscreen.py "<address>" [--radius-mi 2.0] [--refresh-cache]
"""
import argparse
import datetime
import http.client
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(SKILL_DIR, "data", "ct_wells.json")
CACHE_DAYS = 30

GEOCODE_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
CT_URL = "https://data.ct.gov/resource/wphv-ux6v.json"
USGS_URL = "https://api.waterdata.usgs.gov/ogcapi/v0/collections/monitoring-locations/items"

# Sanity bound for Connecticut coordinates (secondary check only, used when
# a Census match carries no structured state component).
CT_BBOX = (40.95, 42.06, -73.76, -71.78)  # lat_min, lat_max, lng_min, lng_max

# CT villages / hamlets / boroughs whose mailing name is not the town the
# Census geocoder files the address under. mailing name -> parent town.
VILLAGE_TO_TOWN = {
    "MOODUS": "East Haddam",
    "HAZARDVILLE": "Enfield",
    "BROAD BROOK": "East Windsor",
    "TERRYVILLE": "Plymouth",
    "DANIELSON": "Killingly",
    "JEWETT CITY": "Griswold",
    "FORESTVILLE": "Bristol",
    "OAKVILLE": "Watertown",
    "PLANTSVILLE": "Southington",
    "COLLINSVILLE": "Canton",
    "HIGGANUM": "Haddam",
}


class DataSourceError(Exception):
    """A public data source did not answer after all retries."""


class NoMatchError(Exception):
    """The geocoder found no match for the address."""


class NotConnecticutError(Exception):
    """The geocoder matched the address, but not in Connecticut."""

    def __init__(self, matched_address, state):
        super().__init__(matched_address)
        self.matched_address = matched_address
        self.state = state


def get(url, params=None, timeout=30, attempts=3):
    """GET JSON with retries + backoff on transient network failures.

    Raises DataSourceError after the last attempt instead of leaking a
    raw urllib/http exception (and its traceback) to the caller.
    """
    if params:
        url += "?" + urllib.parse.urlencode(params)
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "well-data-skill/1.1"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except (urllib.error.URLError, http.client.HTTPException,
                TimeoutError, ConnectionError, ValueError) as e:
            # ValueError covers a truncated body that fails JSON parsing.
            last = e
            if i + 1 < attempts:
                time.sleep(1.5 * (i + 1))
    host = urllib.parse.urlsplit(url).netloc
    raise DataSourceError(
        "%s did not answer after %d tries (last error: %s)"
        % (host, attempts, type(last).__name__))


def _components(match):
    return match.get("addressComponents") or {}


def _in_ct_bbox(lat, lng):
    lat_min, lat_max, lng_min, lng_max = CT_BBOX
    return lat_min <= lat <= lat_max and lng_min <= lng <= lng_max


def _pick_ct_match(matches):
    """First match verified to be in Connecticut, or None.

    Primary evidence is the structured state in the Census match's
    addressComponents. Coordinates inside the CT bounding box are only a
    fallback for a match that carries no state component at all. All
    matches are scanned, so a border address whose first candidate sits
    across the state line still finds its CT match.
    """
    for m in matches:
        state = (_components(m).get("state") or "").upper()
        if state == "CT":
            return m
        if not state:
            c = m.get("coordinates") or {}
            if _in_ct_bbox(c.get("y", 0), c.get("x", 0)):
                return m
    return None


def _find_village(address):
    up = address.upper()
    for name in sorted(VILLAGE_TO_TOWN, key=len, reverse=True):
        if re.search(r"\b" + re.escape(name) + r"\b", up):
            return name
    return None


def _requested_locality(address):
    """The locality name the caller typed, uppercased, or None."""
    parts = [p.strip() for p in address.split(",")]
    if len(parts) < 2:
        return None
    last = parts[-1].upper()
    if re.search(r"\b(CT|CONNECTICUT)\b", last) or re.search(r"\d{5}", last):
        cand = parts[-2] if len(parts) >= 3 else parts[-1]
    else:
        cand = parts[-1]
    cand = re.sub(r"\b(CT|CONNECTICUT)\b", "", cand.upper())
    cand = re.sub(r"\d{5}(-\d{4})?", "", cand).strip(" ,")
    return cand or None


def _locality_notes(address, match):
    """Disclose it when Census filed the address under a different name."""
    requested = _requested_locality(address)
    matched_city = (_components(match).get("city") or "").upper()
    if not requested or not matched_city or requested == matched_city:
        return {}
    if VILLAGE_TO_TOWN.get(requested, "").upper() == matched_city:
        note = ("%s is a village/section of %s; the Census geocoder matched "
                "this address under %s. The screen uses %s."
                % (requested.title(), matched_city.title(),
                   matched_city.title(), matched_city.title()))
    else:
        note = ("The Census geocoder matched this address under '%s', not "
                "the locality name given ('%s')." % (matched_city.title(), requested.title()))
    return {"requested_locality": requested.title(),
            "matched_city": matched_city.title(),
            "locality_note": note}


def _village_fallback(address):
    """Retry the geocode with a known village name swapped for its town.

    Returns (lat, lng, matched_address, info) or None.
    """
    village = _find_village(address)
    if not village:
        return None
    town = VILLAGE_TO_TOWN[village]
    retry = re.sub(r"\b" + re.escape(village) + r"\b", town, address,
                   flags=re.IGNORECASE)
    if retry == address:
        return None
    d = get(GEOCODE_URL, {"address": retry, "benchmark": "2020", "format": "json"})
    m = _pick_ct_match(d["result"]["addressMatches"])
    if m is None:
        return None
    c = m["coordinates"]
    info = {"geocode_fallback": {
        "requested_locality": village.title(),
        "parent_town": town,
        "note": ("No geocode match for '%s' — %s is a village of %s, so the "
                 "address was retried and matched under %s."
                 % (village.title(), village.title(), town, town)),
    }}
    info.update(_locality_notes(retry, m))
    info.pop("locality_note", None)  # the fallback note already says it
    return c["y"], c["x"], m.get("matchedAddress", retry), info


def geocode(address):
    """Geocode and verify Connecticut. Returns (lat, lng, matched, info).

    Raises NoMatchError, NotConnecticutError, or DataSourceError.
    """
    d = get(GEOCODE_URL, {"address": address, "benchmark": "2020", "format": "json"})
    matches = d["result"]["addressMatches"]
    m = _pick_ct_match(matches)
    if m is not None:
        c = m["coordinates"]
        return c["y"], c["x"], m.get("matchedAddress", address), _locality_notes(address, m)
    # No verified-CT match. A known village name may still rescue the
    # address under its parent town before we give up or call it wrong-state.
    fb = _village_fallback(address)
    if fb is not None:
        return fb
    if matches:
        first = matches[0]
        raise NotConnecticutError(first.get("matchedAddress", address),
                                  (_components(first).get("state") or "").upper())
    raise NoMatchError(address)


def ct_wells(refresh=False):
    if refresh and os.path.exists(CACHE):
        os.remove(CACHE)
    if os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < CACHE_DAYS * 86400:
        # A torn or wrong-shape cache is a MISS, never an exception
        # (Codex R26): the file is shared (concurrent MCP calls) and
        # a partial write used to stay "fresh" for the whole window.
        try:
            with open(CACHE) as f:
                cached = json.load(f)
            if isinstance(cached, list):
                return cached, False
        except (ValueError, UnicodeDecodeError, OSError):
            pass
    rows, offset, batch = [], 0, 50000
    while True:
        chunk = get(CT_URL, {"$limit": batch, "$offset": offset})
        if not chunk:
            break
        rows += chunk
        if len(chunk) < batch:
            break
        offset += batch
    # Atomic publish (R26): write the complete cache to a temp file
    # in the same directory, fsync, then os.replace — readers only
    # ever see the old complete file or the new complete file.
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = f"{CACHE}.tmp-{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(rows, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, CACHE)
    return rows, True


def haversine_mi(lat1, lon1, lat2, lon2):
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _norm_addr(s):
    return re.sub(r"[^A-Z0-9]+", " ", (s or "").upper()).strip()


def _as_float(v):
    """A measurement is a finite number >= 0 (Codex R15, mirroring
    the fulfillment pipeline's _num): NaN, infinities and negatives
    are unknown, never data. Zero is preserved — a zero yield is
    evidence, not a missing value."""
    try:
        n = float(v)
        return n if math.isfinite(n) and n >= 0 else None
    except (TypeError, ValueError):
        return None


def _sane_date(s):
    """ISO date string, or "" when unparseable or in the future
    (R15: the source data contains an erroneous year-9202 record;
    a future date must not win 'newest reported value' selection
    or be published as fact)."""
    s = (s or "")[:10]
    try:
        d = datetime.date.fromisoformat(s)
    except ValueError:
        return ""
    return s if d <= datetime.date.today() else ""


def _group_properties(filings):
    """Merge duplicate state filings for the same property.

    The CT dataset is filings, not wells: one property can appear several
    times. Group by normalized street address + town (falling back to
    rounded coordinates when the address is blank). Each property keeps
    its newest filing for display and, per metric, the newest reported
    value — so one property never counts as several wells.
    """
    groups = {}
    for w in filings:
        key_addr = _norm_addr(w["address"])
        if key_addr:
            key = key_addr + "|" + _norm_addr(w["city"])
        else:
            key = "COORD|%.4f|%.4f" % (w["_lat"], w["_lng"])
        groups.setdefault(key, []).append(w)
    props = []
    for group in groups.values():
        group.sort(key=lambda w: w["date"] or "", reverse=True)  # newest first
        rep = dict(group[0])
        rep["filings_for_property"] = len(group)
        rep["distance_mi"] = min(w["distance_mi"] for w in group)
        for metric in ("casing_ft", "bedrock_ft", "yield_test_result"):
            rep[metric] = None
            for w in group:
                if _as_float(w.get(metric)) is not None:
                    rep[metric] = w[metric]
                    if metric == "yield_test_result":
                        rep["yield_test_type"] = w.get("yield_test_type")
                    break
        props.append(rep)
    props.sort(key=lambda w: w["distance_mi"])
    return props


def _avg_with_denominator(props, key):
    """Average over unique properties, always carrying its denominator."""
    vals = [v for v in (_as_float(p.get(key)) for p in props) if v is not None]
    return {
        "value": round(sum(vals) / len(vals), 1) if vals else None,
        "based_on_properties": len(vals),
        "of_properties": len(props),
    }


def _emit_error(kind, message, exit_code, **extra):
    payload = {"error": kind, "message": message}
    payload.update(extra)
    print(json.dumps(payload, indent=1))
    raise SystemExit(exit_code)


HICCUP = ("A public data service did not answer after several tries. "
          "That is a hiccup on their end, not a problem with the address. "
          "Wait a moment and try again.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("address")
    ap.add_argument("--radius-mi", type=float, default=2.0)
    ap.add_argument("--refresh-cache", action="store_true")
    a = ap.parse_args()

    try:
        lat, lng, matched, geo_info = geocode(a.address)
        rows, fetched = ct_wells(refresh=a.refresh_cache)
    except NoMatchError:
        _emit_error(
            "no_geocode_match",
            "The Census geocoder found no match for this address, so no "
            "screen was run. Check the address, or try the nearest town "
            "name if this is a village/hamlet address.",
            1, address=a.address)
    except NotConnecticutError as e:
        _emit_error(
            "not_in_connecticut",
            "This tool screens Connecticut addresses only. The address "
            "matched in %s, so no screen was run — Connecticut well "
            "records were not used for a non-Connecticut address."
            % (e.state or "another state"),
            2, address=a.address, matched_address=e.matched_address,
            matched_state=e.state or None)
    except DataSourceError:
        _emit_error("data_source_unavailable", HICCUP, 3, address=a.address)

    filings = []
    for w in rows:
        try:
            wlat, wlng = float(w["latitude"]), float(w["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        d = haversine_mi(lat, lng, wlat, wlng)
        if d <= a.radius_mi:
            filings.append({
                "distance_mi": round(d, 2),
                "address": w.get("well_address", ""),
                "city": w.get("well_city", ""),
                "date": _sane_date(w.get("date_of_boringabandonment")),
                "report_type": w.get("type_of_report"),
                "casing_ft": w.get("casing_length"),
                "bedrock_ft": w.get("casing_into_bedrock"),
                "yield_test_result": w.get("yield_test_result"),
                "yield_test_type": w.get("yield_test_type"),
                "_lat": wlat,
                "_lng": wlng,
            })
    # Cohort separation (Codex R25): report type is provenance, not
    # noise. The residential statistics describe water-supply
    # completions only — the same separation the fulfillment pipeline
    # implements. Abandonment and other filings are counted and
    # labeled separately below; they never enter the property groups
    # or the averages (an abandonment's casing/yield describe a
    # sealed well, not a residential completion).
    supply_filings = [f for f in filings
                      if f["report_type"] == "Water Supply Well"]
    abandonment_filings = [f for f in filings
                           if f["report_type"] == "Abandonment"]
    other_filings = [f for f in filings
                     if f["report_type"] not in
                     ("Water Supply Well", "Abandonment")]
    properties = _group_properties(supply_filings)
    for p in properties:
        p.pop("_lat", None)
        p.pop("_lng", None)

    pad = a.radius_mi / 69.0  # approx degrees latitude
    usgs_count, usgs_note = None, None
    try:
        usgs = get(USGS_URL, {"bbox": "%f,%f,%f,%f" % (lng - pad, lat - pad, lng + pad, lat + pad),
                              "limit": 100})
        usgs_wells = [f["properties"]["monitoring_location_number"]
                      for f in usgs.get("features", [])
                      if f.get("properties", {}).get("site_type") == "Well"]
        usgs_count = len(usgs_wells)
    except DataSourceError:
        usgs_note = ("The USGS lookup failed after retries, so the USGS well "
                     "site count is unavailable — it is not zero.")

    casing = _avg_with_denominator(properties, "casing_ft")
    yld = _avg_with_denominator(properties, "yield_test_result")

    out = {
        "matched_address": matched,
        "lat": round(lat, 5),
        "lng": round(lng, 5),
        "radius_mi": a.radius_mi,
        "ct_cache_refreshed": fetched,
        "ct_filings_found": len(filings),
        "ct_abandonment_filings_found": len(abandonment_filings),
        "ct_other_filings_found": len(other_filings),
        "ct_wells_found": len(properties),
        "ct_avg_casing_ft": casing["value"],
        "ct_avg_casing_based_on": "%d of %d unique properties reported a casing length"
                                  % (casing["based_on_properties"], casing["of_properties"]),
        "ct_avg_yield": yld["value"],
        "ct_avg_yield_based_on": "%d of %d unique properties reported a yield"
                                 % (yld["based_on_properties"], yld["of_properties"]),
        "usgs_well_sites_in_bbox": usgs_count,
        "ct_wells": properties[:50],
        "coverage_note": "CT open data covers reports filed since 2021-01-01 only.",
        "evidence_note": ("Well counts are unique properties: duplicate state "
                          "filings for the same property are merged (the raw "
                          "filing count is in ct_filings_found). Counts and "
                          "averages cover Water Supply Well completions only; "
                          "abandonment and other report types are counted "
                          "separately (ct_abandonment_filings_found, "
                          "ct_other_filings_found) and never enter the "
                          "residential statistics. Averages use "
                          "the newest reported value per property and state "
                          "how many properties they are based on; a null "
                          "average means no property reported that value."),
    }
    out.update(geo_info)
    if usgs_note:
        out["usgs_note"] = usgs_note
    # allow_nan=False: serialize only finite JSON (R15) — a leaked
    # non-finite value fails loudly here instead of corrupting the
    # consumer's parse downstream.
    print(json.dumps(out, indent=1, allow_nan=False))


if __name__ == "__main__":
    main()
