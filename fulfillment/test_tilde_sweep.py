#!/usr/bin/env python3
"""Tests for the tilde sweep (Marcin's order, 2026-11-04): no
approximation squiggles in customer-visible copy.

Every "~" and "≈" that used to render in front of a number ("~$200",
"≈607 gal") is now the word "about" ("about $200", "about 607 gal").
Numbers never changed; only the symbol became a word. Marcin reads
the squiggle as an AI-writing tell, so it does not ship.

Covered surfaces: verdict notes (thin reserve, deep lift, good with
deep storage, tract forms), the verdict stats row, the cost section
(scenario cards + permit line), the packet filing checklist, and a
full four-surface render (snapshot HTML, screen.json, packet,
report) from the vendored 193 Wells Rd fixture stages.

Run: python3 -m unittest test_tilde_sweep -v  (from fulfillment/)
"""
import json
import os
import statistics as _stats
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import packet, report, screen, setbacks  # noqa: E402

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "test_fixtures", "park39-193-wells-rd")


def _agg(specs):
    """Aggregate wells dict in the shape nearby_wells() returns, built
    from (depth, yield, static, pumping) tuples — same construction
    as test_codex_r01's synthetic neighborhoods."""
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


# Thin columns (about 22 gal median reserve) under strong yields.
THIN = _agg([(100, 15, 85, 95), (110, 20, 88, 100), (95, 25, 80, 92)])
# Deep columns (about 647 gal) under yields of 12-20 gpm.
DEEP_STRONG = _agg([(500, 12, 60, 110), (520, 20, 55, 120),
                    (480, 16, 70, 130)])
# Deep columns (about 647 gal) under low yields — the storage lift.
DEEP_LOW = _agg([(500, 3, 60, 110), (520, 4, 55, 120), (480, 5, 70, 130)])


def assert_no_squiggle(case, text, where):
    case.assertNotIn("≈", text, f"≈ left in {where}")
    case.assertNotIn("~", text, f"~ left in {where}")


class TestVerdictNotesSayAbout(unittest.TestCase):
    def test_thin_reserve_note(self):
        _cls, _word, note = report._verdict(THIN)
        assert_no_squiggle(self, note, "thin-reserve verdict note")
        self.assertIn("typical standing reserve is only about 22 gal",
                      note)

    def test_good_with_deep_storage_note(self):
        _cls, _word, note = report._verdict(DEEP_STRONG)
        assert_no_squiggle(self, note, "good/deep verdict note")
        self.assertIn("standing reserve about 647 gal", note)

    def test_deep_lift_note(self):
        _cls, _word, note = report._verdict(DEEP_LOW)
        assert_no_squiggle(self, note, "deep-lift verdict note")
        self.assertIn("holds about 647 gallons", note)

    def test_tract_forms(self):
        for wells in (THIN, DEEP_LOW):
            _cls, _word, note = report._verdict(wells, parcel_acres=10)
            assert_no_squiggle(self, note, "tract verdict note")


class TestStatsRowSaysAbout(unittest.TestCase):
    def test_storage_lift_stats_lead(self):
        _cls, _word, _note, meta = report._verdict_full(DEEP_LOW)
        self.assertTrue(meta["storage_lift"])
        html = report._stats_html(3, DEEP_LOW, meta)
        assert_no_squiggle(self, html, "stats row")
        self.assertIn("about 647 gal", html)


class TestCostCopySaysAbout(unittest.TestCase):
    def test_cost_section(self):
        html = report._cost_section(DEEP_STRONG)
        assert_no_squiggle(self, html, "cost section")
        self.assertIn("about $45/ft", html)
        # Cost-source pass (2026-11-04): the permit line names the
        # district instead of a statewide "typically about $200".
        self.assertIn("$200 in the North Central District", html)
        self.assertNotIn("typically about $200", html)

    def test_cost_band_excludes_line(self):
        band = report.cost_band(DEEP_STRONG)
        joined = " ".join(band["excludes"])
        assert_no_squiggle(self, joined, "cost band excludes")
        self.assertIn("set by your health district", joined)
        self.assertIn("$200 in the North Central District", joined)


