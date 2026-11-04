"""Stage 9 — Packet generation: prefilled permit-packet draft.

Prefills OUR sections: buyer info, parcel info, nearby-well evidence summary,
water-quality flags, setback checklist, site-sketch placeholder, filing
checklist + fees, exact next steps.
Driller and health-authority sections stay BLANK for their signatures.
Structure mirrors files/well-permit-packet-draft-193-wells-rd.pdf.

Output: orders/<id>/packet.md (the customer deliverable).
"""
import json
from datetime import date

from pipeline import nearby_wells
from pipeline import rules
from pipeline import water_quality


def _town_from(address: str) -> str:
    parts = [p.strip() for p in address.split(",")]
    return parts[1] if len(parts) > 1 else "n/a"


def _well_rows(wells: dict) -> str:
    rows = []
    for w in wells.get("wells", []):
        def f(v, unit=""):
            return f"{v:g}{unit}" if v is not None else "n/a"
        rows.append(
            f"| {w['address']} | {f(w['depth_ft'], ' ft')} | {f(w['yield_gpm'], ' gpm')} "
            f"| {f(w['static_level_ft'], ' ft')} | {f(w.get('pumping_level_ft'), ' ft')} "
            f"| {w['date'] or 'n/a'} |"
        )
    return "\n".join(rows) if rows else "| n/a | n/a | n/a | n/a | n/a | n/a |"


# One shared map (CX pass, 2026-10-29): lives in nearby_wells with
# the yield-test semantics; the snapshot's Strength check uses it too.
_METHOD_PLAIN = nearby_wells.METHOD_PLAIN


def _duration_txt(hours) -> str:
    if hours is None:
        return "duration not recorded"
    if hours < 1:
        return f"{hours * 60:g} minutes ({hours:g} hours)"
    return f"{hours:g} hours"


def _storage_paragraph(st: dict) -> str:
    """Packet section-3 borehole-storage paragraph (2026-10-24,
    borehole-storage fix Part 1; wording from the approved audit
    drafts). Two-layer: headline + one sentence up front, the
    exclusions count and the 6-inch assumption as backing. Renders
    only when at least one sane depth + resting-level pair exists —
    unknown storage is never stated."""
    if not st["wells_with_column"]:
        return ""
    col = st["median_column_ft"]
    gal = st["median_storage_gal"]
    lines = [
        "**The borehole is a storage tank, not just a straw.** "
        "A standard 6-inch well holds about 1.5 gallons for every "
        f"foot of water standing in it, and the typical neighbor "
        f"well here stands about {col:g} ft deep in water: "
        f"roughly {gal:g} gallons in reserve before the aquifer has "
        "to keep up.",
        "",
    ]
    if st["excluded_total"]:
        lines.append(
            f"{st['excluded_total']} filing(s) left out: resting "
            "level missing or impossible against the depth.")
    lines.append(
        "6-inch bore assumed; a wider or narrower bore holds more "
        "or less per foot. The pump also sits a few feet off the "
        "bottom, so treat the figure as 'about,' not exact.")
    return "\n".join(lines)


