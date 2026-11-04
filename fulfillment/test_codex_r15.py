#!/usr/bin/env python3
"""Regression tests for Codex R15, fulfillment side (2026-10-22 P2
pass): record dates need a present-date ceiling.

Numbers were already hardened at this border by the R01 donor port
(_num rejects NaN/Infinity/negatives, preserves zero yield, treats
0-ft depth as missing) and report.cost_band/_verdict were probed
with hostile stats during this pass — no crash, no negative
estimates. What remained: a record dated in the future (the
calibration set contains an erroneous year-9202 record) passed
every guard and published its date as fact.

Contract: an unparseable or future record date is neutralized to
None (the record and its measurements still count — a typo'd year
doesn't un-drill a well) and the exclusion is disclosed on the
result as records_with_unusable_dates.
Run: python3 -m unittest test_codex_r15
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import nearby_wells


class _Resp:
    def __init__(self, rows):
        self._rows = rows

    def raise_for_status(self):
        pass

    def json(self):
        return self._rows


def _rec(date, **kw):
    base = {"type_of_report": "Water Supply Well",
            "depth_of_well": "300", "yield_test_result": "10",
            "well_city": "EAST WINDSOR", "well_address": "1 X St",
            "date_of_boringabandonment": date,
            "geolocation": {"latitude": "41.92", "longitude": "-72.54"}}
    base.update(kw)
    return base


class TestDateCeiling(unittest.TestCase):
    def _run(self, rows):
        import tempfile
        geo = {"lat": 41.92, "lon": -72.54}
        with tempfile.TemporaryDirectory() as d, \
                patch.object(nearby_wells.requests, "get",
                             return_value=_Resp(rows)), \
                patch.object(nearby_wells, "_town_depth_stats",
                             return_value={"n": 0, "p90": None}):
            return nearby_wells.nearby_wells({}, geo, d)

    def test_future_date_neutralized_and_disclosed(self):
        rows = [_rec("9202-05-01"),
                _rec("2024-05-01", well_address="2 Y St")]
        out = self._run(rows)
        by_date = {w["address"]: w["date"] for w in out["wells"]}
        self.assertIsNone(by_date["1 X St EAST WINDSOR"]
                          if "1 X St EAST WINDSOR" in by_date
                          else by_date["1 X St"])
        self.assertEqual(1, out["records_with_unusable_dates"])

    def test_normal_dates_kept(self):
        out = self._run([_rec("2024-05-01")])
        self.assertEqual("2024-05-01", out["wells"][0]["date"])
        self.assertEqual(0, out["records_with_unusable_dates"])


class TestStorageJunkValues(unittest.TestCase):
    """Borehole storage path (2026-10-24): the same border discipline
    as the rest of the pipeline. NaN / Infinity / negative depth or
    static values are not measurements — storage_summary neutralizes
    them via _num (never lets them into a median, never crashes), and
    the well simply doesn't enter the storage statistic."""

    def test_nan_and_infinite_values_excluded(self):
        nan, inf = float("nan"), float("inf")
        wells = [
            {"depth_ft": nan, "static_level_ft": 50.0},
            {"depth_ft": 300.0, "static_level_ft": nan},
            {"depth_ft": inf, "static_level_ft": 50.0},
            {"depth_ft": 300.0, "static_level_ft": inf},
            {"depth_ft": 300.0, "static_level_ft": 50.0},  # sane
        ]
        s = nearby_wells.storage_summary(wells)
        self.assertEqual(s["wells_with_column"], 1)
        self.assertEqual(s["median_column_ft"], 250)
        self.assertEqual(s["median_storage_gal"], 368)

    def test_negative_values_excluded(self):
        wells = [
            {"depth_ft": -300.0, "static_level_ft": 50.0},
            {"depth_ft": 300.0, "static_level_ft": -50.0},
            {"depth_ft": 300.0, "static_level_ft": 50.0},  # sane
        ]
        s = nearby_wells.storage_summary(wells)
        self.assertEqual(s["wells_with_column"], 1)
        self.assertEqual(s["median_column_ft"], 250)

    def test_junk_only_wells_give_nulls(self):
        s = nearby_wells.storage_summary(
            [{"depth_ft": float("nan"), "static_level_ft": -1.0}])
        self.assertEqual(s["wells_with_column"], 0)
        self.assertIsNone(s["median_column_ft"])
        self.assertIsNone(s["median_storage_gal"])


if __name__ == "__main__":
    unittest.main()
