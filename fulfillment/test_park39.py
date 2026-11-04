#!/usr/bin/env python3
"""Regression tests for the parked $39 dossier-intent question
(Marcin, 2026-10-24: wording OUT of every customer surface, plumbing
kept in the tree but OFF behind pipeline.flags.DOSSIER_INTENT_ENABLED).

Contract:
- Flag OFF (the shipped default): the snapshot page renders no
  "Want the full dossier?" card, the delivery email asks ONE favor
  (feedback only), and the plain-text draft carries no pointer to a
  question that no longer exists. The strings "$39", "full report",
  "full dossier" appear in NO customer-facing output.
- Flag ON (monkeypatched): all three surfaces restore the question
  exactly as it ran before the park.

Run: python3 -m unittest test_park39
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_screen
from pipeline import flags, report, screen

BASE = os.path.dirname(os.path.abspath(__file__))
# Fixtures vendored 2026-10-29 (byte-exact copies of the synthetic
# 2026-09-18 test order's data files) so this suite runs on a clean
# checkout — fulfillment/orders/ is never synced. The one remaining
# orders/ path in this file is the ORD-TEST-PARK39 scratch output
# dir, which run_screen creates and tearDown removes.
FIXTURE_DIR = os.path.join(
    BASE, "test_fixtures", "park39-193-wells-rd")
BANNED = ("$39", "full report", "full dossier")

ORDER = {"order_ref": "ORD-TEST-PARK39",
         "property_address": "193 Wells Rd, Broad Brook, CT 06016",
         "name": "Synthetic Tester",
         "email": "synthetic@example.invalid"}

GEO = {"lat": 41.92, "lon": -72.54, "source": "synthetic",
       "display_name": "193 Wells Rd, Broad Brook, CT",
       "state_code": "CT", "precision": "house",
       "house_number": "193", "road": "Wells Rd",
       "street": "Wells Rd"}


def _load(name):
    with open(os.path.join(FIXTURE_DIR, name)) as f:
        return json.load(f)


def _assert_clean(case, text, surface):
    for bad in BANNED:
        case.assertNotIn(bad, text,
                         f"{bad!r} leaked into {surface} while parked")
        case.assertNotIn(bad, text.lower(),
                         f"{bad!r} leaked into {surface} while parked")


class TestParked39(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.order = _load("order.json")
        cls.geo = _load("geocode.json")
        cls.wells = _load("nearby_wells.json")
        cls.wq = _load("water_quality.json")
        cls.setbacks = _load("setbacks.json")

    def _snapshot_html(self, tmp):
        path = screen.build_screen(self.order, self.geo, self.wells,
                                   self.wq, self.setbacks, tmp)
        with open(path) as f:
            return f.read()

    def test_flag_defaults_off(self):
        self.assertFalse(flags.DOSSIER_INTENT_ENABLED)

    def test_snapshot_and_email_clean_while_parked(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = self._snapshot_html(tmp)
            _assert_clean(self, html, "snapshot HTML")
            self.assertNotIn("Want the full dossier?", html)
            email = report.build_delivery_html(self.order, self.wells,
                                               self.wq)
            _assert_clean(self, email, "delivery email HTML")
            self.assertIn("One favor", email)
            self.assertNotIn("Two favors", email)
            # Favor #1 (feedback) survives untouched.
            self.assertIn("Reply and tell me what was most useful", email)

    def test_flag_on_restores_question_exactly(self):
        with patch.object(flags, "DOSSIER_INTENT_ENABLED", True):
            with tempfile.TemporaryDirectory() as tmp:
                html = self._snapshot_html(tmp)
                self.assertIn("Want the full dossier?", html)
                self.assertIn("$39", html)
                email = report.build_delivery_html(self.order, self.wells,
                                                   self.wq)
                self.assertIn("Two favors", email)
                self.assertIn("$39", email)


class TestParked39PlainTextDraft(unittest.TestCase):
    """The run_screen plain-text draft must not point at the parked
    question (and must point at it again when the flag flips on)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.order_file = os.path.join(self._tmp.name, "order.json")
        with open(self.order_file, "w") as f:
            json.dump(ORDER, f)
        self.order_dir = os.path.join(BASE, "orders", "ORD-TEST-PARK39")

    def tearDown(self):
        shutil.rmtree(self.order_dir, ignore_errors=True)
        self._tmp.cleanup()

    def _run_and_read_draft(self):
        tri = {"decision": "proceed", "reason": "synthetic",
               "address_scope": None, "address_scope_confidence": None,
               "genuine_p": None}
        patches = [
            patch.object(run_screen.jev_triage, "triage",
                         return_value=tri),
            patch.object(sys, "argv",
                         ["run_screen.py", "--order-file",
                          self.order_file]),
            patch.object(run_screen.geocode, "geocode",
                         return_value=dict(GEO)),
            patch.object(run_screen.nearby_wells, "nearby_wells",
                         return_value=_load("nearby_wells.json")),
            patch.object(run_screen.water_quality, "water_quality",
                         return_value=_load("water_quality.json")),
            patch.object(run_screen.setbacks, "setback_screen",
                         return_value=_load("setbacks.json")),
        ]
        for p in patches:
            p.start()
        try:
            run_screen.main()
        finally:
            for p in patches:
                p.stop()
        with open(os.path.join(self.order_dir,
                               "delivery_email_draft.txt")) as f:
            return f.read()

    def test_draft_clean_while_parked(self):
        draft = self._run_and_read_draft()
        _assert_clean(self, draft, "plain-text draft")
        # The one remaining favor (feedback) is still asked.
        self.assertIn("One favor, since this is a free pilot", draft)

    def test_draft_restores_pointer_when_flag_on(self):
        with patch.object(flags, "DOSSIER_INTENT_ENABLED", True):
            draft = self._run_and_read_draft()
            self.assertIn("question inside the snapshot about a $39",
                          draft)


if __name__ == "__main__":
    unittest.main()
