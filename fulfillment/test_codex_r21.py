#!/usr/bin/env python3
"""Regression tests for Codex R21 (2026-10-22 P2 pass): abandonment
counts are evidence, not a definite public-water history.

Before the fix, _public_water_verdict asserted "public water
reached the neighborhood and houses sealed their wells as they
hooked up" from type counts alone. The filings do not say WHY a
well was sealed — replacements, demolitions and hookups all
produce abandonment records.

Contract: the verdict reports the counts, names public water as
ONE possible explanation, and routes the reader to check service
availability (water company / health district) — while keeping the
conditional PHC 19-13-B51m permit-bar warning, which is a rule
about proximity, not a claim about this parcel's history.
Run: python3 -m unittest test_codex_r21
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import report

WELLS = {"abandonment_records_in_radius": 5,
         "town_abandonment_count": 40,
         "town_water_supply_count": 3,
         "town": "Windsor", "stats_town": "Windsor"}


class TestAbandonmentLanguage(unittest.TestCase):
    def test_no_definite_public_water_history(self):
        verdict = report._public_water_verdict(WELLS)
        self.assertIsNotNone(verdict)
        _cls, headline, note = verdict
        text = headline + " " + note
        self.assertNotIn("public water reached", text)
        self.assertNotIn("sealed their wells as they hooked up", text)
        self.assertNotIn("Public-water country", headline)

    def test_counts_and_possible_explanation_present(self):
        _cls, _headline, note = report._public_water_verdict(WELLS)
        self.assertIn("5 of the filings", note)
        self.assertIn("40 abandonments", note)
        self.assertIn("one possible explanation", note.lower())
        self.assertIn("check", note.lower())

    def test_permit_bar_warning_kept_conditional(self):
        _cls, _headline, note = report._public_water_verdict(WELLS)
        self.assertIn("19-13-B51m", note)
        self.assertIn("can be barred", note)
        self.assertIn("200 ft", note)

    def test_threshold_unchanged(self):
        quiet = dict(WELLS, abandonment_records_in_radius=1,
                     town_abandonment_count=2)
        self.assertIsNone(report._public_water_verdict(quiet))


if __name__ == "__main__":
    unittest.main()
