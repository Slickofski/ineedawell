#!/usr/bin/env python3
"""Tests for the cost-source pass (2026-11-04).

The defect: the snapshot multiplied depth by $25/$45/$65 typed into
report.py and attributed them to unnamed "2026 published national
guide ranges" — a range HomeGuide publishes as a COMPLETE-SYSTEM
price (pump, casing, wiring included) while the report excluded the
pump and tank as extras. The fix re-scopes the band to "drilling and
casing", moves every figure into the rules table with citation,
confidence, and verified date (the setbacks' standard), and names
the sources in the customer copy.

These tests pin:
  (a) the estimates READ the rules table — mutating a rate in a
      table copy changes the estimate, so no literal can creep back;
  (b) a table missing its cost entries fails LOUDLY (RuntimeError),
      never a silent fallback to literals;
  (c) the rendered snapshot, screen.json, packet, and permit roadmap
      carry the named sources and the drilling-and-casing scope, and
      none of the old framing (unnamed "guide ranges", statewide
      "typically about $200") survives.

Run: python3 -m unittest test_cost_sourcing -v  (from fulfillment/)
"""
import copy
import json
import os
import statistics as _stats
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import packet, permit_roadmap, report, rules, screen, setbacks  # noqa: E402

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "test_fixtures", "park39-193-wells-rd")


def _agg(specs):
    """Aggregate wells dict in the shape nearby_wells() returns —
    same construction as test_tilde_sweep's neighborhoods."""
    ws = [{"address": f"{i} Synthetic St", "depth_ft": d, "yield_gpm": y,
           "static_level_ft": s, "pumping_level_ft": p,
           "yield_test_type": "Compressed Air", "yield_test_hours": 1.0,
           "company": "X", "date": "2024-01-01"}
          for i, (d, y, s, p) in enumerate(specs)]
    ys = [w["yield_gpm"] for w in ws]
    ds = [w["depth_ft"] for w in ws]
    return {"wells": ws, "total_water_supply_wells": len(ws),
            "yield_gpm": {"min": min(ys), "max": max(ys),
                          "median": _stats.median(ys)},
            "depth_ft": {"min": min(ds), "max": max(ds),
                         "median": _stats.median(ds)},
            "yield_count": len(ys), "depth_count": len(ds),
            "zero_yield_count": 0}


WELLS = _agg([(500, 12, 60, 110), (520, 20, 55, 120),
              (480, 16, 70, 130)])  # depth min/med/max = 480/500/520


class _TableSwap(unittest.TestCase):
    """Swap rules._CACHE for a mutated deep copy; always restore."""

    def setUp(self):
        rules.load()
        self._real_cache = rules._CACHE
        self.addCleanup(self._restore)

    def _restore(self):
        rules._CACHE = self._real_cache

    def swap(self, mutate):
        table = copy.deepcopy(self._real_cache)
        mutate(table)
        rules._CACHE = table


class TestRatesComeFromTheTable(_TableSwap):
    def test_baseline_estimates_use_table_rates(self):
        band = report.cost_band(WELLS)
        self.assertEqual(
            [s["rate_usd_per_ft"] for s in band["scenarios"]],
            [25, 45, 65])
        self.assertEqual(band["scenarios"][1]["estimate_usd"],
                         round(500 * 45))
        self.assertEqual(band["dry_hole_contingency_usd"],
                         [10000, 25000])

    def test_mutating_the_table_rate_changes_the_estimate(self):
        def bump(table):
            for r in table["rules"]:
                if r["id"] == "ct_cost_drilling_casing_plan":
                    r["rate_usd_per_ft"] = 99
        self.swap(bump)
        band = report.cost_band(WELLS)
        self.assertEqual(band["scenarios"][1]["rate_usd_per_ft"], 99)
        self.assertEqual(band["scenarios"][1]["estimate_usd"],
                         round(500 * 99))

    def test_mutating_the_table_fee_changes_the_copy(self):
        def bump(table):
            for r in table["rules"]:
                if r["id"] == "ct_fee_well_permit_ncdhd":
                    r["amount_usd"] = 210
        self.swap(bump)
        html = report._cost_section(WELLS)
        self.assertIn("$210 in the North Central District", html)


class TestMissingEntriesFailLoudly(_TableSwap):
    def _drop_cost(self, table):
        table["rules"] = [r for r in table["rules"]
                          if r["applies_to"] != "cost"]

    def test_cost_band_raises_without_cost_entries(self):
        self.swap(self._drop_cost)
        with self.assertRaises(RuntimeError):
            report.cost_band(WELLS)

    def test_accessors_raise_without_entries(self):
        self.swap(self._drop_cost)
        with self.assertRaises(RuntimeError):
            rules.cost_rate("plan_on")
        with self.assertRaises(RuntimeError):
            rules.fee_amount("ct_fee_well_permit_ncdhd")


class TestRenderedSurfacesCarryTheSources(unittest.TestCase):
    def _load(self, name):
        with open(os.path.join(FIXTURE_DIR, name)) as f:
            return json.load(f)

    def _render(self, tmp):
        order = self._load("order.json")
        geo = self._load("geocode.json")
        wells = self._load("nearby_wells.json")
        wq = self._load("water_quality.json")
        sb = setbacks.setback_screen(order, tmp)
        screen.build_screen(order, geo, wells, wq, sb, tmp)
        screen.build_screen_json(order, geo, wells, wq, sb, tmp)
        packet_path = packet.build_packet(order, geo, wells, wq, sb, tmp)
        return packet_path

    def test_snapshot_cost_section_names_sources_and_scope(self):
        html = report._cost_section(WELLS)
        self.assertIn("How we got these numbers", html)
        for name in ("Angi", "CostHelper",
                     "National Ground Water Association"):
            self.assertIn(name, html)
        self.assertIn("drilling and casing", html)
        self.assertIn("not a published figure", html)
        self.assertNotIn("published national guide ranges", html)
        self.assertNotIn("typically about $200", html)
        self.assertNotIn("~", html)
        self.assertNotIn("≈", html)

    def test_screen_json_carries_sourced_cost_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._render(tmp)
            data = json.loads(
                Path(tmp, "screen.json").read_text())
        band = data["cost_band_usd"]
        self.assertTrue(band["scope"].startswith("Drilling and casing"))
        self.assertEqual(len(band["sources"]), 3)
        self.assertIn("Angi", band["sources"][2])
        for s in band["scenarios"]:
            self.assertTrue(s["source"], s["label"])
            self.assertIn(s["confidence"], ("GUIDANCE", "OPINION"))
        self.assertNotIn("published national guide ranges",
                         band["method"])

    def test_packet_and_roadmap_name_the_district_fees(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet_path = self._render(tmp)
            packet_text = Path(packet_path).read_text()
        self.assertIn(
            "$250 per lot, fee schedule effective July 1, 2026",
            packet_text)
        self.assertIn(
            "$200, fee schedule effective July 1, 2026", packet_text)
        self.assertIn("Fees vary by district", packet_text)
        self.assertNotIn("typ. about $", packet_text)
        steps = permit_roadmap.roadmap("East Windsor")
        detail = next(s["detail"] for s in steps
                      if s["step"] == "File the well permit")
        self.assertIn("The fee is set by the district, not the state",
                      detail)
        self.assertIn("$200 in the North Central District", detail)
        self.assertIn("$150 in Bloomfield's district", detail)
        self.assertNotIn("typically runs around $200", detail)


if __name__ == "__main__":
    unittest.main()
