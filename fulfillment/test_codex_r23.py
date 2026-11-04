#!/usr/bin/env python3
"""Regression tests for Codex R23 (2026-10-22 P2 pass): a successful
retry must supersede the obsolete failure marker.

Before the fix, run_screen wrote pipeline_summary.json
(mode=fulfilled) on success but left an earlier
fulfillment_failed.json in place — held and fulfilled states
coexisted, and the pilot checklist tells operators to act on the
failure file.

Contract: on a runner's success path the marker is renamed to
fulfillment_failed.superseded.json (history preserved, live name
freed) and the summary records what it superseded. All three
runners share intake.supersede_failure_marker.
Run: python3 -m unittest test_codex_r23
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
from pipeline import intake

BASE = run_screen.BASE
ORDER = {"order_ref": "ORD-TEST-R23",
         "property_address": "183 Chamberlain Rd, East Windsor, CT 06016",
         "name": "Synthetic Tester",
         "email": "synthetic@example.invalid"}

GEO = {"lat": 41.92, "lon": -72.54, "source": "synthetic",
       "display_name": "183 Chamberlain Rd, East Windsor, CT",
       "state_code": "CT", "precision": "house",
       "house_number": "183", "road": "Chamberlain Rd",
       "street": "Chamberlain Rd"}


def _tri(decision):
    return {"decision": decision, "reason": "synthetic",
            "address_scope": None, "address_scope_confidence": None,
            "genuine_p": None}


class TestSupersedeOnRetry(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.order_file = os.path.join(self._tmp.name, "order.json")
        with open(self.order_file, "w") as f:
            json.dump(ORDER, f)
        self.order_dir = os.path.join(BASE, "orders", "ORD-TEST-R23")

    def tearDown(self):
        shutil.rmtree(self.order_dir, ignore_errors=True)
        self._tmp.cleanup()

    def _run(self, triage_result, success_stubs=False):
        patches = [patch.object(run_screen.jev_triage, "triage",
                                return_value=triage_result),
                   patch.object(sys, "argv",
                                ["run_screen.py", "--order-file",
                                 self.order_file])]
        if success_stubs:
            wells = {"total_water_supply_wells": 3,
                     "wells_with_depth_yield": 2}
            wq = {"arsenic": {"result_rows": 0},
                  "uranium": {"result_rows": 0}, "flags": []}
            patches += [
                patch.object(run_screen.geocode, "geocode",
                             return_value=dict(GEO)),
                patch.object(run_screen.nearby_wells, "nearby_wells",
                             return_value=wells),
                patch.object(run_screen.water_quality, "water_quality",
                             return_value=wq),
                patch.object(run_screen.setbacks, "setback_screen",
                             return_value={}),
                patch.object(run_screen.screen, "build_screen",
                             return_value="screen.html"),
                patch.object(run_screen.screen, "build_screen_json",
                             return_value="screen.json"),
                patch.object(run_screen.report, "_verdict",
                             return_value=("ok", "Good", "note")),
                patch.object(run_screen.report, "cost_band",
                             return_value=None),
                patch.object(run_screen.report, "build_delivery_html",
                             return_value="<html/>"),
            ]
        for p in patches:
            p.start()
        try:
            run_screen.main()
        finally:
            for p in patches:
                p.stop()

    def test_held_then_fulfilled_supersedes_marker(self):
        self._run(_tri("held_for_review"))
        marker = os.path.join(self.order_dir, "fulfillment_failed.json")
        self.assertTrue(os.path.exists(marker))

        self._run(_tri("proceed"), success_stubs=True)
        self.assertFalse(os.path.exists(marker))
        self.assertTrue(os.path.exists(os.path.join(
            self.order_dir, "fulfillment_failed.superseded.json")))
        summary = json.load(open(os.path.join(
            self.order_dir, "pipeline_summary.json")))
        self.assertEqual("fulfilled", summary["mode"])
        self.assertEqual("held",
                         summary["superseded_failure"]["mode"])

    def test_helper_noop_without_marker(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(intake.supersede_failure_marker(d))


if __name__ == "__main__":
    unittest.main()
