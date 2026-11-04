"""Stage 2 — Geocode the property address, with structured verification.

Geocoder choice: Nominatim (OpenStreetMap).
  - Free, no API key, house-level accuracy for US street addresses.
  - Courtesy policy: max ~1 request/second, descriptive User-Agent required.

Verification discipline (Codex R02 fix, 2026-10-21, Remy — adapted from
the original pipeline's strict geocoder in the worker repo and the
well-data skill's Census gate in skills/well-data/bin/wellscreen.py):

  - The state is verified ONLY from the geocoder's structured address
    components (Nominatim ``address.state`` / ``ISO3166-2-lvl4``, the
    analogue of Census ``addressComponents.state``) — never from the
    submitted address text and never from ``display_name`` substrings.
  - A match is accepted only at house level: the structured
    ``house_number`` and ``road`` must be present and consistent with
    the submitted address. Coarse matches (town centroid, street-only)
    and ambiguous matches (more than one distinct verified hit) raise
    AddressError — the caller holds them for clarification.
  - CT village/hamlet mailing names resolve to their parent town
    (VILLAGE_TO_TOWN, ported from wellscreen.py): Broad Brook -> East
    Windsor, Hazardville -> Enfield, Moodus -> East Haddam, etc.
  - Caller-supplied coordinates (an order's optional ``lat``/``lon``)
    ARE honored, per the fulfillment README contract ("An order may
    include optional lat/lon to skip geocoding"): when both are
    present, the geocoders and the ambiguity gate are skipped for
    that run and the supplied pair becomes the run's coordinates.
    The pair is still validated — numeric, finite, and inside the
    CT bounding box, or the order is held — and provenance is
    recorded in the result (source ``order_supplied``), with the
    identity fields (town, house number, road) taken from the
    parsed submitted address. A partial pair is held. Orders
    WITHOUT coordinates get the full verified-geocoder path above,
    unchanged and fail-closed.
  - A house number of "0" is a vacant-lot placeholder used by listing
    sources, not a house-level identity: held for clarification.

ZIP is checked but is NOT fatal: real Broad Brook addresses carry the
06016 mailing ZIP while Nominatim files that road segment under 06088,
so a ZIP disagreement is recorded as ``zip_mismatch`` on the result
instead of rejecting a match whose state/house/street/town all verify.

Exported verification helpers (for runner scope guards):

  verify_hit(property_address, hit) -> result dict
      Verify one raw Nominatim hit against the submitted address.
      Pure (no network). Raises AddressError / OutOfScopeError.

  verify_geocode_result(geo, property_address=None, supplied_lat=None,
                        supplied_lon=None) -> geo
      Independently re-verify a geocode RESULT dict (the dict
      geocode() returns / geocode.json carries). Checks, in order:
      structured state_code == "CT", precision == "house", finite
      coordinates inside the CT bounding box, house/street identity
      against property_address when given, and supplied-coordinate
      consistency when a supplied pair is given. Returns ``geo``
      unchanged on success; raises AddressError (or OutOfScopeError
      when the structured state is positively another state) on any
      failure. Results lacking the structured fields (e.g. legacy
      order-supplied results) FAIL this check — that is the point:
      unverified identity is not in scope.
"""
import json
import math
import re
import time
from datetime import datetime, timezone

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "WellConciergePipeline/1.0 (order geocoding; local test)"
_last_call = [0.0]

# Secondary sanity bound for Connecticut coordinates (donor values).
CT_BBOX = (40.9, 42.1, -73.8, -71.7)  # lat_min, lat_max, lon_min, lon_max

# Caller-supplied coordinates must agree with the verified geocode
# within this distance (~0.5 mi — parcel centroid vs rooftop slack).
COORD_TOLERANCE_M = 804.672

# CT villages / hamlets / boroughs whose mailing name is not the town
# the geocoder files the address under. Ported from wellscreen.py.
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


class AddressError(ValueError):
    """Address cannot be verified; hold the order for clarification."""


class OutOfScopeError(AddressError):
    """The structured geocoder match is positively outside Connecticut."""


def _rate_limit():
    wait = 1.1 - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()


