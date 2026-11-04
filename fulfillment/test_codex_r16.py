#!/usr/bin/env python3
"""Regression tests for Codex R16 (2026-10-22 P2 pass): the
statistical fallback town must not become the property's town.

Before the fix, nearby_wells overwrote the geocoded property town
with whichever neighboring town supplied the depth anchor, and
screen.py fed that value to permit_authority()/roadmap() — a
Windsor property got East Windsor's health authority (NCDHD)
instead of Windsor's own.

Contract:
  - result["town"] is ALWAYS the property's geocoded town;
  - the anchor is reported separately as result["stats_town"],
    with town_stats_fallback marking when they differ;
  - jurisdiction consumers (permit_authority) therefore see the
    property town, while statistical consumers can label the
    anchor's numbers with the anchor's name.
Run: python3 -m unittest test_codex_r16
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import nearby_wells, permit_roadmap


def _rec(city, depth, yld="10"):
    return {"type_of_report": "Water Supply Well",
            "depth_of_well": str(depth), "yield_test_result": yld,
            "well_city": city, "well_address": "1 Synthetic Rd",
            "date_of_boringabandonment": "2025-01-01"}


class _Resp:
    def __init__(self, rows):
        self._rows = rows

    def raise_for_status(self):
        pass

    def json(self):
        return self._rows


def _fake_get(url, params=None, headers=None, timeout=None):
    where = (params or {}).get("$where", "")
    if "within_circle" in where:
        # Local sample: wells driller-reported as East Windsor.
        return _Resp([_rec("East Windsor", 200 + 10 * i)
                      for i in range(6)])
    if "WINDSOR'" in where and "EAST" not in where:
        # The property's own town: only 2 records — too thin to anchor.
        return _Resp([_rec("Windsor", 150), _rec("Windsor", 170)])
    if "EAST WINDSOR" in where:
        return _Resp([_rec("East Windsor", 100 + i) for i in range(30)])
    return _Resp([])


class TestFallbackTownSeparation(unittest.TestCase):
    def _run(self):
        geo = {"lat": 41.85, "lon": -72.65, "town": "Windsor"}
        with patch.object(nearby_wells.requests, "get",
                          side_effect=_fake_get):
            return nearby_wells.nearby_wells({}, geo, "/tmp")

    def test_property_town_is_never_overwritten(self):
        result = self._run()
        self.assertEqual("Windsor", result["town"])
        self.assertEqual("East Windsor", result["stats_town"])
        self.assertTrue(result["town_stats_fallback"])
        self.assertIsNotNone(result["town_depth_ft"])

    def test_jurisdiction_uses_property_town(self):
        result = self._run()
        authority = permit_roadmap.permit_authority(result["town"])
        self.assertNotEqual(
            permit_roadmap.permit_authority("East Windsor"), authority)
        self.assertIn("Windsor", authority)


if __name__ == "__main__":
    unittest.main()
