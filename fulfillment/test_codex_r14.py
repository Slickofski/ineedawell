#!/usr/bin/env python3
"""Regression tests for Codex R14 (2026-10-22 P2 pass): duplicate
empty replies must not satisfy the two-comparable-quotes promise.

Before the fix, quotes._usable only checked `is not None`: two
entries for the SAME company with empty-string price and depth both
counted, and build_comparison took the success branch. Contract:
  - a usable quote needs a positive finite total price AND a
    positive finite included depth or per-foot rate (numeric strings
    that parse are accepted — operators transcribe replies);
  - empty strings, NaN/Infinity, zero/negative numbers, booleans
    are not numbers;
  - the MIN_QUOTES threshold counts DISTINCT companies (a revised
    reply from the same company supersedes, never adds).
Run: python3 -m unittest test_codex_r14
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import quotes

ORDER = {"property_address": "1 Test St, Testville, CT 00000"}


def reply(company, price, depth=None, per_ft=None):
    return {"company": company, "total_price_usd": price,
            "depth_included_ft": depth, "price_per_ft_beyond_usd": per_ft,
            "lead_time_weeks": None, "warranty_terms": None,
            "exclusions": None, "notes": None}


class TestUsableValidation(unittest.TestCase):
    def test_empty_strings_are_not_quotes(self):
        _, usable = quotes.build_comparison(ORDER, [
            reply("Same Co", "", depth=""),
            reply("Same Co", "", depth=""),
        ])
        self.assertEqual(0, len(usable))

    def test_nonfinite_and_nonpositive_rejected(self):
        for bad in (float("nan"), float("inf"), -1, 0, True, "call us", " "):
            with self.subTest(bad=bad):
                _, usable = quotes.build_comparison(
                    ORDER, [reply("A Co", bad, depth=400)])
                self.assertEqual(0, len(usable))

    def test_depth_side_also_validated(self):
        _, usable = quotes.build_comparison(
            ORDER, [reply("A Co", 15000, depth="")])
        self.assertEqual(0, len(usable))
        _, usable = quotes.build_comparison(
            ORDER, [reply("A Co", 15000, depth=float("nan"))])
        self.assertEqual(0, len(usable))

    def test_numeric_strings_accepted(self):
        _, usable = quotes.build_comparison(
            ORDER, [reply("A Co", "15000", depth="400")])
        self.assertEqual(1, len(usable))


class TestDistinctCompanies(unittest.TestCase):
    def test_same_company_twice_counts_once(self):
        _, usable = quotes.build_comparison(ORDER, [
            reply("Acme Wells", 15000, depth=400),
            reply("acme wells ", 15500, depth=400),
        ])
        self.assertEqual(1, len(usable))
        self.assertLess(len(usable), quotes.MIN_QUOTES)

    def test_two_distinct_companies_reach_threshold(self):
        _, usable = quotes.build_comparison(ORDER, [
            reply("Acme Wells", 15000, depth=400),
            reply("Best Bore", 14000, depth=350),
            reply("Acme Wells", 15200, depth=400),  # revision
        ])
        self.assertEqual(2, len(usable))
        self.assertGreaterEqual(len(usable), quotes.MIN_QUOTES)


if __name__ == "__main__":
    unittest.main()