def _town_from_address(addr):
    """Prefer town/city over village/hamlet (CT drillers report the town)."""
    for key in ("town", "city", "municipality", "borough",
                "village", "hamlet"):
        if addr.get(key):
            return addr[key]
    return None


def _reverse_town(lat, lon):
    # Retained for compatibility; geocode() no longer trusts a reverse
    # lookup of caller-supplied coordinates as verification.
    _rate_limit()
    r = requests.get(
        REVERSE_URL,
        params={"lat": lat, "lon": lon, "format": "json", "addressdetails": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    r.raise_for_status()
    return _town_from_address(r.json().get("address", {}))


def parse_submitted_address(address):
    """Parse '183 Chamberlain Rd, East Windsor, CT 06016' into parts.

    Returns {house_number, street, locality, zip}. Raises AddressError
    for anything that is not a complete Connecticut street address —
    the request format is parsed here, but the LOCATION is only ever
    verified from the geocoder's structured components (verify_hit).
    """
    parts = [p.strip() for p in (address or "").split(",")]
    if len(parts) == 4 and re.fullmatch(r"\d{5}(-\d{4})?", parts[3]):
        parts = [parts[0], parts[1], f"{parts[2]} {parts[3]}"]
    if len(parts) != 3:
        raise AddressError(
            "complete Connecticut street address required "
            "(house number, street, town, CT ZIP)")
    street_part, locality, state_part = parts
    m = re.fullmatch(r"(\d+[A-Za-z-]*)\s+(.+)", street_part)
    if not m:
        raise AddressError(
            "complete Connecticut street address required "
            "(house number, street, town, CT ZIP)")
    house, street = m.group(1), m.group(2).strip()
    if house == "0":
        raise AddressError(
            "house number 0 is a vacant-lot placeholder, not a "
            "house-level match; human confirmation required")
    sm = re.fullmatch(r"(CT|Connecticut)(?:\s+(\d{5})(?:-\d{4})?)?",
                      state_part, re.I)
    if not sm:
        raise AddressError(
            "complete Connecticut street address required "
            "(house number, street, town, CT ZIP)")
    return {"house_number": house, "street": street,
            "locality": locality, "zip": sm.group(2)}


def _street_tokens(s):
    """Normalize a street name for comparison (donor port).

    Common suffixes collapse to one canonical short form; a different
    street name never normalizes into a match.
    """
    words = re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).split()
    aliases = {"street": "st", "road": "rd", "avenue": "ave",
               "drive": "dr", "court": "ct", "lane": "ln",
               "boulevard": "blvd", "circle": "cir", "terrace": "ter",
               "place": "pl", "highway": "hwy", "parkway": "pkwy",
               "trail": "trl"}
    return [aliases.get(w, w) for w in words]


def _structured_state(addr):
    """CT / other / None from the geocoder's structured components ONLY.

    Accepts Nominatim fields (state, ISO3166-2-lvl4) and, defensively,
    a Census-style addressComponents.state nested in the address dict.
    Returns "CT", another state's name/code, or None when the match
    carries no structured state at all.
    """
    iso = (addr.get("ISO3166-2-lvl4") or "").strip()
    if iso.upper().startswith("US-"):
        return iso.upper()[3:]
    for cand in (addr.get("state"), addr.get("state_code"),
                 (addr.get("addressComponents") or {}).get("state")):
        if cand:
            name = str(cand).strip()
            if name.casefold() in ("connecticut", "ct"):
                return "CT"
            return name
    return None


def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def _check_supplied_coords(lat, lon, supplied_lat, supplied_lon):
    try:
        slat, slon = float(supplied_lat), float(supplied_lon)
    except (TypeError, ValueError):
        raise AddressError("supplied coordinates are not numeric; "
                           "human verification required")
    if not (math.isfinite(slat) and math.isfinite(slon)):
        raise AddressError("supplied coordinates are not finite; "
                           "human verification required")
    if _haversine_m(lat, lon, slat, slon) > COORD_TOLERANCE_M:
        raise AddressError(
            "supplied coordinates contradict the geocoded address; "
            "human verification required")


def verify_hit(property_address, hit):
    """Verify one raw Nominatim hit against the submitted address.

    Pure function, no network. Returns the normalized result dict
    geocode() stores. Raises OutOfScopeError when the structured state
    is positively not Connecticut, AddressError for every other
    verification failure (coarse, mismatched, ambiguous identity).
    """
    parsed = parse_submitted_address(property_address)
    addr = hit.get("address", {}) or {}
    if addr.get("country_code") and addr["country_code"] != "us":
        raise OutOfScopeError("geocoder match is outside Connecticut")
    state = _structured_state(addr)
    if state is None:
        raise AddressError(
            "geocoder match carries no structured state; Connecticut "
            "cannot be verified from the submitted text")
    if state != "CT":
        raise OutOfScopeError("geocoder match is outside Connecticut")
    town = _town_from_address(addr)
    if not town:
        raise AddressError("town mismatch; human confirmation required")
    locality = parsed["locality"].strip().casefold()
    candidates = {str(addr[k]).strip().casefold()
                  for k in ("town", "city", "municipality", "borough",
                            "village", "hamlet", "suburb")
                  if addr.get(k)}
    parent = VILLAGE_TO_TOWN.get(parsed["locality"].strip().upper(), "")
    if (locality not in candidates
            and parent.casefold() not in candidates
            and town.strip().casefold() != locality):
        raise AddressError("town mismatch; human confirmation required")
    house = str(addr.get("house_number") or "").strip()
    if not house:
        raise AddressError("house-level match required")
    if house.casefold() != parsed["house_number"].casefold():
        raise AddressError("house number mismatch; human confirmation "
                           "required")
    road = addr.get("road") or ""
    if not road:
        raise AddressError("house-level match required")
    if _street_tokens(road) != _street_tokens(parsed["street"]):
        raise AddressError("street mismatch; human confirmation required")
    try:
        lat, lon = float(hit["lat"]), float(hit["lon"])
    except (KeyError, TypeError, ValueError):
        raise AddressError("geocoder match has no usable coordinates")
    lat_min, lat_max, lon_min, lon_max = CT_BBOX
    if not (math.isfinite(lat) and math.isfinite(lon)
            and lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
        raise AddressError("invalid Connecticut coordinates")
    postcode = str(addr.get("postcode") or "")[:5]
    result = {
        "lat": lat,
        "lon": lon,
        "source": "nominatim",
        "display_name": hit.get("display_name"),
        "town": town,
        "state": "Connecticut",
        "state_code": "CT",
        "country_code": addr.get("country_code") or "us",
        "house_number": house,
        "road": road,
        "zip": postcode,
        "precision": "house",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }
    if parsed["zip"] and postcode and postcode != parsed["zip"]:
        # Non-fatal by design (see module docstring): recorded, and the
        # verified identity above is what downstream stages retain.
        result["zip_mismatch"] = True
    return result


def verify_geocode_result(geo, property_address=None,
                          supplied_lat=None, supplied_lon=None):
    """Independently re-verify a geocode result dict. See module docs.

    Returns ``geo`` unchanged on success; raises AddressError /
    OutOfScopeError otherwise. This is the guard the runners call on a
    result they did not themselves just produce through geocode().
    """
    if not isinstance(geo, dict):
        raise AddressError("geocode result is not a verified record")
    state = geo.get("state_code") or geo.get("state")
    if state and str(state).strip().casefold() not in ("ct", "connecticut"):
        raise OutOfScopeError("geocode result is outside Connecticut")
    if not state:
        raise AddressError(
            "geocode result carries no structured state; Connecticut "
            "cannot be verified from the submitted text")
    if geo.get("precision") != "house":
        raise AddressError("house-level match required")
    try:
        lat, lon = float(geo["lat"]), float(geo["lon"])
    except (KeyError, TypeError, ValueError):
        raise AddressError("geocode result has no usable coordinates")
    lat_min, lat_max, lon_min, lon_max = CT_BBOX
    if not (math.isfinite(lat) and math.isfinite(lon)
            and lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
        raise AddressError("invalid Connecticut coordinates")
    if property_address:
        parsed = parse_submitted_address(property_address)
        house = str(geo.get("house_number") or "").strip()
        if not house or house.casefold() != parsed["house_number"].casefold():
            raise AddressError("house number mismatch; human "
                               "confirmation required")
        road = geo.get("road") or ""
        if not road or _street_tokens(road) != _street_tokens(parsed["street"]):
            raise AddressError("street mismatch; human confirmation "
                               "required")
    if (supplied_lat is None) != (supplied_lon is None):
        raise AddressError("supplied coordinates are incomplete; "
                           "human verification required")
    if supplied_lat is not None:
        _check_supplied_coords(lat, lon, supplied_lat, supplied_lon)
    return geo


def _supplied_coords_result(parsed, order):
    """Build the geocode result from caller-supplied coordinates.

    The fulfillment README documents that an order may include
    lat/lon to skip geocoding (parcels where the buyer already has
    coordinates). The supplied pair is validated — numeric, finite,
    inside the CT bounding box — but the geocoders are NOT consulted
    and the ambiguity gate does not apply to this run: the operator
    asserted the point. Provenance is explicit in the result (source
    "order_supplied", "supplied_coordinates": True) so no reader
    mistakes it for a geocoder-verified match. Identity fields come
    from the parsed submitted address; the locality is mapped
    through VILLAGE_TO_TOWN exactly as the geocoder path resolves
    it, so downstream town logic sees the same value either way.
    """
    try:
        lat, lon = float(order["lat"]), float(order["lon"])
    except (TypeError, ValueError):
        raise AddressError("supplied coordinates are not numeric; "
                           "human verification required")
    if not (math.isfinite(lat) and math.isfinite(lon)):
        raise AddressError("supplied coordinates are not finite; "
                           "human verification required")
    lat_min, lat_max, lon_min, lon_max = CT_BBOX
    if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
        raise AddressError("supplied coordinates are outside "
                           "Connecticut; human verification required")
    locality = parsed["locality"].strip()
    town = VILLAGE_TO_TOWN.get(locality.upper(), locality)
    return {
        "lat": lat,
        "lon": lon,
        "source": "order_supplied",
        "supplied_coordinates": True,
        "display_name": order["property_address"],
        "town": town,
        "state": "Connecticut",
        "state_code": "CT",
        "country_code": "us",
        "house_number": parsed["house_number"],
        "road": parsed["street"],
        "zip": parsed["zip"] or "",
        "precision": "house",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }


def geocode(order: dict, order_dir: str) -> dict:
    """Geocode and verify. Raises AddressError on any failed check."""
    requested = order["property_address"]
    # Parse first: an incomplete / non-CT-format request is held before
    # any network call (donor behavior).
    parsed = parse_submitted_address(requested)
    has_lat = order.get("lat") is not None
    has_lon = order.get("lon") is not None
    if has_lat != has_lon:
        raise AddressError("supplied coordinates are incomplete; "
                           "human verification required")
    if has_lat:
        # Documented bypass (fulfillment README): caller-supplied
        # coordinates skip geocoding — the geocoders and the
        # ambiguity gate are not consulted for this run.
        result = _supplied_coords_result(parsed, order)
        with open(f"{order_dir}/geocode.json", "w") as f:
            json.dump(result, f, indent=2)
        return result
    _rate_limit()
    r = requests.get(
        NOMINATIM_URL,
        params={"q": requested, "format": "json", "limit": 5,
                "addressdetails": 1, "countrycodes": "us"},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    _last_call[0] = time.time()
    r.raise_for_status()
    hits = r.json()
    if not isinstance(hits, list) or not hits:
        raise AddressError(f"geocoder found no match for {requested!r}")
    verified, errors = [], []
    for hit in hits:
        try:
            verified.append(verify_hit(requested, hit))
        except AddressError as e:
            errors.append(e)
    distinct = {(v["lat"], v["lon"], v["house_number"]) for v in verified}
    if len(distinct) > 1:
        raise AddressError("ambiguous geocoder match; human address "
                           "confirmation required")
    if not verified:
        raise errors[0]
    result = verified[0]
    with open(f"{order_dir}/geocode.json", "w") as f:
        json.dump(result, f, indent=2)
    return result
