#!/usr/bin/env python3
"""Regression tests for Codex R11 (2026-10-22 P2 pass): a dry-run
preview or a failed live attempt must not consume a follow-up slot.

Before the fix, send_followups built its "done" set from EVERY
followup entry in send_log.json — dry_skip logs an entry with
ok=True (mode dry_run), and failed live attempts log ok=False —
so merely previewing the schedule, or one failed attempt, silently
suppressed the promised follow-up forever.

Contract: a slot is done only when a LIVE send for that kind to
that recipient recorded ok=True. FAKE SMTP only; synthetic orders.
Run: python3 -m unittest test_codex_r11
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

import send_followups
from pipeline import mailer
from test_codex_r10 import FakeSMTP, CFG

BASE = os.path.dirname(os.path.abspath(__file__))
DRILLER = "driller@example.invalid"
DAYS_AGO = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()


def _draft(to, subject, body="body"):
    return f"To: {to}\nSubject: {subject}\n\n{body}"


class FollowupLayout(unittest.TestCase):
    REF = None

    def setUp(self):
        FakeSMTP.instances = []
        self.order_dir = os.path.join(BASE, "orders", self.REF)
        self.outbox_dir = os.path.join(BASE, "outbox", self.REF)
        os.makedirs(self.order_dir, exist_ok=True)
        os.makedirs(self.outbox_dir, exist_ok=True)
        with open(os.path.join(self.order_dir, "order.json"), "w") as f:
            json.dump({"order_id": self.REF, "paid": True, "test": False,
                       "stripe_payment_id": "pi_synthetic"}, f)
        with open(os.path.join(self.outbox_dir, "acme_initial.txt"), "w") as f:
            f.write(_draft(DRILLER, "Bid request"))
        for kind in ("followup1", "followup2"):
            with open(os.path.join(self.outbox_dir, f"acme_{kind}.txt"),
                      "w") as f:
                f.write(_draft(DRILLER, f"Checking in ({kind})"))

    def tearDown(self):
        shutil.rmtree(self.order_dir, ignore_errors=True)
        shutil.rmtree(self.outbox_dir, ignore_errors=True)

    def _write_log(self, extra_entries):
        log = [{"kind": "initial", "to": DRILLER, "subject": "Bid request",
                "mode": "live", "ok": True, "at": DAYS_AGO}] + extra_entries
        with open(os.path.join(self.order_dir, "send_log.json"), "w") as f:
            json.dump(log, f)

    def _run(self):
        argv = ["send_followups.py", "--order-id", self.REF, "--live",
                "--as-of-day", "30"]
        out = io.StringIO()
        with patch.object(sys, "argv", argv), \
                patch.object(mailer, "gate", return_value=(True, "live")), \
                patch.object(mailer, "smtp_config", return_value=(CFG, [])), \
                patch.object(mailer.smtplib, "SMTP", FakeSMTP), \
                redirect_stdout(out):
            send_followups.main()
        return out.getvalue()


class TestDryRunAndFailureDoNotConsume(FollowupLayout):
    REF = "ORD-TEST-R11-A"

    def test_dry_run_and_failed_entries_leave_slots_open(self):
        self._write_log([
            {"kind": "followup1", "to": DRILLER, "subject": "Checking in",
             "mode": "dry_run", "ok": True, "at": DAYS_AGO,
             "note": "preview"},
            {"kind": "followup1", "to": DRILLER, "subject": "Checking in",
             "mode": "live", "ok": False, "at": DAYS_AGO,
             "error": "SMTPException: down"},
            {"kind": "followup2", "to": DRILLER, "subject": "Checking in",
             "mode": "dry_run", "ok": True, "at": DAYS_AGO,
             "note": "preview"},
        ])
        self._run()
        subjects = sorted(m["Subject"] for m in FakeSMTP.all_sent())
        self.assertEqual(["Checking in (followup1)",
                          "Checking in (followup2)"], subjects)


class TestLiveSuccessConsumes(FollowupLayout):
    REF = "ORD-TEST-R11-B"

    def test_live_ok_followup_is_not_resent(self):
        self._write_log([
            {"kind": "followup1", "to": DRILLER, "subject": "Checking in",
             "mode": "live", "ok": True, "at": DAYS_AGO},
        ])
        self._run()
        subjects = [m["Subject"] for m in FakeSMTP.all_sent()]
        self.assertEqual(["Checking in (followup2)"], subjects)


if __name__ == "__main__":
    unittest.main()