def _well_strength(wells: dict) -> str:
    """Buyer-facing record of the neighbors' yield tests (2026-10-20;
    rewritten for Codex R03, 2026-10-21).

    Publishes only what the driller filings record — resting (static)
    level, level during the test, method, duration, recorded yield —
    stated neutrally, per well. Deliberately NO specific-capacity
    (gpm-per-foot) figure, and NO sustained-production conclusion: each
    filing records a single endpoint level, with no stabilization or
    recovery measurement, so sustained yield is unestablished from
    these records. The earlier endpoint inference (a level above the
    bottom read as sustained production) was removed, not reworded.
    Filings with impossible levels (during-test level shallower than
    the resting level, or below the well bottom) are flagged as data
    errors and excluded. Classification comes from the ONE shared
    definition in nearby_wells.strength_summary.
    """
    all_wells = wells.get("wells", [])
    from pipeline import nearby_wells as _NW
    _s = _NW.strength_summary(all_wells)
    trio, invalid = _s["trio"], _s["invalid"]
    # Borehole storage (2026-10-24 fix Part 1): the standing-reserve
    # paragraph renders whenever a sane depth + resting-level pair
    # exists — including when no yield-test details are on file.
    storage_block = _storage_paragraph(_NW.storage_summary(all_wells))
    if not trio and not invalid:
        return f"\n{storage_block}\n" if storage_block else ""

    bullets = []
    if trio:
        bullets.append(
            f"- {len(trio)} of {len(all_wells)} wells on record "
            f"{'has' if len(trio) == 1 else 'have'} full test details "
            f"filed (resting level, level during the test, and yield).")
        for w in trio:
            method = _METHOD_PLAIN.get(
                w.get("yield_test_type"),
                (w.get("yield_test_type") or "method not recorded").lower())
            bullets.append(
                f"- {w.get('address') or 'Address not listed'}: resting "
                f"level {w['static_level_ft']:g} ft, level during the "
                f"test {w['pumping_level_ft']:g} ft, recorded yield "
                f"{w['yield_gpm']:g} gpm, tested with {method} over "
                f"{_duration_txt(w.get('yield_test_hours'))}.")
    if invalid:
        bullets.append(
            f"- {len(invalid)} filed test(s) report impossible water "
            f"levels (a during-test level shallower than the resting "
            f"level, or below the well bottom) and are excluded as data "
            f"errors: do not rely on them.")

    return f"""
**What the neighbors' yield tests recorded.** Gallons-per-minute is the
driller's recorded result of a single test, and the state filings also
record the water levels, method and duration behind it:

{chr(10).join(bullets)}

**Bottom line:** sustained yield is unestablished from these filings.
Each one records a single endpoint level: no measurement of whether
the level stabilized during the test, and no recovery measurement
after it, so no conclusion about sustained production can be drawn
from them. Read the yields as what the driller recorded that day, and
make any driller you hire talk through the deep, low-yield downside
before you sign.
""" + (f"\n{storage_block}\n" if storage_block else "")


# Customer words for the setback status column (CX pass, 2026-10-29):
# the pipeline's snake_case status is machinery, not copy. Unknown
# statuses fall back to de-snaked text rather than shipping raw keys.
_STATUS_PLAIN = {"needs_field_verification": "To confirm on the site plan"}


def _setback_rows(setbacks: dict) -> str:
    rows = []
    for it in setbacks["items"]:
        status = _STATUS_PLAIN.get(it["status"],
                                   str(it["status"]).replace("_", " "))
        rows.append(
            f"| {it['rule']} | {it['minimum_ft']} ft | {status} |"
        )
    return "\n".join(rows)


