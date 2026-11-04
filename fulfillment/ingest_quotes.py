#!/usr/bin/env python3
"""Quote ingestion — turns driller replies into the customer deliverable.

Usage:
  python3 ingest_quotes.py --order-id ORD-20260918-... --replies-file quote_replies.json [--live]

quote_replies.json:
  {"replies": [{"company": "...", "total_price_usd": 15000,
                "depth_included_ft": 400, "price_per_ft_beyond_usd": 28,
                "lead_time_weeks": "4-6", "warranty_terms": "...",
                "exclusions": "pump, tank", "notes": "..."}]}

2+ usable quotes -> quote_comparison.md + finalized delivery email.
Fewer than 2   -> refund_instruction.md; the refund-notice email is
                  sent ONLY when the refund state fully verifies
                  (Codex R13): 14-day window closed, initial + two
                  follow-ups on record for every driller, and a
                  refund_confirmed.json written by the operator after
                  the actual (manual) Stripe refund. Until then the
                  delivery artifact is a marked NOT SENT draft.
In live mode a verified delivery email sends DIRECTLY to the customer
(no approval routing), with the packet attached (R12).
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import quotes, delivery, mailer  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
WINDOW_DAYS = 14


def refund_state(order_dir):
    """Separate the refund states (R13): collecting / window-closed /
    refund-requested / refund-confirmed are different facts, each
    read from its durable record — never assumed from the calendar
    of this run. Returns (state_dict, missing_list)."""
    log = mailer._read_log(order_dir)

    def live_ok(kind=None, to=None):
        return [e for e in log if e.get("mode") == "live" and e.get("ok")
                and (kind is None or e.get("kind") == kind)
                and (to is None or e.get("to") == to)]

    initials = live_ok("initial")
    missing = []
    window_closed = False
    if not initials:
        missing.append("no live initial outreach on record")
    else:
        first = min(datetime.fromisoformat(e["at"]) for e in initials)
        window_closed = datetime.now(timezone.utc) >= first + timedelta(
            days=WINDOW_DAYS)
        if not window_closed:
            missing.append(
                f"14-day window not closed (first initial sent "
                f"{first.date().isoformat()})")
    recipients = {e["to"] for e in initials}
    followups_complete = bool(recipients) and all(
        live_ok("followup1", to) and live_ok("followup2", to)
        for to in recipients)
    if recipients and not followups_complete:
        missing.append("follow-ups not complete for every driller "
                       "(need live followup1 + followup2 each)")
    record = None
    record_path = os.path.join(order_dir, "refund_confirmed.json")
    if os.path.exists(record_path):
        try:
            with open(record_path) as f:
                parsed = json.load(f)
            if (isinstance(parsed, dict) and parsed.get("amount_usd")
                    and parsed.get("reference")
                    and parsed.get("confirmed_at")):
                record = parsed
        except (OSError, json.JSONDecodeError):
            record = None
    if record is None:
        missing.append("refund not confirmed (no valid "
                       "refund_confirmed.json — the Stripe refund is "
                       "a manual step; the notice never announces it "
                       "before it exists)")
    state = {"window_closed": window_closed,
             "followups_complete": followups_complete,
             "refund_record": record}
    return state, missing


def main():
    ap = argparse.ArgumentParser(description="Ingest driller replies")
    ap.add_argument("--order-id", required=True)
    ap.add_argument("--replies-file", required=True)
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()

    from pipeline import intake as _intake
    order_dir, _outbox_dir = _intake.order_paths(BASE, args.order_id)
    order = json.load(open(os.path.join(order_dir, "order.json")))
    replies = json.load(open(args.replies_file)).get("replies", [])

    comparison_md, usable = quotes.build_comparison(order, replies)

    sendable = True
    if len(usable) >= quotes.MIN_QUOTES:
        with open(os.path.join(order_dir, "quote_comparison.md"), "w") as f:
            f.write(comparison_md)
        final = delivery.build_delivery_draft(order, comparison_md=comparison_md)
        outcome = f"{len(usable)} usable quotes — comparison built"
    else:
        state, missing = refund_state(order_dir)
        instruction = quotes.build_refund_instruction(order, state=state)
        with open(os.path.join(order_dir, "refund_instruction.md"), "w") as f:
            f.write(instruction)
        if not missing:
            final = delivery.build_delivery_draft(
                order, refund=True, refund_record=state["refund_record"])
            outcome = (f"only {len(usable)} usable quote(s) — refund "
                       f"verified (window closed, follow-ups complete, "
                       f"refund {state['refund_record']['reference']} "
                       f"confirmed); notice sends")
        else:
            # R13: never announce what isn't verified. The artifact
            # is a marked NOT SENT draft; nothing goes to the customer.
            final = delivery.build_refund_pending_draft(order, missing)
            sendable = False
            outcome = (f"only {len(usable)} usable quote(s) — refund "
                       f"notice NOT SENT, unverified: "
                       + "; ".join(missing))

    final_path = os.path.join(order_dir, "concierge_delivery_final.txt")
    with open(final_path, "w") as f:
        f.write(final)

    allowed, reason = mailer.gate(order, args.live)
    cfg, _ = mailer.smtp_config()
    to, subject, body = mailer.parse_draft(final_path)
    if not sendable:
        if allowed:
            mailer.dry_skip(to, subject, order_dir, "delivery",
                            f"refund notice held (R13): {outcome}")
        send_note = "NOT SENT — refund state unverified (see above)"
    elif allowed:
        # R12: both delivery variants promise the packet attached.
        # A missing packet means NO send — never a text-only mail
        # carrying the promise.
        packet_path = os.path.join(order_dir, "packet.md")
        if not os.path.exists(packet_path):
            mailer.dry_skip(to, subject, order_dir, "delivery",
                            "packet.md missing — delivery email "
                            "promises an attachment (R12)")
            send_note = ("NOT SENT — packet.md missing; the delivery "
                         "email promises it as an attachment")
        else:
            res = mailer.send_email(to, subject, body, cfg, order_dir,
                                    "delivery",
                                    attachment_paths=[packet_path])
            if res.get("already_sent"):
                send_note = "ALREADY SENT by a prior run — not resent"
            elif res["ok"]:
                send_note = "SENT to customer (packet attached)"
            else:
                send_note = f"SEND FAILED: {res['error']}"
    else:
        mailer.dry_skip(to, subject, order_dir, "delivery", reason)
        send_note = f"dry-run, not sent ({reason})"

    print(outcome)
    print(f"Delivery email: {send_note}")
    print(f"Artifacts in {order_dir}")


if __name__ == "__main__":
    main()
