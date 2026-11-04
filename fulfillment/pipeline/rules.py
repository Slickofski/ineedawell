"""Rules-as-data loader (rules package step 1, 2026-10-16, Remy).

Single source of truth for CT well rules: pipeline/rules_ct.json.
Every rule carries value, condition, citation, confidence
(CODE / OPINION / GUIDANCE / UNVERIFIED), and verified_date. Consumers
(setbacks, permit_roadmap, packet, report) read from here — no rule
text or distance may be hardcoded in a consumer anymore.

Audit trail: files/rules-audit-2026-10-15.md (what the rules are) and
files/rules-step0-research-2026-10-15.md (the two pre-flight answers).
"""
import json
import os

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules_ct.json")
_CACHE = None


def load() -> dict:
    global _CACHE
    if _CACHE is None:
        with open(_PATH) as f:
            _CACHE = json.load(f)
    return _CACHE


def get_rule(rule_id: str) -> dict:
    for r in load()["rules"]:
        if r["id"] == rule_id:
            return r
    raise KeyError(rule_id)


def rules_for(jurisdiction=None, applies_to=None, band=None) -> list:
    out = []
    for r in load()["rules"]:
        if jurisdiction is not None and r["jurisdiction"] != jurisdiction:
            continue
        if applies_to is not None and r["applies_to"] != applies_to:
            continue
        if band is not None and r.get("band") != band:
            continue
        out.append(r)
    return out


# ---------------------------------------------------------------- districts

def district_key_for_town(town: str):
    if not town:
        return None
    return load()["town_district"].get(town.strip().title())


def district_for_town(town: str):
    key = district_key_for_town(town)
    if key is None:
        return None
    info = dict(load()["districts"][key])
    info["key"] = key
    return info


def district_name(town: str) -> str:
    d = district_for_town(town)
    return d["name"] if d else "your local health district"


# ------------------------------------------------------------- demand bands

# Band keys (the demand_bands vocabulary) vs the suffixes the rule
# ids actually use — the two spellings diverged and the non-default
# branches built ids that don't exist (Codex R22). Every conversion
# goes through this explicit map, nowhere else.
_BAND_RULE_SUFFIX = {"under_10": "under_10", "10_to_50": "10_50",
                     "over_50": "over50"}


def governing_band(use: str = "single_family_home",
                   required_gpm=None) -> str:
    """Band keyed by what the well SERVES (its required withdrawal rate =
    demand), never by tested yield. A single-family home sits in the
    under-10 band (operating assumption, OPINION-labeled in the table).

    Explicit rate model (R22): when required_gpm is given it decides
    the band (<10 under_10, 10-50 10_to_50, >50 over_50). A shared or
    large supply WITHOUT a rate has no honest band — ValueError,
    never a guess."""
    if required_gpm is not None:
        if isinstance(required_gpm, bool) or not isinstance(
                required_gpm, (int, float)) or required_gpm <= 0:
            raise ValueError(
                f"required_gpm must be a positive number, got "
                f"{required_gpm!r}")
        if required_gpm < 10:
            return "under_10"
        if required_gpm <= 50:
            return "10_to_50"
        return "over_50"
    if use in ("shared_supply", "large_supply"):
        raise ValueError(
            f"{use} requires an explicit required withdrawal rate "
            "(required_gpm): the band is not guessed (R22)")
    return "under_10"


_BAND_SCALES = {
    "under_10": (
        "Bands follow the well's required withdrawal rate (what it serves), "
        "not its tested yield: 150 ft if the supply must deliver 10-50 gpm "
        "(e.g. one well serving several homes), 200 ft above 50 gpm. A single "
        "home sits in the under-10 band."
    ),
    "10_to_50": "200 ft if the supply must deliver over 50 gpm.",
    "over_50": "Highest band in 19-13-B51d.",
}


def _cite(rule: dict) -> str:
    return rule["citation"]


