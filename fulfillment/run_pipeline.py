#!/usr/bin/env python3
"""Concierge fulfillment orchestrator — stages 1-7, 9, 10.

Usage:
  python3 run_pipeline.py --order '{"name":..., "email":..., "phone":..., "property_address":...}'
  python3 run_pipeline.py --order-file order.json [--live]

Default is DRY-RUN: outreach drafts are written to outbox/, nothing sends.
Live sending requires --live AND a real paid order (paid=true,
stripe_payment_id set, test=false) AND SMTP env vars (see pipeline/mailer.py).
The demo/test order can NEVER trigger a live send.

Deliverables go to the customer; nothing routes through Marcin for approval.

Guards (Codex fixes, 2026-10-21, Remy):
  - R02 follow-on: scope_check() re-verifies the geocode result's
    STRUCTURED fields via geocode.verify_geocode_result, mirroring
    run_screen.py / run_report.py — the submitted address text is
    never state evidence. A failed check writes fulfillment_failed.json
    and stops the run before any outreach or send.
  - R05: outreach messages travel as structured values from the
    manifest into the mailer (no draft-text re-parse on the send
    path), and the final recipient is verified against the selected
    driller record's email before any send; a mismatch aborts loudly.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import (  # noqa: E402
    intake, geocode, nearby_wells, water_quality, setbacks, drillers,
    outreach, packet, delivery, mailer, quotes,
)

BASE = os.path.dirname(os.path.abspath(__file__))


def scope_check(geo, order_or_address=None):
    """Independent scope guard (Codex R02): None if verified, else reason.

    Delegates to geocode.verify_geocode_result: the state comes only
    from the geocode result's STRUCTURED fields, never from the
    submitted address text or display_name substrings. Accepts an
    order dict (uses its property_address + supplied lat/lon) or a
    bare address string. Same helper as run_screen/run_report.
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
    ap = argparse.ArgumentParser(description="Concierge fulfillment pipeline")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--order", help="order JSON string")
    g.add_argument("--order-file", help="path to order JSON file")
    ap.add_argument("--live", action="store_true",
                    help="actually send email (paid orders only)")
    args = ap.parse_args()

    raw = json.loads(args.order) if args.order else json.load(open(args.order_file))

    order_id = intake.new_order_id(raw)
    order_dir, outbox_dir = intake.order_paths(BASE, order_id)
    os.makedirs(order_dir, exist_ok=True)
    os.makedirs(outbox_dir, exist_ok=True)

    print(f"[1/9] intake -> {order_id}")
    order = intake.intake(raw, order_dir)

    print("[2/9] geocode")
    try:
        geo = geocode.geocode(order, order_dir)
    except Exception as e:
        out_of_scope = isinstance(e, geocode.OutOfScopeError)
        fail = {"order_id": order_id, "tier": order.get("tier"),
                "mode": "declined" if out_of_scope else "failed",
                "stage": "geocode", "error": str(e),
                "note": ("Pilot covers Connecticut only."
                         if out_of_scope else
                         "Address could not be verified.")}
        with open(os.path.join(order_dir, "fulfillment_failed.json"), "w") as f:
            json.dump(fail, f, indent=2)
        print(f"      FAILED (graceful): {e}")
        return
    print(f"      {geo['lat']:.5f}, {geo['lon']:.5f} ({geo['source']})")

    # Scope guard (Codex R02): structured geocode fields only — the
    # submitted address text is never state evidence.
    scope_reason = scope_check(geo, order)
    if scope_reason is not None:
        fail = {"order_id": order_id, "tier": order.get("tier"),
                "mode": "declined", "stage": "scope_check",
                "geocoded_as": geo.get("display_name"),
                "reason": scope_reason,
                "note": "Pilot covers Connecticut only."}
        with open(os.path.join(order_dir, "fulfillment_failed.json"), "w") as f:
            json.dump(fail, f, indent=2)
        print(f"      OUT OF SCOPE: geocoded as {geo.get('display_name')}")
        return

    print("[3/9] nearby wells")
    wells = nearby_wells.nearby_wells(order, geo, order_dir, BASE)
    print(f"      {wells['total_water_supply_wells']} wells, "
          f"{wells['wells_with_depth_yield']} with depth/yield")

    print("[4/9] water quality")
    wq = water_quality.water_quality(order, geo, order_dir)
    print(f"      arsenic rows={wq['arsenic']['result_rows']}, "
          f"uranium rows={wq['uranium']['result_rows']}")

    print("[5/9] setback screen")
    sb = setbacks.setback_screen(order, order_dir)

    print("[6/9] driller match")
    ranked = drillers.rank_drillers(order, geo, order_dir)
    for d in ranked["drillers"]:
        print(f"      {d['company']} ({d['town']}) — {d['distance_miles']} mi — {d['channel']}")

    print("[7/9] outreach drafts")
    manifest = outreach.build_outreach(order, geo, wells, ranked, outbox_dir)
    with open(os.path.join(order_dir, "outreach_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    allowed, reason = mailer.gate(order, args.live)
    cfg, _ = mailer.smtp_config()
    sent = 0
    print(f"      send gate: {'LIVE' if allowed else 'DRY-RUN'} ({reason})")
    for item in manifest["outreach"]:
        d = next(x for x in ranked["drillers"] if x["company"] == item["company"])
        # Structured values only (R05): no draft-text re-parse on the
        # send path. Legacy manifests without "messages" fall back to
        # the hardened parse_draft.
        msg = (item.get("messages") or {}).get("initial")
        if msg:
            to, subject, body = msg["to"], msg["subject"], msg["body"]
        else:
            to, subject, body = mailer.parse_draft(item["files"]["initial"])
        if item["channel"] != "email":
            mailer.dry_skip(to or item["to"], subject or "bid request",
                            order_dir, "initial",
                            f"manual channel ({item['channel']}) — draft only")
            continue
        # Recipient verification (R05): the final recipient must equal
        # the selected driller record's email — a mismatch raises and
        # aborts the run loudly, before any send.
        mailer.verify_recipient(to, d["email"])
        if allowed:
            res = mailer.send_email(to, subject, body, cfg, order_dir,
                                    "initial", expected_recipient=d["email"])
            sent += 1 if res["ok"] else 0
            if res.get("already_sent"):
                print(f"      ALREADY SENT (prior run) initial -> "
                      f"{d['company']} ({to}) — skipped, not resent")
            else:
                print(f"      SENT initial -> {d['company']} ({to})"
                      if res["ok"] else f"      SEND FAILED -> {d['company']}: {res['error']}")
        else:
            mailer.dry_skip(to, subject, order_dir, "initial", reason)
    print(f"      drafts in {outbox_dir}; live-sent: {sent}")

    print("[8/9] permit packet")
    packet_path = packet.build_packet(order, geo, wells, wq, sb, order_dir)
    print(f"      {packet_path}")

    print("[9/9] concierge delivery draft (awaiting quotes)")
    draft = delivery.build_delivery_draft(order)
    with open(os.path.join(order_dir, "concierge_delivery_draft.txt"), "w") as f:
        f.write(draft)

    superseded = intake.supersede_failure_marker(order_dir)
    summary = {
        "order_id": order_id,
        "mode": "live" if allowed else "dry_run",
        "send_gate_reason": reason,
        "initials_live_sent": sent,
        "artifacts": {
            "order": "order.json", "geocode": "geocode.json",
            "wells": "nearby_wells.json", "water_quality": "water_quality.json",
            "setbacks": "setbacks.json", "drillers": "drillers_ranked.json",
            "outreach_manifest": "outreach_manifest.json",
            "packet": "packet.md", "concierge_delivery_draft": "concierge_delivery_draft.txt",
            "send_log": "send_log.json",
        },
        "next": ("ingest_quotes.py --order-id %s --replies-file quote_replies.json [--live]"
                 % order_id),
    }
    if superseded is not None:
        summary["superseded_failure"] = superseded
    with open(os.path.join(order_dir, "pipeline_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\nDone. Mode: {summary['mode']}. Order dir: {order_dir}")
    print("Next: collect driller replies -> quote_replies.json -> ingest_quotes.py")


if __name__ == "__main__":
    main()
