#!/usr/bin/env python3
"""Free-tier fulfillment orchestrator (pilot).

Usage:
  python3 run_screen.py --order '{"name":..., "email":..., "property_address":...}'
  python3 run_screen.py --order-file order.json

Runs: intake -> geocode -> nearby wells -> water quality -> setback screen ->
free snapshot (screen.html) -> delivery draft.

The free tier contacts nobody and costs nothing to fulfill: public data in,
one HTML file out. No drillers, no outreach, no packet, no mailer.
"""
import argparse
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import (  # noqa: E402
    intake, geocode, nearby_wells, water_quality, setbacks, screen, report,
    jev_triage,
)
from pipeline import flags as FLAGS  # noqa: E402  (aliased: main() has a local `flags`)

BASE = os.path.dirname(os.path.abspath(__file__))


def first_name(customer: dict) -> str:
    """Greeting name; the free pilot collects no name, so fall back to 'there'."""
    return ((customer.get("name") or "").split() or ["there"])[0]


def scope_check(geo, order_or_address=None):
    """Independent scope guard (Codex R02): None if verified, else reason.

    Delegates to geocode.verify_geocode_result: the state comes only
    from the geocode result's STRUCTURED fields, never from the
    submitted address text or display_name substrings. Accepts an
    order dict (uses its property_address + supplied lat/lon) or a
    bare address string.
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
    ap = argparse.ArgumentParser(description="Free-tier fulfillment (pilot)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--order", help="order JSON string")
    g.add_argument("--order-file", help="path to order JSON file")
    args = ap.parse_args()

    raw = json.loads(args.order) if args.order else json.load(open(args.order_file))
    raw.setdefault("tier", "free")
    raw.setdefault("amount_usd", 0)

    order_id = intake.new_order_id(raw)
    order_dir, _outbox_dir = intake.order_paths(BASE, order_id)
    os.makedirs(order_dir, exist_ok=True)

    print(f"[1/6] intake -> {order_id}")
    order = intake.intake(raw, order_dir)

    print("[1b/6] jev triage")
    tri = jev_triage.triage(raw, order_dir)
    print(f"      {tri['decision']} ({tri['reason']})")
    c = order["customer"]
    site = report._biz(report.load_business(), "website", "https://ineedawell.com")
    if tri["decision"] in ("out_of_scope", "unparseable"):
        # High-confidence Jev verdict: skip the geocode call entirely and
        # reuse the same graceful drafts the geocode/scope checks write.
        failed = {
            "order_id": order_id, "tier": "free",
            "mode": "declined" if tri["decision"] == "out_of_scope" else "failed",
            "stage": "triage",
            "triage": {k: tri[k] for k in
                       ("address_scope", "address_scope_confidence", "genuine_p")},
            "note": tri["reason"],
        }
        with open(os.path.join(order_dir, "fulfillment_failed.json"), "w") as f:
            json.dump(failed, f, indent=2)
        if tri["decision"] == "out_of_scope":
            body = (
                f"Thanks for writing — right now the pilot only covers Connecticut "
                f"properties, and {order['property_address']} looks like it's "
                f"outside the area. I've noted your interest and I'll reach out "
                f"if we expand.\n"
            )
        else:
            body = (
                f"I couldn't pin down {order['property_address']} on the map — "
                f"could you double-check the spelling, or send a nearby "
                f"intersection or the listing link? I'll run it again right away.\n"
            )
        draft = (
            f"Subject: Re: your free well snapshot request\n"
            f"To: {c['email']}\n"
            f"\n"
            f"Hi — Marcin here, from I Need a Well ({site}).\n"
            f"\n{body}\n"
            f"— Marcin\n"
            f"I Need a Well · {site} (free pilot)\n"
        )
        with open(os.path.join(order_dir, "customer_reply_draft.txt"), "w") as f:
            f.write(draft)
        print(f"      TRIAGE DECLINED: {tri['reason']}")
        return
    if tri["decision"] == "held_for_review":
        # High-confidence junk, but never silently dropped: parked for Marcin.
        failed = {
            "order_id": order_id, "tier": "free", "mode": "held",
            "stage": "triage",
            "triage": {k: tri[k] for k in
                       ("address_scope", "address_scope_confidence", "genuine_p")},
            "note": "Held for human review — nothing sent to the customer.",
        }
        with open(os.path.join(order_dir, "fulfillment_failed.json"), "w") as f:
            json.dump(failed, f, indent=2)
        with open(os.path.join(order_dir, "triage_review.txt"), "w") as f:
            f.write(
                f"Order {order_id} held by Jev triage.\n"
                f"Address: {order['property_address']}\n"
                f"Reason: {tri['reason']}\n"
                f"Decide: run anyway (delete triage.json and re-run), or discard.\n"
            )
        print(f"      HELD FOR REVIEW: {tri['reason']}")
        return

    print("[2/6] geocode")
    try:
        geo = geocode.geocode(order, order_dir)
    except Exception as e:
        out_of_scope = isinstance(e, geocode.OutOfScopeError)
        fail = {"order_id": order_id, "tier": "free",
                "mode": "declined" if out_of_scope else "failed",
                "stage": "geocode", "error": str(e),
                "note": ("Pilot covers Connecticut only. Send the "
                         "out-of-scope email." if out_of_scope else
                         "Address could not be located. Send the not-found email.")}
        with open(os.path.join(order_dir, "fulfillment_failed.json"), "w") as f:
            json.dump(fail, f, indent=2)
        c = order["customer"]
        site = report._biz(report.load_business(), "website", "https://ineedawell.com")
        if out_of_scope:
            body = (
                f"Thanks for writing — right now the pilot only covers "
                f"Connecticut properties, and {order['property_address']} "
                f"looks like it's outside the area. I've noted your "
                f"interest and I'll reach out if we expand.\n"
            )
        else:
            body = (
                f"I couldn't pin down {order['property_address']} on the "
                f"map — could you double-check the spelling, or send a "
                f"nearby intersection or the listing link? I'll run it "
                f"again right away.\n"
            )
        draft = (
            f"Subject: Re: your free well snapshot request\n"
            f"To: {c['email']}\n"
            f"\n"
            f"Hi \u2014 Marcin here, from I Need a Well ({site}).\n"
            f"\n{body}\n"
            f"— Marcin\n"
            f"I Need a Well \u00b7 {site}\n"
        )
        with open(os.path.join(order_dir, "customer_reply_draft.txt"), "w") as f:
            f.write(draft)
        print(f"      FAILED (graceful): {e}")
        print("      Wrote not-found reply draft instead of a snapshot.")
        return
    print(f"      {geo['lat']:.5f}, {geo['lon']:.5f} ({geo['source']})")

    # Scope guard (Codex R02): structured geocode fields only — the
    # submitted address text is never state evidence.
    scope_reason = scope_check(geo, order)
    if scope_reason is not None:
        fail = {"order_id": order_id, "tier": "free", "mode": "declined",
                "stage": "scope_check", "geocoded_as": geo.get("display_name"),
                "reason": scope_reason,
                "note": "Pilot covers Connecticut only. Send the out-of-scope email."}
        with open(os.path.join(order_dir, "fulfillment_failed.json"), "w") as f:
            json.dump(fail, f, indent=2)
        c = order["customer"]
        draft = (
            f"Subject: Re: your free well snapshot request\n"
            f"To: {c['email']}\n"
            f"\n"
            f"Hi — Marcin here, from I Need a Well ({site}).\n"
            f"\n"
            f"Thanks for writing — right now the pilot only covers Connecticut "
            f"properties, and {order['property_address']} looks like it's "
            f"outside the area. I've noted your interest and I'll reach out "
            f"if we expand.\n"
            f"\n"
            f"— Marcin\n"
            f"I Need a Well \u00b7 {site}\n"
        )
        with open(os.path.join(order_dir, "customer_reply_draft.txt"), "w") as f:
            f.write(draft)
        print(f"      OUT OF SCOPE: geocoded as {geo.get('display_name')}")
        print("      Wrote out-of-scope reply draft instead of a snapshot.")
        return

    print("[3/6] nearby wells")
    wells = nearby_wells.nearby_wells(order, geo, order_dir, BASE)
    print(f"      {wells['total_water_supply_wells']} wells, "
          f"{wells['wells_with_depth_yield']} with depth/yield")

    print("[4/6] water quality")
    wq = water_quality.water_quality(order, geo, order_dir)
    print(f"      arsenic rows={wq['arsenic']['result_rows']}, "
          f"uranium rows={wq['uranium']['result_rows']}")

    print("[5/6] setback screen")
    sb = setbacks.setback_screen(order, order_dir)

    print("[6/6] free snapshot")
    screen_path = screen.build_screen(order, geo, wells, wq, sb, order_dir)
    screen_json_path = screen.build_screen_json(order, geo, wells, wq, sb, order_dir)
    print(f"      {screen_path}")
    print(f"      {screen_json_path}")

    c = order["customer"]
    # The email body carries the 60-second answer inline — the attachment
    # (or link) is the evidence. Nobody should have to open a file to get
    # the verdict.
    vcls, vword, vnote = report._verdict(wells, order.get("parcel_acres"))
    band = report.cost_band(wells)
    if band:
        band_lines = "\n".join(
            f"  - {s['label']}: ${s['estimate_usd']:,}"
            f" ({s['depth_ft']:g} ft x about ${s['rate_usd_per_ft']}/ft)"
            + ("+" if s.get("open_ended") else "")
            for s in band["scenarios"]
        )
        band_block = (
            f"\nWhat to budget (planning band, driller quotes are the real numbers):\n"
            f"{band_lines}\n"
            f"  Dry-hole contingency to carry: +${band['dry_hole_contingency_usd'][0]:,}"
            f"-${band['dry_hole_contingency_usd'][1]:,} "
            f"(you pay for the attempt even if it's dry)\n"
        )
    else:
        band_block = "\nCost band: not enough nearby depth records to price — the snapshot explains why.\n"
    flags = wq.get("flags") or []

    def _flag_line(f):
        if isinstance(f, dict):
            return f"  - {f.get('title', '')}: {f.get('summary', '')}"
        return f"  - {f}"

    flags_block = "\n".join(_flag_line(f) for f in flags) if flags else "  (none flagged)"
    # $39 dossier-intent pointer: parked 2026-10-24 (pipeline.flags).
    # The snapshot no longer carries the question while parked, so the
    # plain-text email must not point at it either.
    dossier_note = (
        "(There's also a question inside the snapshot about a $39 full "
        "report — an honest YES or NO helps more than politeness.)\n"
        "\n"
    ) if FLAGS.DOSSIER_INTENT_ENABLED else ""
    site = report._biz(report.load_business(), "website", "https://ineedawell.com")
    draft = (
        f"Subject: Your free well snapshot for {order['property_address']}\n"
        f"To: {c['email']}\n"
        f"\n"
        f"Hi — Marcin here, from I Need a Well ({site}).\n"
        f"\n"
        f"Your free well snapshot for {order['property_address']} is attached — "
        f"here's the 60-second answer so you don't even have to open it:\n"
        f"\n"
        f"VERDICT: {vword}\n"
        f"{vnote}\n"
        f"{band_block}\n"
        f"Water: what to test (the lab confirms all of this — the state-required panel covers the big ones):\n"
        f"{flags_block}\n"
        f"\n"
        f"The attached snapshot has the full evidence: nearby well records, "
        f"setback top-lines, next steps, and where every number came from.\n"
        f"\n"
        f"One favor, since this is a free pilot: reply and tell me what was "
        f"most useful and what was missing. That decides what gets built next.\n"
        f"\n"
        f"{dossier_note}"
        f"And whether you reply or not — thanks for reading. That's what keeps this free.\n"
        f"\n"
        f"— Marcin\n"
        f"I Need a Well · {site} (free pilot)\n"
    )
    with open(os.path.join(order_dir, "delivery_email_draft.txt"), "w") as f:
        f.write(draft)
    # Designed HTML version of the same email (inline styles, Gmail-safe).
    email_html = report.build_delivery_html(order, wells, wq)
    with open(os.path.join(order_dir, "delivery_email.html"), "w") as f:
        f.write(email_html)

    superseded = intake.supersede_failure_marker(order_dir)
    if superseded is not None:
        print(f"      superseded earlier failure marker "
              f"(mode={superseded['mode']}, stage={superseded['stage']})")
    summary = {
        "order_id": order_id,
        "tier": "free",
        "mode": "fulfilled",
        "generated": date.today().isoformat(),
        "artifacts": {
            "order": "order.json", "geocode": "geocode.json",
            "wells": "nearby_wells.json", "water_quality": "water_quality.json",
            "setbacks": "setbacks.json",
            "screen": "screen.html", "screen_json": "screen.json",
            "delivery_draft": "delivery_email_draft.txt",
            "delivery_email_html": "delivery_email.html",
        },
        "note": "Free tier contacts nobody. Public data in, one HTML file out.",
    }
    if superseded is not None:
        summary["superseded_failure"] = superseded
    with open(os.path.join(order_dir, "pipeline_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\nDone. Snapshot: {screen_path}")


if __name__ == "__main__":
    main()