def well_setback_items(town: str = None, use: str = "single_family_home",
                       required_gpm=None) -> list:
    """Setback checklist items in the established setbacks.py item shape
    (id / rule / minimum_ft / condition / scales / source), generated from
    the rules table for the governing demand band of `use`.

    Adds two items the old hardcoded list lacked: the conditional doubling
    rule (honest condition, never computed) and, in NCDHD towns, the
    district's 25 ft boundary guidance labeled as guidance. The old
    'well to reservoir' item is gone: the 100 ft reservoir figure is a
    leaching-system rule (audit M5) and lives in the table as such.
    """
    band = governing_band(use, required_gpm)
    suffix = _BAND_RULE_SUFFIX[band]
    items = []

    def _item(rule_id, display, item_id):
        r = get_rule(rule_id)
        return {
            "id": item_id,
            "rule": display,
            "minimum_ft": r["value_ft"],
            "condition": r["condition"],
            "scales": _BAND_SCALES.get(band),
            "source": _cite(r),
            "confidence": r["confidence"],
        }

    items.append(_item(
        f"ct_b51d_pollution_{suffix}",
        "Well to septic system and its leaching field (where septic water drains into the soil), buried oil tank, or other pollution source",
        "septic",
    ))
    items.append(_item(
        f"ct_b51d_surface_{suffix}",
        "Well to surface water (high-water mark) / surface-water or foundation drain",
        "watercourse",
    ))
    items.append(_item(
        f"ct_b51d_sewer_{suffix}",
        "Well to tight-joint sewer (extra-heavy cast iron, leaded joints, or equal)",
        "sewer",
    ))

    dbl = get_rule("ct_b51d_doubling")
    base = get_rule(f"ct_b51d_pollution_{suffix}")["value_ft"]
    items.append({
        "id": "doubling_rule",
        "rule": "Well to the leaching system (where septic water drains into the soil) IF the soil is fast AND the ledge is shallow: the distance doubles",
        "minimum_ft": base * dbl["multiplier"],
        "condition": dbl["condition"],
        "scales": None,
        "source": _cite(dbl),
        "confidence": dbl["confidence"],
    })

    if district_key_for_town(town) == "NCDHD":
        g = get_rule("ncdhd_well_boundary_guidance")
        items.append({
            "id": "district_boundary_guidance",
            "rule": "Well to property boundary. District guidance: keep 25 ft 'if possible' (guidance, not a state setback)",
            "minimum_ft": g["value_ft"],
            "condition": g["condition"],
            "scales": None,
            "source": _cite(g),
            "confidence": g["confidence"],
        })
    return items


# ------------------------------------------------------------------ costs

def cost_entry(band: str) -> dict:
    """The rules-table cost entry for a band ('cooperates', 'plan_on',
    'fights_you', 'contingency'). Costs are rules-as-data like the
    setbacks (cost-source pass, 2026-11-04): every rate, fee, and
    contingency a consumer prints comes from here, never from a
    literal in the consumer. A missing entry is a broken table, not
    a cue to guess — RuntimeError, loud, never a silent fallback."""
    for r in rules_for(applies_to="cost"):
        if r.get("band") == band:
            return r
    raise RuntimeError(
        f"rules_ct.json has no cost entry for band {band!r}: cost "
        "figures come from the rules table or not at all")


def cost_rate(band: str):
    """USD-per-foot planning rate for a scenario band, from the table."""
    r = cost_entry(band)
    if r.get("rate_usd_per_ft") is None:
        raise RuntimeError(
            f"rules_ct.json cost entry {r['id']} has no rate_usd_per_ft")
    return r["rate_usd_per_ft"]


def fee_amount(rule_id: str):
    """A district fee in USD from the rules table. Loud if absent —
    a fee is never restated from memory (CGS 25-130 sets fees district
    by district, so an unnamed-district number is wrong by construction)."""
    try:
        r = get_rule(rule_id)
    except KeyError:
        raise RuntimeError(
            f"rules_ct.json is missing fee entry {rule_id!r}") from None
    if r.get("amount_usd") is None:
        raise RuntimeError(
            f"rules_ct.json fee entry {rule_id} has no amount_usd")
    return r["amount_usd"]


