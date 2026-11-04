#!/usr/bin/env python3
"""Regression tests for Codex R13 (2026-10-22 P2 pass): early quote
ingestion must not announce an unverified refund / completed outreach.

Before the fix, ingest_quotes with 0-1 usable quotes immediately
sent the refund email — asserting the initial request plus two
follow-ups happened and a $100 refund was on its way — with no check
of the 14-day window, the follow-up history, or any refund action
(the refund is a separate manual Stripe step).

Contract: the refund notice is SENT only when all three are verified
from durable records:
  1. window closed — >= 14 days since the first live initial send;
  2. follow-ups complete — every driller who got a live initial also
     has live ok followup1 AND followup2 entries;
  3. refund confirmed — orders/<id>/refund_confirmed.json exists
     (operator-written after the Stripe refund) with amount +
     reference; the email states THAT amount/reference.
Otherwise ingest writes the operator instruction + a clearly marked
UNSENT draft, sends nothing, and prints what's missing.
FAKE SMTP only; synthetic orders. Run: python3 -m unittest test_codex_r13
"""
import io
import json
import os
import shutil
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ingest_quotes
from pipeline import mailer
from test_codex_r10 import FakeSMTP, CFG

BASE = os.path.dirname(os.path.abspath(__file__))
D1, D2 = "driller1@example.invalid", "driller2@example.invalid"
CUSTOMER = "buyer@example.invalid"


def _ts(days_ago):
    return (datetime.now(timezone.utc)
            - timedelta(days=days_ago)).isoformat()


def _entry(kind, to, days_ago, ok=True, mode="live"):
    return {"kind": kind, "to": to, "subject": "x", "mode": mode,
            "ok": ok, "at": _ts(days_ago)}


class IngestLayout(unittest.TestCase):
    REF = None

    def setUp(self):
        FakeSMTP.instances = []
        self.order_dir = os.path.join(BASE, "orders", self.REF)
        os.makedirs(self.order_dir, exist_ok=True)
        order = {"order_id": self.REF,
                 "customer": {"name": "Synthetic Buyer",
                              "email": CUSTOMER,
                              "phone": "(860) 555-0100"},
                 "property_address": "1 Test St, Testville, CT 00000",
                 "paid": True, "test": False,
                 "stripe_payment_id": "pi_synthetic",
                 "tier": "concierge", "amount_usd": 100}
        with open(os.path.join(self.order_dir, "order.json"), "w") as f:
            json.dump(order, f)
        with open(os.path.join(self.order_dir, "packet.md"), "w") as f:
            f.write("# Synthetic packet\n")
        self.replies_path = os.path.join(self.order_dir, "replies.json")
        with open(self.replies_path, "w") as f:
            json.dump({"replies": [
                {"company": "Only Co", "total_price_usd": 15000,
                 "depth_included_ft": 400,
                 "price_per_ft_beyond_usd": None,
                 "lead_time_weeks": None, "warranty_terms": None,
                 "exclusions": None, "notes": None}]}, f)

    def tearDown(self):
        shutil.rmtree(self.order_dir, ignore_errors=True)

    def _log(self, entries):
        with open(os.path.join(self.order_dir, "send_log.json"), "w") as f:
            json.dump(entries, f)

    def _run(self):
        argv = ["ingest_quotes.py", "--order-id", self.REF,
                "--replies-file", self.replies_path, "--live"]
        out = io.StringIO()
        with patch.object(sys, "argv", argv), \
                patch.object(mailer, "gate", return_value=(True, "live")), \
                patch.object(mailer, "smtp_config", return_value=(CFG, [])), \
                patch.object(mailer.smtplib, "SMTP", FakeSMTP), \
                redirect_stdout(out):
            ingest_quotes.main()
        return out.getvalue()


class TestEarlyIngestSendsNothing(IngestLayout):
    REF = "ORD-TEST-R13-EARLY"

    def test_window_open_no_followups_no_refund_record(self):
        self._log([_entry("initial", D1, 1), _entry("initial", D2, 1)])
        out = self._run()
        self.assertEqual([], FakeSMTP.all_sent())
        self.assertIn("NOT SENT", out)
        # The operator instruction is still produced.
        self.assertTrue(os.path.exists(
            os.path.join(self.order_dir, "refund_instruction.md")))
        with open(os.path.join(self.order_dir,
                               "concierge_delivery_final.txt")) as f:
            draft = f.read()
        self.assertIn("NOT SENT", draft)
        self.assertNotIn("refund on its way", draft.split("\n")[0])


class TestWindowClosedButRefundUnverified(IngestLayout):
    REF = "ORD-TEST-R13-NOREFUND"

    def test_no_refund_record_no_send(self):
        entries = []
        for d in (D1, D2):
            entries += [_entry("initial", d, 20),
                        _entry("followup1", d, 15),
                        _entry("followup2", d, 10)]
        self._log(entries)
        out = self._run()
        self.assertEqual([], FakeSMTP.all_sent())
        self.assertIn("refund", out.lower())


class TestVerifiedRefundSends(IngestLayout):
    REF = "ORD-TEST-R13-VERIFIED"

    def test_all_three_verified_sends_with_actual_amount(self):
        entries = []
        for d in (D1, D2):
            entries += [_entry("initial", d, 20),
                        _entry("followup1", d, 15),
                        _entry("followup2", d, 10)]
        self._log(entries)
        with open(os.path.join(self.order_dir,
                               "refund_confirmed.json"), "w") as f:
            json.dump({"amount_usd": 100, "reference": "re_synthetic_1",
                       "confirmed_at": "2026-10-20"}, f)
        self._run()
        sent = FakeSMTP.all_sent()
        self.assertEqual(1, len(sent))
        body = "".join(
            p.get_payload(decode=True).decode()
            for p in sent[0].walk()
            if p.get_content_type() == "text/plain")
        self.assertIn("$100", body)
        self.assertIn("re_synthetic_1", body)
        # R12: the refund notice also carries the promised packet.
        self.assertEqual(1, len(list(sent[0].iter_attachments())))


if __name__ == "__main__":
    unittest.main()