def build_packet(order, geo, wells, wq, setbacks, order_dir) -> str:
    c = order["customer"]
    town = _town_from(order["property_address"])
    today = date.today().isoformat()
    d, y = wells.get("depth_ft"), wells.get("yield_gpm")

    if d and y:
        # R01: depth and yield counts are stated separately — the depth
        # range rests on depth-bearing records, the yield range only on
        # yield-bearing ones; neither count stands in for the other.
        total = wells.get("total_water_supply_wells")
        dc = wells.get("depth_count")
        yc = wells.get("yield_count")
        if total is not None and dc is not None and yc is not None:
            evidence_line = (
                f"{total} nearby water-supply wells on record: {dc} report "
                f"depth, drilled {d['min']:g} to {d['max']:g} ft (median "
                f"{d['median']:g} ft); {yc} report yield, "
                f"{y['min']:g} to {y['max']:g} gallons per minute (gpm)."
            )
        else:
            evidence_line = (
                f"{wells['wells_with_depth_yield']} nearby water-supply wells "
                f"with both depth and yield on file, drilled "
                f"{d['min']:g} to {d['max']:g} ft (median {d['median']:g} ft), "
                f"yielding {y['min']:g} to {y['max']:g} gallons per minute (gpm)."
            )
    else:
        evidence_line = "Limited public well records near this parcel."

    # B-13: flags are _flag() dicts — format them to finished copy via
    # the shared formatter, never print the raw dict as a bullet.
    wq_flags = "\n".join(f"- {water_quality.format_flag(f)}"
                         for f in wq["flags"])

    # Filing fees come from the rules table (cost-source pass,
    # 2026-11-04): CGS 25-130 sets fees district by district, so the
    # checklist names the district and takes the figure from its
    # cited fee entry — never a statewide "typical" number.
    ncdhd_permit_fee = rules.fee_amount("ct_fee_well_permit_ncdhd")
    ncdhd_site_fee = rules.fee_amount("ct_fee_site_investigation_ncdhd")

    packet = f"""# DRAFT: Well Permit Packet

**Not filed. Not a permit.** Generated {today} for order {order['order_id']}.
Prefilled from public data + your order details. Verify every rule, fee, and
parcel detail against current authoritative documents before filing.

## 1. Subject parcel

- **Address:** {order['property_address']}
- **Coordinates:** {geo['lat']:.5f}, {geo['lon']:.5f} (source: {geo['source']})
- **Jurisdiction:** Town of {town} → {rules.district_name(town)}

## 2. Prepared for

- **Name:** {c['name']}
- **Phone:** {c['phone']}
- **Email:** {c['email']}

## 3. Nearby well evidence

Source: {wells['source']}{f" (records pulled {wells['pulled_at']})" if wells.get('pulled_at') else ""}. {evidence_line}

| Address | Depth | Yield | Static level (how far down the water sits) | Level during test | Date |
|---|---|---|---|---|---|
{_well_rows(wells)}
{_well_strength(wells)}
**Reading this:** budget against the geology, not the average. The honest
downside is a deep, low-yield hole: under the standard driller contract you
still pay for the attempt (+$10k to $25k contingency band).

## 4. Water-quality flags

{wq_flags}

- For a NEW well, Connecticut's required baseline test panel ({len(rules.testing_panel())} tests: CGS 19a-37(c)(2), effective 2022-10-01) includes arsenic and uranium by law. Lead joins
  the required panel as a first-draw sample when the new well serves an EXISTING structure.
  For an existing well tested voluntarily, none of these are automatic: request them on
  the lab panel.
- {wq['final_word']}

## 5. Setback checklist (CT rules: your engineer confirms each distance on the site plan)

A setback is the legal distance a well must sit from septic and water.

{setbacks.get('confidence_note') or rules.confidence_line(town)}

| Separation | Minimum | Status |
|---|---|---|
{_setback_rows(setbacks)}

Screening-level only. Not a substitute for survey, wetlands delineation, or
field soil/perc tests. {rules.doubling_caveat()}

{rules.exception_paths_text()}

## 6. Site sketch: PLACEHOLDER

[Schematic well-location sketch to be finalized with the driller + health
district from the engineered site plan. Must show: the well relative to at
least two roads with distances, north arrow, distances to septic area(s),
property lines, and any watercourses.]

## 7. Filing checklist & fees

- [ ] Confirm the ownership path: the owner of record (or authorized agent) completes the owner sections
- [ ] Health-district site investigation, soil and percolation testing (North Central District: ${ncdhd_site_fee} per lot, fee schedule effective July 1, 2026. Fees vary by district, so confirm yours before you budget.)
- [ ] Hire a CT-licensed well driller: the driller completes and signs the state permit as applicant (CT WWC-________)
- [ ] File the CT DCP Well Drilling Permit via the health district (North Central District: ${ncdhd_permit_fee}, fee schedule effective July 1, 2026. Fees vary by district, so confirm yours.) The permit must be countersigned by the Director of Health or an agent before it is valid.
- [ ] Run the septic track in parallel if needed (construction permit + plan review; verify current fees)
- [ ] Drill: the driller files the Well Completion Report with you, CT DCP, and the health district
- [ ] Test the water: the state-required new-well baseline panel (includes arsenic and uranium; first-draw lead too when the well serves an existing structure); add radon: recommended in CT bedrock country, not required, given local bedrock

Fees change. Confirm the current schedule with the health district when you file.
Drilling itself is quoted separately by the driller you choose.

## 8. Your exact next steps

1. Pick a driller from the quote comparison we sent you.
2. Sign the owner sections of this packet; the driller signs the applicant section.
3. Submit to the health district with fees; complete the site investigation.
4. Drill, then test the water before drinking it.

## 9. Signature blocks

**Owner / applicant (you):** ______________________  Date: __________

**Licensed driller (to be completed by your driller):**

Business: ______________________  CT WWC-# __________  Signature: ______________________  Date: __________

**Health district (countersignature):**

Approved / Rejected: Officer: ______________________  Signature: ______________________  Date: __________

---
*DRAFT: generated {today}. Not filed with any agency. Not legal advice.*
"""
    path = f"{order_dir}/packet.md"
    with open(path, "w") as f:
        f.write(packet)
    return path
