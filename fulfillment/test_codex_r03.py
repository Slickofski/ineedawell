"""Codex R03 regression tests (2026-10-21) — a single endpoint reading
must not be narrated as sustained production.

Synthetic well dicts only; never real customer orders. Exercises
pipeline.nearby_wells.strength_summary(), the snapshot strength line
(pipeline.screen) and the packet well-strength section
(pipeline.packet).

Run: python3 -m unittest test_codex_r03 -v  (from fulfillment/)
"""
import sys
import unittest

sys.path.insert(0, ".")
from pipeline import nearby_wells, packet, screen  # noqa: E402

RETIRED = ["kept up", "emptied out of storage"]


def _well(**kw):
    w = {
        "address": "1 Synthetic Way",
        "city": "",
        "depth_ft": 300.0,
        "yield_gpm": 10.0,
        "static_level_ft": 50.0,
        "pumping_level_ft": 180.0,
        "yield_test_type": "Compressed Air",
        "yield_test_hours": 0.1,  # six minutes
        "company": "Synthetic Drilling",
        "date": "2024-01-01",
    }
    w.update(kw)
    return w


def _outputs(w):
    wells = {"wells": [w]}
    return (screen._strength_note(wells) or "",
            packet._well_strength(wells) or "")


class TestR03EndpointHonesty(unittest.TestCase):
    def test_r03a_six_minute_air_test_is_neutral(self):
        w = _well()
        s = nearby_wells.strength_summary([w])
        self.assertEqual(len(s["invalid"]), 0)
        self.assertEqual(s["sustained_yield"], "unestablished")
        for text in _outputs(w):
            low = text.lower()
            # Recorded facts stated neutrally: static level, level during
            # test, method, duration.
            self.assertIn("50", text)
            self.assertIn("180", text)
            self.assertIn("compressed air", low)
            self.assertTrue("0.1" in text or "6 min" in low,
                            f"duration missing from: {text!r}")
            # No sustained-production claim; sustained yield unestablished.
            for phrase in RETIRED:
                self.assertNotIn(phrase, low)
            self.assertIn("unestablished", low)

    def test_r03b_reversed_levels_rejected(self):
        # During-test level (40 ft) shallower than static (100 ft):
        # the water cannot rise while the well is being pumped/bailed.
        w = _well(static_level_ft=100.0, pumping_level_ft=40.0,
                  yield_test_hours=1.0)
        s = nearby_wells.strength_summary([w])
        self.assertEqual(len(s["invalid"]), 1)
        self.assertEqual(len(s["trio"]), 0)
        note, pkt = _outputs(w)
        blob = (note + "\n" + pkt).lower()
        self.assertIn("impossible", blob)
        for phrase in RETIRED:
            self.assertNotIn(phrase, blob)
            self.assertNotIn(phrase, note.lower())

    def test_r03c_level_below_bottom_rejected(self):
        # During-test level (250 ft) below the well bottom (200 ft).
        w = _well(depth_ft=200.0, static_level_ft=50.0,
                  pumping_level_ft=250.0, yield_test_hours=1.0)
        s = nearby_wells.strength_summary([w])
        self.assertEqual(len(s["invalid"]), 1)
        self.assertEqual(len(s["trio"]), 0)
        note, pkt = _outputs(w)
        blob = (note + "\n" + pkt).lower()
        self.assertIn("impossible", blob)
        for phrase in RETIRED:
            self.assertNotIn(phrase, blob)


class TestStorageSummary(unittest.TestCase):
    """Borehole storage (2026-10-24 fix, Part 1): column = depth -
    resting (static) level, storage = column x 1.47 gal/ft (standard
    6-inch bore). Only sane pairs enter; missing static is 'unknown'
    (excluded, never filled or zeroed); impossible pairs and junk
    columns are excluded and counted, R03-'invalid' style."""

    def test_sane_pair_counted(self):
        s = nearby_wells.storage_summary(
            [_well(depth_ft=300.0, static_level_ft=50.0)])
        self.assertEqual(s["wells_with_column"], 1)
        self.assertEqual(s["median_column_ft"], 250)
        # 250 ft x 1.47 = 367.5 -> reported as about 368 gal.
        self.assertEqual(s["median_storage_gal"], 368)
        self.assertFalse(s["thin"])

    def test_missing_static_excluded_never_filled(self):
        s = nearby_wells.storage_summary(
            [_well(static_level_ft=None)])
        self.assertEqual(s["wells_with_column"], 0)
        self.assertIsNone(s["median_column_ft"])
        self.assertIsNone(s["median_storage_gal"])

    def test_empty_gives_nulls_not_zeros(self):
        s = nearby_wells.storage_summary([])
        self.assertEqual(s["wells_with_column"], 0)
        self.assertIsNone(s["median_column_ft"])
        self.assertIsNone(s["median_storage_gal"])

    def test_static_at_or_below_zero_excluded_and_counted(self):
        for bad_static in (0.0, 300.0, 350.0):
            s = nearby_wells.storage_summary(
                [_well(depth_ft=300.0, static_level_ft=bad_static)])
            self.assertEqual(s["wells_with_column"], 0,
                             f"static {bad_static} entered the statistic")
            self.assertEqual(s["excluded_impossible"], 1)

    def test_monster_column_excluded_by_plausibility_guard(self):
        # Depth 100,000 ft / static 10 ft: a filing error, not a well.
        s = nearby_wells.storage_summary(
            [_well(depth_ft=100000.0, static_level_ft=10.0)])
        self.assertEqual(s["wells_with_column"], 0)
        self.assertEqual(s["excluded_impossible"], 1)
        self.assertIsNone(s["median_column_ft"])

    def test_thin_flag_below_50ft_median_column(self):
        wells = [_well(depth_ft=100.0, static_level_ft=85.0,
                       pumping_level_ft=95.0),
                 _well(depth_ft=110.0, static_level_ft=88.0,
                       pumping_level_ft=100.0),
                 _well(depth_ft=95.0, static_level_ft=80.0,
                       pumping_level_ft=92.0)]
        s = nearby_wells.storage_summary(wells)
        self.assertEqual(s["median_column_ft"], 15)
        self.assertEqual(s["median_storage_gal"], 22)
        self.assertTrue(s["thin"])


