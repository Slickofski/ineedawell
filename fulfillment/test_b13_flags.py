#!/usr/bin/env python3
"""Tests for B-13: raw water-quality flag dicts printed into the paid
report and the packet (adversarial review 2026-10-21, Finding B-13).

water_quality._flag() returns a dict {title, summary, detail}. The
snapshot card and the delivery email render it as finished copy; the
paid report (report.build_report) and the packet (packet.build_packet)
used to interpolate the raw dict, printing a Python repr to customers.

The fix: one shared formatter, water_quality.format_flag(), used by
both surfaces. Dict flags become "Title: summary" — the same front
wording the snapshot shows (title + summary; the detail layer stays
behind the snapshot's details toggle and in screen.json, never
inline). Legacy plain-string flags pass through unchanged.

All inputs are synthetic (fake order, .example.invalid email); the
flag comes from the REAL water_quality._flag() with the production
title/summary. No customer data.

Run: python3 -m unittest test_b13_flags -v  (from fulfillment/)
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import packet, report, screen, water_quality  # noqa: E402

TITLE = "Water quality: the state panel covers it"
SUMMARY = ("Connecticut requires arsenic and uranium testing on every "
           "new well: the two contaminants that actually show up in "
           "CT bedrock, so the lab runs the state panel as routine. "
           "Radon is optional; ask the lab whether to add it, and get "
           "the panel price when you book.")
DETAIL = "Water Quality Portal: synthetic detail for repro only."
FLAG = water_quality._flag(TITLE, SUMMARY, DETAIL)

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "test_fixtures", "park39-193-wells-rd")

BANNED = ["{'title':", "{&#x27;title&#x27;", "&quot;title&quot;",
          "'summary':", "&#x27;summary&#x27;"]


def _order():
    return {"order_id": "ORD-20261029-b13", "tier": "report",
            "customer": {"name": "Test Person",
                         "email": "t@example.invalid",
                         "phone": "(860) 555-0100"},
            "property_address": "1 Synthetic Way, East Windsor, CT"}


def _geo():
    return {"lat": 41.9, "lon": -72.5, "source": "synthetic",
            "town": "East Windsor"}


def _wells():
    return {"total_water_supply_wells": 2, "depth_count": 2,
            "yield_count": 1, "wells_with_depth_yield": 2,
            "depth_ft": {"min": 200, "max": 260, "median": 230},
            "yield_gpm": {"min": 5, "max": 5, "median": 5},
            "wells": [
                {"address": "1 Synthetic Way", "depth_ft": 200,
                 "yield_gpm": 5, "static_level_ft": 40,
                 "pumping_level_ft": None, "date": "2024-01-01"},
                {"address": "2 Synthetic Way", "depth_ft": 260,
                 "yield_gpm": None, "static_level_ft": 55,
                 "pumping_level_ft": None, "date": "2024-06-01"}],
            "source": "synthetic test source"}


def _setbacks():
    return {"items": [{"rule": "Well to septic", "minimum_ft": 75,
                       "status": "CODE"}],
            "confidence_note": "synthetic confidence note"}


def _wq(flags):
    return {"flags": flags, "radius_miles": 25,
            "final_word": water_quality.FINAL_WORD,
            "source": "synthetic source", "note": "synthetic note"}


def _render_report(flags):
    with tempfile.TemporaryDirectory() as tmp:
        path = report.build_report(_order(), _geo(), _wells(),
                                   _wq(flags), _setbacks(),
                                   {"drillers": []}, tmp)
        return Path(path).read_text()


def _render_packet(flags):
    with tempfile.TemporaryDirectory() as tmp:
        path = packet.build_packet(_order(), _geo(), _wells(),
                                   _wq(flags), _setbacks(), tmp)
        return Path(path).read_text()


def _load_fixture(name):
    with open(os.path.join(FIXTURE_DIR, name)) as f:
        return json.load(f)


class TestFormatFlagUnit(unittest.TestCase):
    def test_dict_becomes_finished_text(self):
        self.assertEqual(water_quality.format_flag(FLAG),
                         f"{TITLE}: {SUMMARY}")

    def test_no_dict_repr_in_formatted_text(self):
        out = water_quality.format_flag(FLAG)
        for bad in BANNED:
            self.assertNotIn(bad, out)
        # The technical detail layer never rides inline.
        self.assertNotIn(DETAIL, out)

    def test_legacy_string_passes_through(self):
        self.assertEqual(water_quality.format_flag("plain legacy flag"),
                         "plain legacy flag")

    def test_dict_missing_summary_renders_title_only(self):
        self.assertEqual(
            water_quality.format_flag({"title": "Only a title"}),
            "Only a title")


class TestReportFlagsB13(unittest.TestCase):
    def test_report_flags_are_finished_copy(self):
        html_doc = _render_report([FLAG])
        for bad in BANNED:
            self.assertNotIn(bad, html_doc)
        self.assertIn(TITLE, html_doc)
        self.assertIn(SUMMARY, html_doc)
        # Detail text stays out of the report copy (it lives behind
        # the snapshot's details toggle and in screen.json).
        self.assertNotIn(DETAIL, html_doc)

    def test_report_legacy_string_flag_renders(self):
        html_doc = _render_report(["A legacy plain-string flag."])
        self.assertIn("A legacy plain-string flag.", html_doc)

    def test_report_empty_flags_render_clean(self):
        html_doc = _render_report([])
        self.assertIn('<ul class="flags"></ul>', html_doc)


class TestPacketFlagsB13(unittest.TestCase):
    def test_packet_flags_are_finished_bullet(self):
        md = _render_packet([FLAG])
        for bad in BANNED:
            self.assertNotIn(bad, md)
        self.assertIn(f"- {TITLE}: {SUMMARY}", md)
        self.assertNotIn(DETAIL, md)

    def test_packet_legacy_string_flag_renders(self):
        md = _render_packet(["A legacy plain-string flag."])
        self.assertIn("- A legacy plain-string flag.", md)

    def test_packet_empty_flags_render_clean(self):
        md = _render_packet([])
        self.assertIn("## 4. Water-quality flags", md)


class TestPacketSnapshotWordingParity(unittest.TestCase):
    """Packet section 4 wording must match the snapshot's for the
    same flag: both show the flag's title and summary, verbatim."""

    def test_same_flag_same_wording(self):
        order = _load_fixture("order.json")
        geo = _load_fixture("geocode.json")
        wells = _load_fixture("nearby_wells.json")
        setbacks = _load_fixture("setbacks.json")
        wq = _load_fixture("water_quality.json")
        wq["flags"] = [FLAG]
        with tempfile.TemporaryDirectory() as tmp:
            snap_path = screen.build_screen(order, geo, wells, wq,
                                            setbacks, tmp)
            snap_html = Path(snap_path).read_text()
            pkt_path = packet.build_packet(order, geo, wells, wq,
                                           setbacks, tmp)
            pkt_md = Path(pkt_path).read_text()
        # Snapshot card shows the title and summary (HTML-escaped;
        # neither string carries escapable characters).
        self.assertIn(TITLE, snap_html)
        self.assertIn(SUMMARY, snap_html)
        # Packet bullet carries the identical wording.
        self.assertIn(f"- {TITLE}: {SUMMARY}", pkt_md)
        self.assertEqual(f"- {water_quality.format_flag(FLAG)}",
                         f"- {TITLE}: {SUMMARY}")


if __name__ == "__main__":
    unittest.main()
