"""Stage 5 — CT setback screen.

Rules come from the rules-as-data table (pipeline/rules.py +
rules_ct.json) — no distances or rule text are hardcoded here anymore.
Each item carries its citation and confidence label from the table.
Parcel GIS (boundaries, wetlands, structures) is NOT yet ingested, so
every item is marked "needs field verification" — this stage produces
the checklist a sanitarian/engineer works from, not a compliance verdict.

History: the pre-2026-10-16 hardcoded list carried three audit findings
(rules audit M5/M6): a "well to reservoir 100 ft" item that is actually
a leaching-system rule, a citation to a technical-standards table number
that does not exist (the real one is Table 1), and band
wording keyed to pumping/yield instead of required withdrawal rate. The
table fixes all three at the source.
"""
import json

from pipeline import rules


def _town_from(order: dict) -> str:
    parts = [p.strip() for p in (order.get("property_address") or "").split(",")]
    return parts[1] if len(parts) > 1 else ""


def setback_screen(order: dict, order_dir: str) -> dict:
    town = _town_from(order)
    items = []
    for item in rules.well_setback_items(town=town):
        items.append(
            {
                **item,
                "status": "needs_field_verification",
                "finding": (
                    "We can't measure your lot from public records: your "
                    "surveyor or engineer confirms these distances on the "
                    "site plan, and the health district checks them in the "
                    "field before filing."
                ),
            }
        )
    result = {
        "parcel_data_available": False,
        "items": items,
        "confidence_note": rules.confidence_line(town),
        "note": "Screening-level checklist only. Not a substitute for survey, "
                "wetlands delineation, or field soil/perc tests.",
    }
    with open(f"{order_dir}/setbacks.json", "w") as f:
        json.dump(result, f, indent=2)
    return result