class TestStorageSurfaces(unittest.TestCase):
    """The storage sentence on the snapshot line (deep and thin forms),
    the labeled borehole_storage block in screen.json, and the packet
    section-3 storage paragraph."""

    def _agg(self, specs):
        ws = [_well(depth_ft=d, yield_gpm=y, static_level_ft=s,
                    pumping_level_ft=p)
              for (d, y, s, p) in specs]
        return {"wells": ws}

    def test_strength_note_thin_sentence(self):
        wells = self._agg([(100, 15, 85, 95), (110, 20, 88, 100),
                           (95, 25, 80, 92)])
        note = screen._strength_note(wells)
        self.assertIn("Borehole storage: these wells stand shallow",
                      note)
        self.assertIn("only about 22 gallons standing in the bore "
                      "itself", note)

    def test_strength_note_deep_sentence(self):
        wells = self._agg([(500, 2, 60, 300), (520, 3, 55, 320),
                           (480, 4, 70, 310)])
        note = screen._strength_note(wells)
        self.assertIn("Borehole storage: wells here also stand deep",
                      note)
        self.assertIn("about 647 gallons of water standing in the "
                      "bore itself", note)
        self.assertIn("about 440 ft of water column", note)

    def test_storage_sentence_without_test_levels(self):
        # Depth+static on file but no during-test levels: the trio is
        # empty, yet the storage fact still reaches the snapshot line.
        wells = {"wells": [_well(pumping_level_ft=None)]}
        note = screen._strength_note(wells)
        self.assertIsNotNone(note)
        self.assertIn("Borehole storage:", note)

    def test_borehole_storage_json_block(self):
        wells = self._agg([(500, 2, 60, 300), (520, 3, 55, 320),
                           (480, 4, 70, 310)])
        block = screen._storage_json(wells)
        self.assertEqual(block["wells_with_column"], 3)
        self.assertEqual(block["excluded_impossible"], 0)
        self.assertEqual(block["median_column_ft"], 440)
        self.assertEqual(block["median_storage_gal_6in_bore"], 647)

    def test_borehole_storage_json_nulls_when_unknown(self):
        wells = {"wells": [_well(static_level_ft=None)]}
        block = screen._storage_json(wells)
        self.assertEqual(block["wells_with_column"], 0)
        self.assertIsNone(block["median_column_ft"])
        self.assertIsNone(block["median_storage_gal_6in_bore"])

    def test_packet_storage_paragraph(self):
        wells = self._agg([(500, 2, 60, 300), (520, 3, 55, 320),
                           (480, 4, 70, 310)])
        text = packet._well_strength(wells)
        self.assertIn("The borehole is a storage tank, not just a "
                      "straw.", text)
        self.assertIn("about 1.5 gallons for every foot of water "
                      "standing in it", text)
        self.assertIn("stands about 440 ft deep in water", text)
        self.assertIn("roughly 647 gallons in reserve", text)
        self.assertIn("6-inch bore assumed", text)

    def test_packet_storage_exclusions_line(self):
        good = _well(depth_ft=300.0, static_level_ft=50.0,
                     pumping_level_ft=180.0)
        left_out = _well(depth_ft=100.0, static_level_ft=0.0,
                         pumping_level_ft=None, yield_gpm=None)
        text = packet._well_strength({"wells": [good, left_out]})
        self.assertIn("1 filing(s) left out: resting level missing "
                      "or impossible against the depth", text)


if __name__ == "__main__":
    unittest.main()
