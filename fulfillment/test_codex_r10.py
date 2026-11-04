#!/usr/bin/env python3
"""Regression tests for Codex R10 (2026-10-22 P2 pass): replaying a
successful send command must not send duplicate mail.

Before the fix, neither the initial outreach nor the quote delivery
checked prior successful sends, and SMTP acceptance happened before
the audit log write — leaving an uncertain-send replay window.

Contract (mailer.send_email, the single send seam):
  - a durable INTENT entry is appended to send_log.json BEFORE any
    SMTP activity, and the outcome entry follows it, linked by
    intent_id;
  - if a live ok entry already exists for the same (kind, recipient)
    in this order, send_email returns already_sent WITHOUT touching
    SMTP;
  - if an intent exists with NO outcome (a previous attempt's result
    was never recorded — the mail may or may not have gone out),
    send_email refuses with uncertain=True: no automatic resend,
    a human reconciles first.
FAKE SMTP only. Run: python3 -m unittest test_codex_r10
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import mailer

CFG = {"host": "fake.invalid", "port": 587, "user": "u", "password": "p",
       "from_email": "concierge@example.invalid", "from_name": "Test",
       "tls": False}


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, u, p):
        pass

    def send_message(self, msg):
        self.sent.append(msg)

    @classmethod
    def all_sent(cls):
        return [m for i in cls.instances for m in i.sent]


class TestMailerIdempotency(unittest.TestCase):
    def setUp(self):
        FakeSMTP.instances = []
        self.tmp = tempfile.TemporaryDirectory()
        self.order_dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _send(self, kind="initial", to="driller@example.invalid"):
        with patch.object(mailer.smtplib, "SMTP", FakeSMTP):
            return mailer.send_email(to, "Bid request", "body", CFG,
                                     self.order_dir, kind)

    def _log(self):
        with open(os.path.join(self.order_dir, "send_log.json")) as f:
            return json.load(f)

    def test_replay_does_not_resend(self):
        r1 = self._send()
        self.assertTrue(r1["ok"])
        self.assertEqual(1, len(FakeSMTP.all_sent()))
        r2 = self._send()
        self.assertTrue(r2["ok"])
        self.assertTrue(r2.get("already_sent"))
        self.assertEqual(1, len(FakeSMTP.all_sent()))

    def test_different_recipient_or_kind_still_sends(self):
        self._send()
        self._send(to="other@example.invalid")
        self._send(kind="delivery", to="driller@example.invalid")
        self.assertEqual(3, len(FakeSMTP.all_sent()))

    def test_intent_is_logged_before_outcome(self):
        self._send()
        log = self._log()
        kinds = [(e.get("status"), e.get("ok")) for e in log]
        self.assertEqual([("intent", None), (None, True)], kinds)
        self.assertEqual(log[0]["intent_id"], log[1]["intent_id"])

    def test_uncertain_intent_blocks_resend(self):
        # Simulate a crashed attempt: intent recorded, outcome never.
        mailer._log(self.order_dir,
                    {"kind": "initial", "to": "driller@example.invalid",
                     "subject": "Bid request", "mode": "live",
                     "status": "intent", "intent_id": "deadbeef"})
        r = self._send()
        self.assertFalse(r["ok"])
        self.assertTrue(r.get("uncertain"))
        self.assertEqual(0, len(FakeSMTP.all_sent()))

    def test_failed_attempt_may_be_retried(self):
        mailer._log(self.order_dir,
                    {"kind": "initial", "to": "driller@example.invalid",
                     "subject": "Bid request", "mode": "live",
                     "status": "intent", "intent_id": "cafe"})
        mailer._log(self.order_dir,
                    {"kind": "initial", "to": "driller@example.invalid",
                     "subject": "Bid request", "mode": "live",
                     "intent_id": "cafe", "ok": False,
                     "error": "SMTPException: down"})
        r = self._send()
        self.assertTrue(r["ok"])
        self.assertEqual(1, len(FakeSMTP.all_sent()))

    def test_dry_run_entries_do_not_block(self):
        mailer.dry_skip("driller@example.invalid", "Bid request",
                        self.order_dir, "initial", "dry-run test")
        r = self._send()
        self.assertTrue(r["ok"])
        self.assertFalse(r.get("already_sent"))
        self.assertEqual(1, len(FakeSMTP.all_sent()))


if __name__ == "__main__":
    unittest.main()
