#!/usr/bin/env python3
"""Regression tests for Codex R22 (2026-10-22 P2 pass): the
shared/large-supply branches of the rules API raised KeyError.

Before the fix, governing_band returned "10_to_50" while the table's
ids use "10_50" — every non-default branch crashed — and large_supply
was mapped to a band with no withdrawal-rate input at all.

Contract: bands come from an explicit required-withdrawal-rate
model. single_family_home keeps the under-10 operating assumption;
shared/large supplies REQUIRE a required_gpm (no rate, no band —
ValueError, never a guess); the rate selects the band; an explicit map
turns the band into the rule-id suffix the table uses.
Run: python3 -m unittest test_codex_r22
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import rules


def _pollution_ft(items):
    return next(i["minimum_ft"] for i in items if i["id"] == "septic")


class TestBandBranches(unittest.TestCase):
    def test_single_family_default_unchanged(self):
        self.assertEqual(75, _pollution_ft(rules.well_setback_items()))

    def test_shared_supply_with_rate(self):
        items = rules.well_setback_items(use="shared_supply",
                                         required_gpm=25)
        self.assertEqual(150, _pollution_ft(items))

    def test_large_supply_over_50(self):
        items = rules.well_setback_items(use="large_supply",
                                         required_gpm=75)
        self.assertEqual(200, _pollution_ft(items))

    def test_shared_or_large_without_rate_refused(self):
        for use in ("shared_supply", "large_supply"):
            with self.subTest(use=use):
                with self.assertRaises(ValueError):
                    rules.well_setback_items(use=use)

    def test_rate_boundaries(self):
        self.assertEqual("under_10", rules.governing_band(required_gpm=5))
        self.assertEqual("10_to_50", rules.governing_band(required_gpm=10))
        self.assertEqual("10_to_50", rules.governing_band(required_gpm=50))
        self.assertEqual("over_50", rules.governing_band(required_gpm=51))


if __name__ == "__main__":
    unittest.main()
