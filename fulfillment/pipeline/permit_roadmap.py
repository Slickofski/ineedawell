"""Permit roadmap for the Free Well Snapshot.

Practitioner insight (Bogdan Rejnin, fire pit, 2026-09-19): the universal pain
point in construction work is paperwork — permits, who files what, in what
order. The snapshot answers "can I get water here"; this section answers
"what do I actually do."

Contents: per-town permitting authority (pilot towns; falls back to the local
health district) plus the ordered process for a new CT private well, each step
labeled with who does it, the typical fee where one exists, and what to
verify. Fees are planning context, not quotes — verify with the district
before acting.
"""

# district = well-permit / environmental-health authority for the town.
# Source of truth: pipeline/rules_ct.json (town_district map + district
# names), loaded via pipeline/rules.py — this module no longer keeps its
# own copy of the map. (Pre-2026-10-16 this file carried a hand-mirrored
# TOWN_DISTRICTS dict; the rules package retired the duplicate.)
from pipeline import rules


def permit_authority(town: str) -> str:
    """Permitting authority for a town; falls back to the local health district."""
    name = rules.district_name(town) if town else "your local health district"
    if name == "your local health district":
        return name
    return f"the {name}"


def roadmap(town: str) -> list:
    """Ordered permit/process steps for a new CT private well.

    Each step: title, who does it, and the detail incl. what to verify.
    """
    district = permit_authority(town)
    # Fee figures from the rules table (cost-source pass, 2026-11-04):
    # CGS 25-130 sets the fee district by district, so the copy names
    # districts and takes the amounts from their cited fee entries.
    ncdhd_fee = rules.fee_amount("ct_fee_well_permit_ncdhd")
    bloom_fee = rules.fee_amount("ct_fee_well_only_whbhd")
    return [
        {
            "step": "Before you commit: get driller quotes",
            "who": "You",
            "detail": ("Get 2\u20133 quotes from Connecticut-licensed well drillers. "
                       "Ask each one: what depth would you permit for this lot, and "
                       "what exactly do I pay if the hole comes up dry?"),
        },
        {
            "step": "Verify the driller's license",
            "who": "You",
            "detail": ("Well drillers must hold a Connecticut Department of Consumer "
                       "Protection license. Check it before you sign anything — "
                       "an unlicensed driller can't pull your permit."),
        },
        {
            "step": "File the well permit",
            "who": "Your driller files it; you pay the fee",
            "detail": (f"Your driller signs the state permit application as applicant "
                       f"and files it with {district}. The fee is set by the "
                       f"district, not the state: it is ${ncdhd_fee} in the North "
                       f"Central District and ${bloom_fee} in Bloomfield's "
                       f"district, to give the local range. Confirm the current "
                       f"fee with the district before the driller files. "
                       f"Nothing gets drilled before this is approved."),
        },
        {
            "step": "Pass the site and setback review",
            "who": "The health district",
            "detail": ("The district confirms the proposed well spot meets CT setbacks "
                       "(Public Health Code \u00a719-13-B51d): "
                       + rules.buyer_sentence("ct_b51d_pollution_under_10") +
                       " Plus 25 ft from surface water and drains (50 ft in the "
                       "higher bands). "
                       + rules.doubling_caveat() +
                       " This is the step that kills bad lot "
                       "layouts \u2014 better to learn it now than after you own the lot."),
        },
        {
            "step": "Drill the well",
            "who": "Your driller",
            "detail": ("You pay for the attempt whether the hole comes up wet or dry "
                       "\u2014 that's why the cost band in this snapshot carries the "
                       "dry-hole contingency. Get the driller's dry-hole policy in writing."),
        },
        {
            "step": "Lab-test the water",
            "who": "You, through a certified lab",
            "detail": ("Every new CT well must be lab-tested on the "
                       "state-required baseline panel \u2014 15 tests, "
                       "including arsenic and uranium (CGS 19a-37(c)(2), "
                       "since October 1, 2022). If the well serves an "
                       "existing structure, a first-draw lead sample from "
                       "the plumbing is also required. Ask the lab whether "
                       "radon testing makes sense for your lot too \u2014 the "
                       "water section above flags what your area's geology "
                       "suggests."),
        },
        {
            "step": "Get the health authority's compliance determination",
            "who": "Your local health authority",
            "detail": ("The lab results go to your local health authority, "
                       "which determines whether they comply with the "
                       "state maximum contaminant levels. The well may "
                       "not be used for domestic purposes until the "
                       "health authority makes that determination "
                       "(CGS 19a-37(c)(2))."),
        },
        {
            "step": "Keep your paperwork",
            "who": "You",
            "detail": ("Get a copy of the well completion report your driller files "
                       "with the state, and keep it with the deed. Future buyers \u2014 "
                       "and future you \u2014 will want it."),
        },
    ]
