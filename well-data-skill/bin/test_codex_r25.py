#!/usr/bin/env python3
"""Regression tests for Codex R25 (2026-10-22 P2 pass): the
standalone engine must preserve report type and keep abandonment
filings out of the residential-well statistics.

Before the fix, main() dropped type_of_report at the border and
admitted rows by distance alone: abandonment filings merged into
the property groups and moved the well count and the mean
yield/casing.

Contract:
  - the residential cohort is type_of_report == "Water Supply Well"
    (the same separation the fulfillment pipeline implements);
  - ct_wells / averages are computed from that cohort only;
  - abandonment and other filings are counted and labeled
    separately (ct_abandonment_filings_found,
    ct_other_filings_found), and every ct_wells entry carries its
    report_type provenance.
Run: python3 -m unittest test_codex_r25 (from this directory)
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


def _row(addr, city, lat, lng, type_of_report, casing, yld,
         date="2025-01-01"):
    return {"latitude": lat, "longitude": lng, "well_address": addr,
            "well_city": city, "date_of_boringabandonment": date,
            "casing_length": casing, "casing_into_bedrock": "10",
            "yield_test_result": yld, "yield_test_type": "Pumped",
            "type_of_report": type_of_report}


ROWS = [
    # Property A: two water-supply completions (duplicate filings).
    _row("1 A St", "Testville", 41.001, -72.0, "Water Supply Well",
         "100", "10", "2024-01-01"),
    _row("1 A St", "Testville", 41.001, -72.0, "Water Supply Well",
         "120", "12", "2025-01-01"),
    # Property B: abandonment ONLY, with poison measurements.
    _row("2 B St", "Testville", 41.002, -72.0, "Abandonment",
         "999", "99"),
    # Property C: a completion AND a later abandonment filing.
    _row("3 C St", "Testville", 41.003, -72.0, "Water Supply Well",
         "200", "20", "2024-06-01"),
    _row("3 C St", "Testville", 41.003, -72.0, "Abandonment",
         "888", "88", "2025-06-01"),
]


class TestReportTypeSeparation(unittest.TestCase):
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
        return json.loads(out.getvalue())

    def test_abandonment_property_is_not_a_well(self):
        out = self._run()
        self.assertEqual(2, out["ct_wells_found"])
        self.assertEqual(2, out["ct_abandonment_filings_found"])
        self.assertEqual(0, out["ct_other_filings_found"])
        self.assertEqual(5, out["ct_filings_found"])

    def test_poison_values_do_not_move_averages(self):
        out = self._run()
        # Cohort yields: A newest 12, C 20 -> mean 16. Casings: 120, 200.
        self.assertEqual(16.0, out["ct_avg_yield"])
        self.assertEqual(160.0, out["ct_avg_casing_ft"])

    def test_report_type_provenance_kept(self):
        out = self._run()
        for p in out["ct_wells"]:
            self.assertEqual("Water Supply Well", p["report_type"])


if __name__ == "__main__":
    unittest.main()
