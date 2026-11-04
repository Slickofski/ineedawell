#!/usr/bin/env python3
"""Regression tests for Codex R12 (2026-10-22 P2 pass): the delivery
email promises the packet attached; the mailer must actually attach
it — or not send.

Before the fix, send_email had no attachment capability at all:
captured MIME was text/plain with zero attachments while the copy
promised "Your prefilled permit packet is attached."

Contract:
  - send_email(attachment_paths=[...]) attaches the files and binds
    them to the send: intent + outcome log entries carry each
    attachment's filename and SHA-256;
  - an unreadable/missing attachment aborts BEFORE intent and SMTP —
    no dangling intent, no text-only send carrying the promise;
  - ingest_quotes sends the delivery email only when packet.md
    exists in the order dir.
FAKE SMTP only. Run: python3 -m unittest test_codex_r12
"""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ingest_quotes
from pipeline import mailer
from test_codex_r10 import FakeSMTP, CFG

BASE = os.path.dirname(os.path.abspath(__file__))
PACKET = b"# Synthetic permit packet\n\nSetbacks, evidence, steps.\n"


class TestMailerAttachments(unittest.TestCase):
    def setUp(self):
        FakeSMTP.instances = []
        self.tmp = tempfile.TemporaryDirectory()
        self.order_dir = self.tmp.name
        self.packet = os.path.join(self.order_dir, "packet.md")
        with open(self.packet, "wb") as f:
            f.write(PACKET)

    def tearDown(self):
        self.tmp.cleanup()

    def test_attachment_sent_and_hash_bound(self):
        with patch.object(mailer.smtplib, "SMTP", FakeSMTP):
            res = mailer.send_email("buyer@example.invalid", "Delivery",
                                    "see attached", CFG, self.order_dir,
                                    "delivery",
                                    attachment_paths=[self.packet])
        self.assertTrue(res["ok"])
        msg = FakeSMTP.all_sent()[0]
        atts = list(msg.iter_attachments())
        self.assertEqual(1, len(atts))
        self.assertEqual("packet.md", atts[0].get_filename())
        self.assertEqual(PACKET, atts[0].get_payload(decode=True))
        with open(os.path.join(self.order_dir, "send_log.json")) as f:
            log = json.load(f)
        expect = hashlib.sha256(PACKET).hexdigest()
        for entry in log:
            self.assertEqual(expect, entry["attachments"][0]["sha256"])
            self.assertEqual("packet.md",
                             entry["attachments"][0]["filename"])

    def test_missing_attachment_aborts_before_anything(self):
        with patch.object(mailer.smtplib, "SMTP", FakeSMTP):
            with self.assertRaises((FileNotFoundError, ValueError)):
                mailer.send_email(
                    "buyer@example.invalid", "Delivery", "body", CFG,
                    self.order_dir, "delivery",
                    attachment_paths=[os.path.join(self.order_dir,
                                                   "nope.md")])
        self.assertEqual([], FakeSMTP.all_sent())
        self.assertFalse(os.path.exists(
            os.path.join(self.order_dir, "send_log.json")))


class TestIngestAttachesPacket(unittest.TestCase):
    REF = "ORD-TEST-R12-A"

    def setUp(self):
        FakeSMTP.instances = []
        self.order_dir = os.path.join(BASE, "orders", self.REF)
        os.makedirs(self.order_dir, exist_ok=True)
        order = {"order_id": self.REF,
                 "customer": {"name": "Synthetic Buyer",
                              "email": "buyer@example.invalid",
                              "phone": "(860) 555-0100"},
                 "property_address": "1 Test St, Testville, CT 00000",
                 "paid": True, "test": False,
                 "stripe_payment_id": "pi_synthetic",
                 "tier": "concierge", "amount_usd": 100}
        with open(os.path.join(self.order_dir, "order.json"), "w") as f:
            json.dump(order, f)
        replies = {"replies": [
            {"company": c, "total_price_usd": 15000,
             "depth_included_ft": 400, "price_per_ft_beyond_usd": None,
             "lead_time_weeks": None, "warranty_terms": None,
             "exclusions": None, "notes": None}
            for c in ("Acme Wells", "Best Bore")]}
        self.replies_path = os.path.join(self.order_dir, "replies.json")
        with open(self.replies_path, "w") as f:
            json.dump(replies, f)

    def tearDown(self):
        shutil.rmtree(self.order_dir, ignore_errors=True)

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

    def test_comparison_delivery_carries_packet(self):
        with open(os.path.join(self.order_dir, "packet.md"), "wb") as f:
            f.write(PACKET)
        out = self._run()
        sent = FakeSMTP.all_sent()
        self.assertEqual(1, len(sent))
        atts = list(sent[0].iter_attachments())
        self.assertEqual(1, len(atts))
        self.assertEqual(PACKET, atts[0].get_payload(decode=True))
        self.assertIn("packet attached", out)

    def test_missing_packet_blocks_send(self):
        out = self._run()
        self.assertEqual([], FakeSMTP.all_sent())
        self.assertIn("NOT SENT", out)
        self.assertIn("packet", out.lower())


if __name__ == "__main__":
    unittest.main()
