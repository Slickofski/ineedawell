#!/usr/bin/env python3
"""Regression tests for Codex R15, engine side (2026-10-22 P2 pass):
malformed measurements must not corrupt statistics or output.

Before the fix, _as_float accepted float("nan") / float("inf") /
negatives as measurements: a NaN could become a property's
published metric, nonstandard NaN/Infinity tokens could reach the
JSON output, and a filing dated 9202 won "newest reported value"
selection over real filings.

Contract (mirrors the fulfillment pipeline's _num, Codex R01):
a measurement is a finite number >= 0; zero yield is preserved as
evidence; anything else is unknown (None). An unparseable or
future filing date cannot win recency. Output JSON is strict
(json.dump with allow_nan=False + these tests parse with
NaN/Infinity rejected).
Run: python3 -m unittest test_codex_r15 (from this directory)
"""
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wellscreen


def _strict_loads(text):
    def _reject(x):
        raise ValueError(f"nonstandard JSON constant: {x}")
    return json.loads(text, parse_constant=_reject)


def _row(addr, date, casing, yld):
    return {"latitude": 41.001, "longitude": -72.0,
            "well_address": addr, "well_city": "Testville",
            "date_of_boringabandonment": date,
            "casing_length": casing, "casing_into_bedrock": "10",
            "yield_test_result": yld, "yield_test_type": "Pumped",
            "type_of_report": "Water Supply Well"}


ROWS = [
    # Same property: a real 2024 filing, then a 9202 filing carrying
    # poison values — the poison must not win "newest".
    _row("1 A St", "2024-01-01", "100", "10"),
    _row("1 A St", "9202-01-01", "NaN", "Infinity"),
    # Negative casing must be unknown, not a published metric.
    _row("2 B St", "2024-06-01", "-50", "5"),
    # Zero yield is evidence and must survive.
    _row("3 C St", "2024-06-01", "150", "0"),
]


class TestMalformedMeasurements(unittest.TestCase):
    def _run(self):
        argv = ["wellscreen.py", "1 A St, Testville, CT"]
        out = io.StringIO()
        with patch.object(sys, "argv", argv), \
                patch.object(wellscreen, "geocode",
                             return_value=(41.0, -72.0, "matched", {})), \
                patch.object(wellscreen, "ct_wells",
                             return_value=(ROWS, False)), \
                patch.object(wellscreen, "get",
                             return_value={"features": []}), \
                redirect_stdout(out):
            wellscreen.main()
        return _strict_loads(out.getvalue())

    def test_output_is_strict_json(self):
        self._run()  # raises if NaN/Infinity tokens appear

    def test_poison_values_do_not_win(self):
        out = self._run()
        a = next(p for p in out["ct_wells"]
                 if p["address"] == "1 A St")
        # Property entries carry the filing's raw values (provenance);
        # what matters is they are the 2024 filing's, not the poison.
        self.assertEqual("100", str(a["casing_ft"]))
        self.assertEqual("10", str(a["yield_test_result"]))
        # Averages: reported yields are 10, 5 and the preserved zero
        # -> mean 5.0 over 3 properties (zero is evidence, counted).
        self.assertEqual(5.0, out["ct_avg_yield"])

    def test_negative_casing_is_unknown(self):
        out = self._run()
        b = next(p for p in out["ct_wells"]
                 if p["address"] == "2 B St")
        self.assertIsNone(b["casing_ft"])


if __name__ == "__main__":
    unittest.main()