# ------------------------------------------------------------------- copy

def testing_panel() -> list:
    """The statutory BASELINE panel (CGS 19a-37(c)(2)): 15 parameters
    every newly constructed well is tested for."""
    return list(get_rule("ct_testing_new_well_panel")["parameters"])


def testing_panel_conditional() -> list:
    """Conditional additions to the baseline panel — currently only
    first-draw lead, required when the well serves an existing
    structure. Each entry carries its own condition; renderers must
    print the condition WITH the parameter (R17/R18)."""
    return list(get_rule("ct_testing_new_well_panel")
                .get("conditional_parameters", []))


def exception_paths_text() -> str:
    """M4 replacement: the real exception paths, replacing the old
    unverified footer that promised a health-director setback reduction
    with a 25 ft floor — a power no source could be found for."""
    return (
        "If a distance genuinely cannot be met, the paths are narrow and "
        "specific: DPH can grant an exception to the separating-distance "
        "requirements for a sewage system under CGS 19a-209c: affected "
        "well owners must be notified by certified mail, and an approval "
        "is not a defense against contamination claims, and the "
        "Commissioner holds limited exception authority under Public "
        "Health Code 19-13-B51m(c). There is no general rule letting a "
        "health director reduce setbacks; do not plan around one."
    )


def confidence_line(town: str = None) -> str:
    """Step 5: the packet's self-labeling of how verified its rules are,
    driven by the district's harvest state in the table. Two options only
    (per the coding package): documented, or unconfirmed — never vaguer."""
    d = district_for_town(town)
    if d is None:
        return ("State rules verified; district add-ons unconfirmed "
                "(district not yet mapped for this town).")
    if d["docs_state"] == "harvested":
        return (f"State rules verified; {d['name']} add-ons checked "
                f"(Oct 2026): the ones that apply are included below.")
    return (f"State rules verified; district add-ons unconfirmed "
            f"({d['name']} may publish its own: not yet checked). "
            "The district has the final word on your lot.")


# Customer words for the table's confidence vocabulary (CX residual
# fix, 2026-10-29): the codes are internal shorthand — a buyer who
# reads "Confidence: CODE" learns nothing. Customer-facing surfaces
# render these plain phrases through confidence_plain(); the codes
# themselves stay in the data (rules_ct.json, setbacks.json and the
# other JSON surfaces keep CODE/OPINION/GUIDANCE/UNVERIFIED).
CONFIDENCE_PLAIN = {
    "CODE": "written in the state health code",
    "OPINION": ("standard professional reading: not spelled out "
                "word-for-word in the code"),
    "GUIDANCE": "district guidance: not a state rule",
    "UNVERIFIED": "not yet verified",
}


def confidence_plain(code) -> str:
    """Plain-words rendering of a rules-table confidence code, for
    customer surfaces. A code the table does not define yet passes
    through unchanged rather than being assigned a meaning it may
    not have — test_cx_residual_fixes pins every value in the
    table's confidence_values list to a phrase, so a new code
    cannot ship bare unnoticed."""
    return CONFIDENCE_PLAIN.get(str(code or "").strip().upper(), code)


def buyer_sentence(rule_id: str) -> str:
    r = get_rule(rule_id)
    if "buyer_sentence" not in r:
        raise KeyError(f"{rule_id} has no buyer_sentence")
    return r["buyer_sentence"]


def doubling_caveat() -> str:
    """The doubling rule as a standing caveat sentence (coding package
    step 3): it ships wherever the 75 ft figure is stated and soil is
    unknown — caveat_only until the pipeline has perc/ledge data."""
    return (
        "One rule can change this number: if the soil drains very fast "
        "(perc faster than 1 minute per inch) and ledge rock is shallow "
        "(leaching bottom less than 8 ft above it), the state doubles the "
        "well-to-septic distance: 150 ft for a typical home. That is only "
        "known after a perc test, which happens at permit time."
    )