class TestFullSurfaceRender(unittest.TestCase):
    """All four customer surfaces rendered from the real fixture
    stages carry no squiggle anywhere in their text."""

    def _load(self, name):
        with open(os.path.join(FIXTURE_DIR, name)) as f:
            return json.load(f)

    def test_fixture_render_is_squiggle_free(self):
        order = self._load("order.json")
        geo = self._load("geocode.json")
        wells = self._load("nearby_wells.json")
        wq = self._load("water_quality.json")
        with tempfile.TemporaryDirectory() as tmp:
            # Setback stage re-run from the rules table, exactly as
            # the sample regeneration does (the stored setbacks.json
            # is a frozen artifact of an older table).
            sb = setbacks.setback_screen(order, tmp)
            screen.build_screen(order, geo, wells, wq, sb, tmp)
            screen.build_screen_json(order, geo, wells, wq, sb, tmp)
            packet_path = packet.build_packet(order, geo, wells, wq,
                                              sb, tmp)
            report.build_report(order, geo, wells, wq, sb,
                                {"drillers": []}, tmp)
            rendered = {}
            for p in sorted(Path(tmp).rglob("*")):
                if p.is_file():
                    rendered[p.name] = p.read_text(errors="replace")
            self.assertIn("screen.html", rendered)
            self.assertIn("screen.json", rendered)
            self.assertTrue(packet_path)
            for name, text in rendered.items():
                assert_no_squiggle(self, text, f"rendered {name}")
            packet_text = Path(packet_path).read_text()
            # Cost-source pass (2026-11-04): the checklist carries the
            # exact NCDHD fees with the district named, not "about".
            self.assertIn("$250 per lot", packet_text)
            self.assertIn("$200, fee schedule effective July 1, 2026",
                          packet_text)


def assert_no_ai_typography(case, text, where):
    """Em-dash sweep (Marcin's order, 2026-11-04): no em dashes, no
    dash entities, no en dashes, no smart quotes, no emoji icons in
    customer-visible copy. Marcin reads them as AI-writing tells."""
    for bad in ("—", "&mdash;", "&#8212;", "–", "&ndash;", "&#8211;",
                "&rsquo;", "&lsquo;", "&ldquo;", "&rdquo;",
                "‘", "’", "“", "”", "✅"):
        case.assertNotIn(bad, text, f"{bad!r} left in {where}")


class TestEmDashSweep(unittest.TestCase):
    """Rendered surfaces and verdict headlines after the 2026-11-04
    em-dash sweep: punctuation changed, words and numbers did not."""

    def test_headlines_use_colons(self):
        _cls, word, _note = report._verdict(THIN, parcel_acres=10)
        self.assertEqual(
            word, "Strong yields, thin reserve: approvals decide "
                  "this tract")
        zero = _agg([(300, 0, 100, 150), (320, 5, 90, 140),
                     (310, 8, 95, 145)])
        _cls, word, _note = report._verdict(zero, parcel_acres=10)
        self.assertEqual(
            word, "Zero-yield records: approvals and water both open")

    def test_headlines_and_notes_have_no_dashes(self):
        for wells in (THIN, DEEP_STRONG, DEEP_LOW):
            for acres in (None, 10):
                _cls, word, note = report._verdict(wells,
                                                   parcel_acres=acres)
                assert_no_ai_typography(self, word, "verdict headline")
                assert_no_ai_typography(self, note, "verdict note")

    def test_ranges_say_to(self):
        _cls, _word, note = report._verdict(DEEP_STRONG)
        self.assertIn("12 to 20 gallons per minute", note)

    def test_fixture_render_is_typography_clean(self):
        order = TestFullSurfaceRender()._load("order.json")
        geo = TestFullSurfaceRender()._load("geocode.json")
        wells = TestFullSurfaceRender()._load("nearby_wells.json")
        wq = TestFullSurfaceRender()._load("water_quality.json")
        with tempfile.TemporaryDirectory() as tmp:
            sb = setbacks.setback_screen(order, tmp)
            screen.build_screen(order, geo, wells, wq, sb, tmp)
            screen.build_screen_json(order, geo, wells, wq, sb, tmp)
            packet_path = packet.build_packet(order, geo, wells, wq,
                                              sb, tmp)
            report.build_report(order, geo, wells, wq, sb,
                                {"drillers": []}, tmp)
            for p in sorted(Path(tmp).rglob("*")):
                if p.is_file():
                    assert_no_ai_typography(
                        self, p.read_text(errors="replace"),
                        f"rendered {p.name}")
            packet_text = Path(packet_path).read_text()
            self.assertIn("+$10k to $25k contingency band", packet_text)

    def test_site_files_are_typography_clean(self):
        site = Path(os.path.dirname(os.path.abspath(__file__))).parent \
            / "site"
        pages = [site / "index.html"] + sorted(
            (site / "towns").glob("*.html"))
        llms = site / "llms.txt"
        if llms.exists():
            pages.append(llms)
        self.assertGreaterEqual(len(pages), 11)
        for p in pages:
            text = p.read_text(errors="replace")
            assert_no_ai_typography(self, text, f"site {p.name}")
            self.assertNotIn('class="icon"', text)
            self.assertNotIn('class="big-icon"', text)


if __name__ == "__main__":
    unittest.main()
