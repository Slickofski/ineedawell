#!/usr/bin/env python3
"""Report-tier fulfillment orchestrator ($39 tier).

Usage:
  python3 run_report.py --order '{"name":..., "email":..., "phone":..., "property_address":...}'
  python3 run_report.py --order-file order.json

Runs: intake -> geocode -> nearby wells -> water quality -> setback screen ->
driller shortlist -> customer report (report.html) -> delivery draft.

The report tier NEVER contacts anyone: no outreach drafts, no packet, no
mailer. It is pure information. Nothing in this script can send an email.
"""
import argparse
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import (  # noqa: E402
    intake, geocode, nearby_wells, water_quality, setbacks, drillers, report,
)

BASE = os.path.dirname(os.path.abspath(__file__))


def scope_check(geo, order_or_address=None):
    """Independent scope guard (Codex R02): None if verified, else reason.

    Same guard as run_screen.scope_check: delegates to
    geocode.verify_geocode_result, so the state comes only from the
    geocode result's STRUCTURED fields, never from submitted text.
    """
    if isinstance(order_or_address, dict):
        address = order_or_address.get("property_address")
        slat = order_or_address.get("lat")
        slon = order_or_address.get("lon")
    else:
        address, slat, slon = order_or_address, None, None
    try:
        geocode.verify_geocode_result(geo, address,
                                      supplied_lat=slat, supplied_lon=slon)
    except geocode.AddressError as e:
        return str(e)
    return None


def main():
    ap = argparse.ArgumentParser(description="Report-tier fulfillment ($39)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--order", help="order JSON string")
    g.add_argument("--order-file", help="path to order JSON file")
    args = ap.parse_args()

    raw = json.loads(args.order) if args.order else json.load(open(args.order_file))
    raw.setdefault("tier", "report")

    order_id = intake.new_order_id(raw)
    order_dir, _outbox_dir = intake.order_paths(BASE, order_id)
    os.makedirs(order_dir, exist_ok=True)

    print(f"[1/7] intake -> {order_id}")
    order = intake.intake(raw, order_dir)
    assert order["tier"] == "report", "run_report.py is for the report tier only"

    print("[2/7] geocode")
    try:
        geo = geocode.geocode(order, order_dir)
        scope_reason = scope_check(geo, order)
        if scope_reason is not None:
            raise geocode.AddressError(scope_reason)
    except Exception as e:
        out_of_scope = isinstance(e, geocode.OutOfScopeError)
        fail = {"order_id": order_id, "tier": "report",
                "mode": "declined" if out_of_scope else "failed",
                "stage": "geocode", "error": str(e),
                "note": ("Pilot covers Connecticut only. Send the "
                         "out-of-scope email." if out_of_scope else
                         "Address could not be verified. Send the "
                         "clarification email.")}
        with open(os.path.join(order_dir, "fulfillment_failed.json"), "w") as f:
            json.dump(fail, f, indent=2)
        c = order["customer"]
        site = report._biz(report.load_business(), "website",
                           "https://ineedawell.com")
        if out_of_scope:
            body = (
                f"Thanks for writing — right now I Need a Well only "
                f"covers Connecticut properties, and "
                f"{order['property_address']} looks like it's outside "
                f"the area. I've noted your interest and I'll reach "
                f"out if we expand.\n"
            )
        else:
            body = (
                f"I couldn't pin down {order['property_address']} on "
                f"the map with enough certainty to build a report on "
                f"it — could you double-check the spelling, or send a "
                f"nearby intersection or the listing link? I'll run "
                f"it again right away.\n"
            )
        draft = (
            f"Subject: Re: your well report request\n"
            f"To: {c['email']}\n"
            f"\n"
            f"Hi {c['name'].split()[0] if c.get('name') else 'there'} "
            f"— Marcin here, from I Need a Well ({site}).\n"
            f"\n{body}\n"
            f"— Marcin\n"
            f"I Need a Well \u00b7 {site}\n"
        )
        with open(os.path.join(order_dir, "customer_reply_draft.txt"), "w") as f:
            f.write(draft)
        print(f"      FAILED (graceful): {e}")
        print("      Wrote clarification reply draft instead of a report.")
        return
    print(f"      {geo['lat']:.5f}, {geo['lon']:.5f} ({geo['source']})")

    print("[3/7] nearby wells")
    wells = nearby_wells.nearby_wells(order, geo, order_dir, BASE)
    print(f"      {wells['total_water_supply_wells']} wells, "
          f"{wells['wells_with_depth_yield']} with depth/yield")

    print("[4/7] water quality")
    wq = water_quality.water_quality(order, geo, order_dir)
    print(f"      arsenic rows={wq['arsenic']['result_rows']}, "
          f"uranium rows={wq['uranium']['result_rows']}")

    print("[5/7] setback screen")
    sb = setbacks.setback_screen(order, order_dir)

    print("[6/7] driller shortlist")
    ranked = drillers.rank_drillers(order, geo, order_dir)
    for d in ranked["drillers"]:
        print(f"      {d['company']} ({d['town']}) — {d['distance_miles']} mi")

    print("[7/7] customer report")
    report_path = report.build_report(order, geo, wells, wq, sb, ranked, order_dir)
    print(f"      {report_path}")

    c = order["customer"]
    draft = (
        f"Subject: Your well report for {order['property_address']}\n"
        f"To: {c['email']}\n"
        f"\n"
        f"Hi {c['name'].split()[0]},\n"
        f"\n"
        f"Your property well report is ready — attached.\n"
        f"\n"
        f"Bottom line up front: open the report and read the verdict box first; "
        f"everything else is the evidence behind it.\n"
        f"\n"
        f"Two things worth doing this week:\n"
        f"1. Call two or three drillers from the 'Who to call' section and ask "
        f"what depth they would permit for on this lot.\n"
        f"2. When the well is finished, confirm the lab runs the state-required "
        f"arsenic and uranium panel — Connecticut requires both for new wells — "
        f"and ask whether radon makes sense for your lot.\n"
        f"\n"
        f"Questions about the report? Just reply to this email.\n"
    )
    with open(os.path.join(order_dir, "delivery_email_draft.txt"), "w") as f:
        f.write(draft)

    superseded = intake.supersede_failure_marker(order_dir)
    summary = {
        "order_id": order_id,
        "tier": "report",
        "mode": "fulfilled",
        "generated": date.today().isoformat(),
        "artifacts": {
            "order": "order.json", "geocode": "geocode.json",
            "wells": "nearby_wells.json", "water_quality": "water_quality.json",
            "setbacks": "setbacks.json", "drillers": "drillers_ranked.json",
            "report": "report.html", "delivery_draft": "delivery_email_draft.txt",
        },
        "note": "Report tier contacts nobody. No outreach, no packet, no mailer.",
    }
    if superseded is not None:
        summary["superseded_failure"] = superseded
    with open(os.path.join(order_dir, "pipeline_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\nDone. Report: {report_path}")


if __name__ == "__main__":
    main()
