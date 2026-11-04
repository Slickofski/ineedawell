#!/usr/bin/env python3
"""CX residual fixes regression tests (2026-10-29).

FIX A: the rules table's confidence codes (CODE / OPINION /
GUIDANCE / UNVERIFIED) are internal vocabulary. Customer surfaces
must render plain words through the ONE shared mapping
(rules.confidence_plain); the codes themselves stay in the data —
rules_ct.json values and the setbacks.json stage output are
asserted unchanged.

Synthetic data only; provider calls are mocked. No customer data.
Run: python3 -m unittest test_cx_residual_fixes -v  (from fulfillment/)
"""
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import nearby_wells, packet, report, rules, screen, setbacks  # noqa: E402
from pipeline import water_quality  # noqa: E402

BARE = re.compile(r"Confidence: (CODE|OPINION|GUIDANCE|UNVERIFIED)\b")


def _order(order_id="ORD-20261029-cxr"):
    return {"order_id": order_id, "tier": "free",
            "customer": {"name": "Test Person",
                         "email": "t@example.invalid",
                         "phone": "(860) 555-0100"},
            "property_address": "1 Synthetic Way, East Windsor, CT"}


def _geo():
    return {"lat": 41.9, "lon": -72.5, "source": "synthetic",
            "town": "East Windsor"}


def _row(depth=None, yld=None, static=None, addr="1 Synthetic Way"):
    return {
        "type_of_report": "Water Supply Well",
        "depth_of_well": None if depth is None else str(depth),
        "yield_test_result": None if yld is None else str(yld),
        "water_depth_from_surface": None if static is None else str(static),
        "water_during_test": None,
        "yield_test_type": None,
        "yield_test_hours": None,
        "well_address": addr,
        "well_city": "",
        "drilling_company": "Synthetic Drilling",
        "date_of_boringabandonment": "2024-01-01T00:00:00.000",
    }


def _pull(rows, order, geo, order_dir, base_dir=None):
    resp = Mock()
    resp.json.return_value = rows
    resp.raise_for_status.return_value = None
    with patch.object(nearby_wells.requests, "get", return_value=resp):
        return nearby_wells.nearby_wells(order, geo, order_dir, base_dir)


def _wq():
    return {"flags": [water_quality._flag("T", "S", "D")],
            "radius_miles": 25, "final_word": water_quality.FINAL_WORD,
            "source": "synthetic source", "note": "synthetic note"}


class TestConfidencePlainMapping(unittest.TestCase):
    """The shared mapping: every code the table can emit has plain
    words, and the words are the agreed buyer copy."""

    def test_every_table_value_maps_to_plain_words(self):
        values = rules.load()["confidence_values"]
        self.assertTrue(values)
        for code in values:
            plain = rules.confidence_plain(code)
            self.assertTrue(plain, code)
            self.assertNotEqual(plain, code,
                                f"{code} would ship bare")

    def test_agreed_phrases(self):
        self.assertEqual(rules.confidence_plain("CODE"),
                         "written in the state health code")
        self.assertEqual(
            rules.confidence_plain("OPINION"),
            "standard professional reading: not spelled out "
            "word-for-word in the code")
        self.assertEqual(rules.confidence_plain("UNVERIFIED"),
                         "not yet verified")

    def test_unknown_code_passes_through_unassigned(self):
        # No invented meanings: an undefined code renders as itself
        # (test_every_table_value_maps... is the drift guard that
        # keeps a NEW table value from reaching this fallback).
        self.assertEqual(rules.confidence_plain("SOMEDAY"), "SOMEDAY")


class TestConfidenceRendering(unittest.TestCase):
    """No bare 'Confidence: CODE' on any customer surface; the
    plain phrase is what renders instead."""

    def _surfaces(self, tmp):
        sb = setbacks.setback_screen(_order(), tmp)
        wells = _pull([_row(200, 5, 40), _row(260, 7, 55)], _order(),
                      _geo(), tmp)
        outs = {}
        outs["screen.html"] = Path(
            screen.build_screen(_order(), _geo(), wells, _wq(), sb,
                                tmp)).read_text()
        outs["report.html"] = Path(
            report.build_report(_order(), _geo(), wells, _wq(), sb,
                                {"drillers": []}, tmp)).read_text()
        outs["packet.md"] = Path(
            packet.build_packet(_order(), _geo(), wells, _wq(), sb,
                                tmp)).read_text()
        outs["delivery.html"] = report.build_delivery_html(
            _order(), wells, _wq())
        return outs

    def test_no_bare_code_on_any_customer_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in self._surfaces(tmp).items():
                self.assertIsNone(BARE.search(text),
                                  f"bare confidence code in {name}")

    def test_plain_phrase_renders_on_html_surfaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            outs = self._surfaces(tmp)
            for name in ("screen.html", "report.html"):
                self.assertIn(
                    "Confidence: written in the state health code",
                    outs[name], name)
                self.assertNotIn("Confidence: CODE", outs[name], name)

    def test_setback_detail_maps_each_code(self):
        base = {"rule": "R", "minimum_ft": 75, "condition": "C",
                "scales": None, "source": "S"}
        for code in rules.load()["confidence_values"]:
            detail = report.setback_detail({**base, "confidence": code})
            self.assertIn(
                f"Confidence: {rules.confidence_plain(code)}", detail)
            self.assertIsNone(BARE.search(detail), code)

    def test_guidance_item_renders_district_words(self):
        # The NCDHD boundary item carries GUIDANCE; its number
        # phrase already says guidance — the confidence tail must
        # not reintroduce the bare code either.
        item = {"rule": "R", "minimum_ft": 25, "condition": "C",
                "scales": None, "source": "S", "confidence": "GUIDANCE"}
        detail = report.setback_detail(item)
        self.assertIn("Confidence: district guidance", detail)
        self.assertIsNone(BARE.search(detail))


class TestMachineSurfacesKeepCodes(unittest.TestCase):
    """The other direction: data surfaces are NOT scrubbed. The
    stage output (setbacks.json) still carries the raw table
    codes, and the table itself is untouched."""

    def test_setbacks_stage_keeps_raw_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            sb = setbacks.setback_screen(_order(), tmp)
            on_disk = json.loads(
                Path(tmp, "setbacks.json").read_text())
            codes = {it["confidence"] for it in on_disk["items"]}
            self.assertTrue(codes)
            self.assertTrue(
                codes <= set(rules.load()["confidence_values"]))
            self.assertIn("CODE", codes)
            for it in sb["items"]:
                self.assertIn(it["confidence"],
                              rules.load()["confidence_values"])

    def test_rules_table_still_carries_codes(self):
        raw = Path(rules.__file__).with_name("rules_ct.json").read_text()
        self.assertIn('"confidence": "CODE"', raw)


if __name__ == "__main__":
    unittest.main()
