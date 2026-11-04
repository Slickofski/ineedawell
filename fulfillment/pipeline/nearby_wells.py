"""Stage 3 — Nearby well completions from the CT DCP Socrata dataset.

Source: https://data.ct.gov/resource/wphv-ux6v.json ("Wells in Connecticut").
Covers reports submitted online since 2021-01-01; older reports exist only as
scanned PDFs by town/year, so this is a recent sample, not a census.
Nulls are normal: many records lack depth/yield fields.

Deep-end anchor: the local 3 km sample can be tiny (2-4 wells) and miss the
deep tail. We also compute the town-wide p90 depth (same dataset, driller-
reported well_city matched case-insensitively) so the cost band's worst case
is max(local max, town p90) instead of local max alone.

At the demo parcel (193 Wells Rd) this stage historically returns
25 records within 3 km, 14 with depth/yield data.
"""
import json
import math
import os
import re
import statistics
from collections import Counter
from datetime import date

import requests

SOCRATA_URL = "https://data.ct.gov/resource/wphv-ux6v.json"
USER_AGENT = "WellConciergePipeline/1.0 (nearby well lookup; local test)"
RADIUS_M = 3000
DATASET_START = "2021-01-01"

# Plain words for the driller-filed yield-test methods (CX pass,
# 2026-10-29): the snapshot's Strength check used to print the raw
# dataset terms ("methods: Bailed, Compressed Air"); the packet has
# always mapped them. One map, here, shared by both surfaces.
METHOD_PLAIN = {"Compressed Air": "compressed air", "Bailed": "bailing",
                "Pumped": "a pump"}

# One pull per property (CX pass, 2026-10-29; audit Finding 8): every
# flow used to issue its own live Socrata pull, so the snapshot and
# the packet for the SAME house shipped different well counts and
# storage figures. Pulls are now stored per property under
# <fulfillment>/pulls/ and reused by every later flow for that
# property; each order's own nearby_wells.json is still written (a
# copy), so per-order consumers and cleanup semantics don't change.
PULLS_DIRNAME = "pulls"
# A stored pull is reused when the normalized property address
# matches exactly, or when the geocoded point lands within this
# distance of the point the stored pull was made for (two geocodes
# of one parcel land within a few meters; a different parcel's does
# not land on this lot).
PULL_MATCH_M = 10.0


def _norm_addr(text) -> str:
    return " ".join((text or "").lower().split())


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180.0
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p)
         * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 2 * 6371000.0 * math.asin(math.sqrt(a))


def _pulls_dir(base_dir: str) -> str:
    return os.path.join(base_dir, PULLS_DIRNAME)


def find_shared_pull(base_dir: str, order: dict, geo: dict):
    """The stored pull for this property, or None. Scans the pulls
    store (pilot scale: a handful of files). A candidate matches on
    normalized property address, or on geocode proximity alone when
    either side lacks a comparable address."""
    store = _pulls_dir(base_dir)
    if not os.path.isdir(store):
        return None
    want_addr = _norm_addr(order.get("property_address"))
    lat, lon = geo.get("lat"), geo.get("lon")
    for name in sorted(os.listdir(store)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(store, name)) as f:
                stored = json.load(f)
        except (OSError, ValueError):
            continue
        if stored.get("radius_m") != RADIUS_M:
            continue
        have_addr = _norm_addr(stored.get("pull_property_address"))
        if want_addr and have_addr and want_addr == have_addr:
            return stored
        plat, plon = stored.get("pull_lat"), stored.get("pull_lon")
        if (lat is not None and lon is not None
                and plat is not None and plon is not None
                and _haversine_m(lat, lon, plat, plon) <= PULL_MATCH_M):
            return stored
    return None


def save_shared_pull(base_dir: str, order: dict, geo: dict,
                     result: dict) -> None:
    """Persist a fresh pull as the property's shared pull. Best
    effort: a store write failure never breaks fulfillment — the
    per-order copy is already written by nearby_wells()."""
    try:
        store = _pulls_dir(base_dir)
        os.makedirs(store, exist_ok=True)
        key = f"{geo['lat']:.5f}_{geo['lon']:.5f}".replace("-", "m")
        with open(os.path.join(store, f"{key}.json"), "w") as f:
            json.dump(result, f, indent=2)
    except (OSError, TypeError, ValueError):
        pass


