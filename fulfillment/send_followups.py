#!/usr/bin/env python3
"""Follow-up sender — sends due follow-up drafts (day 5 / day 10).

Usage:
  python3 send_followups.py --order-id ORD-20260918-... [--live] [--as-of-day N]

Reads orders/<id>/send_log.json + outbox/<id>/ drafts. A follow-up is due when
its scheduled day has passed since the initial send and it hasn't been sent.
--as-of-day simulates elapsed days (for testing); default is real elapsed time.
Dry-run (default): logs what WOULD send, sends nothing.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import mailer  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
SCHEDULE = {"followup1": 5, "followup2": 10}


def main():
    ap = argparse.ArgumentParser(description="Send due follow-ups")
    ap.add_argument("--order-id", required=True)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--as-of-day", type=int, default=None,
                    help="simulate N days elapsed since initial send")
    args = ap.parse_args()

    from pipeline import intake as _intake
    order_dir, outbox_dir = _intake.order_paths(BASE, args.order_id)
    order = json.load(open(os.path.join(order_dir, "order.json")))
    log = json.load(open(os.path.join(order_dir, "send_log.json")))

    sent_initial = {e["to"]: e for e in log
                    if e["kind"] == "initial" and e["mode"] == "live" and e.get("ok")}
    if not sent_initial:
        print("No live initial sends on record — nothing to follow up. "
              "(Follow-ups only follow live-sent initials.)")
        return

    allowed, reason = mailer.gate(order, args.live)
    cfg, _ = mailer.smtp_config()
    now = datetime.now(timezone.utc)

    for to_addr, entry in sent_initial.items():
        sent_at = datetime.fromisoformat(entry["at"])
        elapsed = args.as_of_day if args.as_of_day is not None \
            else (now - sent_at).days
        # A slot is consumed ONLY by a successful LIVE send (R11):
        # dry-run previews log ok=True entries with mode "dry_run",
        # and failed live attempts log ok=False — neither may
        # suppress the promised follow-up.
        done = {e["kind"] for e in log
                if e.get("to") == to_addr and e["kind"].startswith("followup")
                and e.get("mode") == "live" and e.get("ok")}
        slug = None
        for item_file in os.listdir(outbox_dir):
            if item_file.endswith("_initial.txt"):
                t, _, _ = mailer.parse_draft(os.path.join(outbox_dir, item_file))
                if t == to_addr:
                    slug = item_file.replace("_initial.txt", "")
        for kind, due_day in SCHEDULE.items():
            if kind in done or elapsed < due_day:
                continue
            draft_path = os.path.join(outbox_dir, f"{slug}_{kind}.txt")
            to, subject, body = mailer.parse_draft(draft_path)
            if allowed:
                res = mailer.send_email(to, subject, body, cfg, order_dir, kind)
                print(f"{'SENT' if res['ok'] else 'FAILED'} {kind} -> {to}"
                      + ("" if res["ok"] else f": {res['error']}"))
            else:
                mailer.dry_skip(to, subject, order_dir, kind,
                                f"{reason} (due at day {due_day}, {elapsed}d elapsed)")
                print(f"dry-run: {kind} -> {to} is due ({elapsed}d elapsed) — not sent")


if __name__ == "__main__":
    main()