def _num(v):
    # Ported from the donor pipeline (Codex R01 fix, 2026-10-21): NaN,
    # infinities and negatives are not measurements — they are unknown.
    # Zero is a real measurement and is preserved (a zero yield is
    # evidence, not a missing value).
    try:
        n = float(v)
        return n if math.isfinite(n) and n >= 0 else None
    except (TypeError, ValueError):
        return None


def _p90(vals):
    vals = sorted(vals)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    return statistics.quantiles(vals, n=100, method="inclusive")[89]


def _sane_record_date(rec):
    """The record's date as an ISO string, or None when unparseable
    or in the future (Codex R15: the dataset contains an erroneous
    year-9202 record; a future date is a typo, not a fact). The
    record and its measurements still count — only the date is
    neutralized."""
    raw = (rec.get("date_of_boringabandonment") or "")[:10]
    if not raw:
        return None
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        return None
    return raw if d <= date.today() else None


def _recent(rec):
    """Keep records with no date or a date on/after the dataset's start.

    The dataset is nominally reports filed since 2021-01-01, but pre-2021
    backfills exist; this excludes the ones we can date as too old.
    """
    dt = (rec.get("date_of_boringabandonment") or "")[:10]
    return not dt or dt >= DATASET_START


def _town_depth_stats(town_upper):
    """Town-wide depth stats from the same CT dataset (for the deep-end anchor)."""
    safe = re.sub(r"[^A-Z0-9 \-]", "", town_upper)
    r = requests.get(
        SOCRATA_URL,
        params={
            "$where": f"upper(well_city)='{safe}'",
            "$select": "depth_of_well,date_of_boringabandonment,type_of_report",
            "$limit": 5000,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    r.raise_for_status()
    depths = [
        v for rec in r.json()
        if rec.get("type_of_report") == "Water Supply Well"
        and _recent(rec)
        and (v := _num(rec.get("depth_of_well"))) is not None
    ]
    # Abandonment signal (2026-10-16, rules package step 4): town-wide type
    # counts from the same query. Abandonments are CONTEXT, never depth/yield
    # evidence — a town sealing wells as public water arrives looks like
    # "no wells" unless the abandonments are counted out loud.
    recent = [rec for rec in r.json() if _recent(rec)]
    type_counts = {
        "water_supply_n": sum(1 for rec in recent
                              if rec.get("type_of_report") == "Water Supply Well"),
        "abandonment_n": sum(1 for rec in recent
                             if rec.get("type_of_report") == "Abandonment"),
    }
    if not depths:
        return {"p90": None, "median": None, "n": 0, **type_counts}
    return {"p90": _p90(depths), "median": statistics.median(depths),
            "n": len(depths), **type_counts}


def strength_summary(well_dicts):
    """Shared well-test summary (2026-10-20; rewritten for Codex R03,
    2026-10-21).

    ONE definition for every consumer — the internal drawdown block in
    nearby_wells(), the packet's well-test section (packet.py), and the
    snapshot's test line (screen.py).

    R03: a filing records ONE endpoint — the water level at one moment
    during the test — with no stabilization or recovery measurement.
    That endpoint cannot establish sustained production, so this summary
    makes no sustained-yield classification at all: sustained yield is
    "unestablished" for every filing, and consumers publish the recorded
    levels, method and duration neutrally. (The earlier "worked to the
    bottom / held" inference class was removed, not reworded.)

    Impossible levels are rejected into "invalid" and never reach the
    valid trio: a during-test level shallower than the resting (static)
    level (the water cannot rise while the well is produced), or a
    during-test level deeper than the well bottom.
    """
    def _invalid_reason(w):
        static, pumping = w.get("static_level_ft"), w.get("pumping_level_ft")
        if static is None or pumping is None:
            return None
        if pumping < static:
            return ("during-test level shallower than the resting level")
        depth = w.get("depth_ft")
        if depth is not None and pumping > depth:
            return "during-test level below the well bottom"
        return None

    with_levels = [w for w in well_dicts
                   if w.get("static_level_ft") is not None
                   and w.get("pumping_level_ft") is not None]
    invalid = [w for w in with_levels if _invalid_reason(w)]
    valid_levels = [w for w in with_levels if not _invalid_reason(w)]
    trio = [w for w in valid_levels if (w.get("yield_gpm") or 0) > 0]
    judged = [w for w in trio
              if w.get("depth_ft") is not None
              and w["depth_ft"] > w["static_level_ft"]]

    def _bottomed(w):
        # Endpoint geometry ONLY (where the recorded level sat relative
        # to the bottom) — retained for the internal counts in
        # screen.json; it is not a sustained-production finding and no
        # customer-facing text may narrate it as one.
        avail = w["depth_ft"] - w["static_level_ft"]
        return (w["pumping_level_ft"] - w["static_level_ft"]) >= 0.9 * avail

    return {
        "trio": trio,
        "valid": trio,
        "invalid": invalid,
        "invalid_reasons": {id(w): _invalid_reason(w) for w in invalid},
        "sustained_yield": "unestablished",
        "judged": judged,
        "bottomed": [w for w in judged if _bottomed(w)],
        "held": [w for w in judged if not _bottomed(w)],
        "pumped": [w for w in trio if w.get("yield_test_type") == "Pumped"],
        "method_counts": Counter(w.get("yield_test_type") for w in trio
                                 if w.get("yield_test_type")),
        "hours": [w["yield_test_hours"] for w in trio
                  if w.get("yield_test_hours") is not None],
    }


GAL_PER_FT_6IN_BORE = 1.47
MAX_SANE_COLUMN_FT = 1500.0
THIN_COLUMN_FT = 50.0


def storage_summary(well_dicts):
    """Borehole-storage summary (2026-10-24, borehole-storage fix
    Part 1 — the sentence-level companion to strength_summary).

    A well's bore is a storage tank: the water column standing in it
    (drilled depth minus the resting/static level) is a reserve the
    household draws against while the aquifer refills. Storage =
    column x 1.47 gal/ft for a standard 6-inch bore. Unlike the yield
    test's during-test level, the two inputs are geometry and a
    resting measurement, so the figure is NOT contaminated by the
    compressed-air/bailed test methods that killed specific capacity
    (audit, hidden_files/borehole-storage-audit-2026-10-24).

    Honesty rules, same discipline as strength_summary's "invalid":
    a well enters the statistic ONLY with a sane pair (both fields
    present, 0 < static < depth). A missing resting level means the
    storage is UNKNOWN — the well is counted as missing, never filled
    and never zeroed. Pairs that present data but fail sanity
    (resting level at or below zero, resting level at or beyond the
    drilled depth, or a column over MAX_SANE_COLUMN_FT — a filing
    error, not a well) are excluded and counted in
    "excluded_impossible".
    Returns medians only when at least one sane pair exists, else
    explicit nulls.
    """
    columns = []
    missing = 0
    excluded_impossible = 0
    for w in well_dicts:
        depth = _num(w.get("depth_ft"))
        static = _num(w.get("static_level_ft"))
        if depth is None or static is None:
            missing += 1
            continue
        column = depth - static
        if static <= 0 or column <= 0 or column > MAX_SANE_COLUMN_FT:
            excluded_impossible += 1
            continue
        columns.append(column)
    if columns:
        median_column = statistics.median(columns)
        median_storage = round(
            statistics.median([c * GAL_PER_FT_6IN_BORE for c in columns]))
        thin = median_column < THIN_COLUMN_FT
    else:
        median_column = None
        median_storage = None
        thin = None
    return {
        "wells_with_column": len(columns),
        "missing": missing,
        "excluded_impossible": excluded_impossible,
        "excluded_total": missing + excluded_impossible,
        "median_column_ft": median_column,
        "median_storage_gal": median_storage,
        "thin": thin,
    }


def nearby_wells(order: dict, geo: dict, order_dir: str,
                 base_dir: str | None = None) -> dict:
    lat, lon = geo["lat"], geo["lon"]
    # One pull per property (CX pass, 2026-10-29): with a base_dir,
    # a stored pull for this property is reused verbatim — every
    # surface of every flow for the house then reads the same wells,
    # counts and storage figures. The per-order nearby_wells.json is
    # still written (a copy of the shared pull). Without a base_dir
    # the stage behaves exactly as before (fresh live pull).
    if base_dir:
        shared = find_shared_pull(base_dir, order, geo)
        if shared is not None:
            shared = dict(shared)
            shared["pull_reused_for"] = order.get("order_id")
            with open(f"{order_dir}/nearby_wells.json", "w") as f:
                json.dump(shared, f, indent=2)
            return shared
    where = (
        f"within_circle(geolocation, {lat}, {lon}, {RADIUS_M}) AND "
        f"(date_of_boringabandonment >= '{DATASET_START}T00:00:00' "
        f"OR date_of_boringabandonment IS NULL)"
    )
    r = requests.get(
        SOCRATA_URL,
        params={"$where": where, "$limit": 5000},
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    r.raise_for_status()
    records = r.json()

    # Abandonment signal (2026-10-16, rules package step 4): count ALL
    # record types in the radius, not just water-supply wells. A bare
    # "0 wells" can misread as "no water here" when the record actually
    # holds sealed wells — for reasons the filings don't state (R21) —
    # and PHC 19-13-B51m can bar a new well permit within 200 ft of an
    # approved public supply. Abandonments are context for the verdict,
    # never depth/yield evidence.
    abandonments_in_radius = sum(
        1 for rec in records if rec.get("type_of_report") == "Abandonment"
    )

    wells = []
    unusable_dates = 0
    for rec in records:
        if rec.get("type_of_report") != "Water Supply Well":
            continue
        if (rec.get("date_of_boringabandonment")
                and _sane_record_date(rec) is None):
            unusable_dates += 1
        depth = _num(rec.get("depth_of_well"))
        if depth == 0:
            depth = None  # donor rule: a 0-ft depth is a missing depth
        yld = _num(rec.get("yield_test_result"))
        static = _num(rec.get("water_depth_from_surface"))
        city = (rec.get("well_city") or "").strip()
        addr = " ".join(x for x in [rec.get("well_address"), city] if x)
        wells.append(
            {
                "address": addr or "address not listed",
                "city": city,
                "depth_ft": depth,
                "yield_gpm": yld,
                "static_level_ft": static,
                # Drawdown capture (2026-10-20, Marcin approved background-only):
                # the driller-filed water level during the yield test, plus the
                # test method and duration. Recorded for the internal
                # drawdown_analysis block below — NOT rendered in the screen,
                # packet, or report.
                "pumping_level_ft": _num(rec.get("water_during_test")),
                "yield_test_type": rec.get("yield_test_type") or None,
                "yield_test_hours": _num(rec.get("yield_test_hours")),
                "company": rec.get("drilling_company"),
                "date": _sane_record_date(rec),
            }
        )

    # Drawdown / specific-capacity analysis (2026-10-20) — INTERNAL ONLY.
    # Specific capacity = yield / drawdown (gpm per ft). Data check the same
    # day found the trio (yield, static level, during-test level) on ~98% of
    # CT records, BUT the during-test level is method-contaminated: for
    # compressed-air and bailed tests (~94% of records) the well is typically
    # blown/bailed to the bottom, so drawdown approximates drilled depth, not
    # a controlled pump-test drawdown. This block exists so the raw material
    # accumulates for a future, human-approved use; per Marcin's direction
    # nothing customer-facing reads it. Full record:
    # hidden_files/specific-capacity-data-check-2026-10-20.md
    _trio = strength_summary(wells)["trio"]
    _sc = sorted(
        w["yield_gpm"] / (w["pumping_level_ft"] - w["static_level_ft"])
        for w in _trio
        if w["pumping_level_ft"] - w["static_level_ft"] > 0
        and (w["depth_ft"] is None or w["pumping_level_ft"] <= w["depth_ft"])
    )
    # Slimmed at Marcin's direction (2026-10-20): this block keeps ONLY what
    # exists nowhere else — the specific-capacity figures themselves and the
    # warning label explaining why they are not published. The counts (trio,
    # methods, worked-to-bottom) were dropped: the packet and the snapshot
    # recompute those from the per-well records via strength_summary, and a
    # stored duplicate nobody reads is dead weight, not a cross-check.
    drawdown_analysis = {
        "status": ("internal_only — captured 2026-10-20 at Marcin's direction; "
                   "not rendered in the screen, packet, or report, and never "
                   "feeds the verdict"),
        "method_note": (
            "water_during_test is the driller-filed water level during the "
            "yield test. For compressed-air and bailed tests (the large "
            "majority) the well is typically blown/bailed to the bottom, so "
            "drawdown approximates depth below static, not a controlled "
            "pump-test drawdown. Specific capacity from these records is "
            "method- and depth-contaminated — never quote it as a precise "
            "figure or use it to reassure or alarm."
        ),
        "specific_capacity_gpm_per_ft": (
            {"n": len(_sc), "min": round(_sc[0], 3),
             "median": round(statistics.median(_sc), 3),
             "max": round(_sc[-1], 3)}
            if _sc else None
        ),
    }

    # Separate counts (donor pipeline, Codex R01 fix 2026-10-21): depth
    # evidence and yield evidence are counted independently. The old
    # code derived yields only from depth-bearing records, which dropped
    # zero-yield records without depth and let depth-only records pose
    # as yield evidence. Zero yields are preserved as real values.
    depths = [w["depth_ft"] for w in wells if w["depth_ft"] is not None]
    yields = [w["yield_gpm"] for w in wells if w["yield_gpm"] is not None]
    paired = sum(1 for w in wells
                 if w["depth_ft"] is not None and w["yield_gpm"] is not None)

    # Town anchor: the property's town from the geocoder (drillers report
    # well_city as the town, so this matches the dataset's vocabulary).
    # Falls back to the most common driller-reported city among local wells
    # when the geocoded town has too few records to anchor on.
    # R16: the anchor is a STATISTICAL device only. It is tracked as
    # stats_town and never overwrites the property's town — jurisdiction
    # consumers (permit authority, roadmap) must see the property town,
    # and statistical consumers must label the anchor's numbers with the
    # anchor's name.
    property_town = geo.get("town")
    anchor_town, town_stats, thin_fallback = None, None, None
    candidates = []
    if property_town:
        candidates.append(property_town)
    cities = Counter(w["city"].upper() for w in wells if w.get("city"))
    if cities:
        candidates.append(cities.most_common(1)[0][0].title())
    for cand in dict.fromkeys(candidates):  # de-dupe, keep order
        try:
            stats = _town_depth_stats(cand.upper())
        except Exception:
            continue  # town anchor is advisory; never break the pipeline
        if stats and thin_fallback is None:
            thin_fallback = (cand, stats)
        if stats and stats["n"] >= 5:
            anchor_town, town_stats = cand, stats
            break
    if town_stats is None and thin_fallback is not None:
        anchor_town, town_stats = thin_fallback

    def rng(vals):
        return {"min": min(vals), "max": max(vals),
                "median": statistics.median(vals)} if vals else None

    result = {
        "radius_m": RADIUS_M,
        # Pull provenance (CX pass, 2026-10-29): surfaces print the
        # pull date so a later re-pull reads as an update, never a
        # contradiction; the store matches on address / point.
        "pulled_at": date.today().isoformat(),
        "pull_lat": lat,
        "pull_lon": lon,
        "pull_property_address": order.get("property_address"),
        "pull_origin": order.get("order_id"),
        "source": "CT DCP Socrata wphv-ux6v (online submissions since 2021-01-01)",
        "total_water_supply_wells": len(wells),
        # Corrected semantics (R01): this key now counts records carrying
        # BOTH depth and yield. It previously counted depth-only records,
        # which is what let depth pose as yield evidence downstream.
        "wells_with_depth_yield": paired,
        "depth_count": len(depths),
        "yield_count": len(yields),
        "paired_count": paired,
        "zero_yield_count": yields.count(0),
        "missing_yield_count": len(wells) - len(yields),
        "missing_depth_count": len(wells) - len(depths),
        "records_in_radius": len(records),
        "records_with_unusable_dates": unusable_dates,
        "abandonment_records_in_radius": abandonments_in_radius,
        "town_water_supply_count": (town_stats or {}).get("water_supply_n"),
        "town_abandonment_count": (town_stats or {}).get("abandonment_n"),
        "drawdown_analysis": drawdown_analysis,
        "depth_ft": rng(depths),
        "yield_gpm": rng(yields),
        "town": property_town,
        "stats_town": anchor_town,
        "town_stats_fallback": bool(
            anchor_town and property_town
            and anchor_town != property_town),
        "town_depth_ft": town_stats,
        "planning_note": (
            "Plan against the geology, not the average: budget for the deep "
            "end of the local depth range and price the dry/low-yield downside "
            "(+$10k-$25k contingency) before committing."
        ),
        "wells": sorted(wells, key=lambda w: (w["depth_ft"] is None, w["depth_ft"])),
    }
    with open(f"{order_dir}/nearby_wells.json", "w") as f:
        json.dump(result, f, indent=2)
    if base_dir:
        save_shared_pull(base_dir, order, geo, result)
    return result
