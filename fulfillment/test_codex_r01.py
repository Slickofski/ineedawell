"""Codex R01 regression tests (2026-10-21) — missing/zero yields must not
produce reassuring conclusions.

Synthetic Socrata provider rows only (mocked requests.get); never real
customer orders. Aggregation is exercised end-to-end through
pipeline.nearby_wells.nearby_wells(), verdicts through pipeline.report.

Run: python3 -m unittest test_codex_r01 -v  (from fulfillment/)
"""
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, ".")
from pipeline import nearby_wells, report  # noqa: E402


def _row(depth=None, yld=None, addr="1 Synthetic Way"):
    return {
        "type_of_report": "Water Supply Well",
        "depth_of_well": None if depth is None else str(depth),
        "yield_test_result": None if yld is None else str(yld),
        "water_depth_from_surface": None,
        "water_during_test": None,
        "yield_test_type": None,
        "yield_test_hours": None,
        "well_address": addr,
        "well_city": "",
        "drilling_company": "Synthetic Drilling",
        "date_of_boringabandonment": "2024-01-01T00:00:00.000",
    }


def _run(rows):
    """Run nearby_wells() against synthetic provider rows (no network).

    well_city is blank and geo carries no town, so no town-anchor query
    fires; the single mocked GET serves the radius query.
    """
    resp = Mock()
    resp.json.return_value = rows
    resp.raise_for_status.return_value = None
    geo = {"lat": 41.0, "lon": -72.0}
    with tempfile.TemporaryDirectory() as d:
        with patch.object(nearby_wells.requests, "get", return_value=resp):
            return nearby_wells.nearby_wells({}, geo, d)


class TestR01YieldEvidence(unittest.TestCase):
    def test_r01a_one_yield_of_three_depths(self):
        # 3 records with depths, only 1 with yield 20.
        res = _run([_row(100, 20, "a"), _row(200, None, "b"),
                    _row(300, None, "c")])
        # Aggregates reflect only the yield-bearing record.
        self.assertEqual(res["yield_count"], 1)
        self.assertEqual(res["depth_count"], 3)
        self.assertEqual(res["wells_with_depth_yield"], 1)
        self.assertEqual(res["yield_gpm"]["min"], 20)
        self.assertEqual(res["yield_gpm"]["max"], 20)
        vcls, vword, vnote = report._verdict(res)
        # No green verdict from depth-only evidence.
        self.assertNotEqual(vcls, "good")
        self.assertNotEqual(vword, "Good water outlook")
        # No claim that all three produce 20-20 gpm.
        self.assertNotIn("20\u201320", vnote)
        self.assertNotIn("produce 20", vnote)
        # The note scopes the yield to the 1 record that reports it.
        self.assertIn("1 of 3", vnote)

    def test_r01b_all_yields_zero(self):
        res = _run([_row(100, 0, "a"), _row(200, 0, "b"), _row(300, 0, "c")])
        # Zeros are preserved in the aggregates, not dropped.
        self.assertIsNotNone(res["yield_gpm"])
        self.assertEqual(res["yield_gpm"]["min"], 0)
        self.assertEqual(res["yield_gpm"]["max"], 0)
        self.assertEqual(res["yield_gpm"]["median"], 0)
        self.assertEqual(res["zero_yield_count"], 3)
        vcls, vword, vnote = report._verdict(res)
        self.assertEqual(vcls, "caution")
        self.assertIn("zero", vnote.lower())
        self.assertNotIn("produced water", vnote.lower())
        self.assertNotIn("produced water", vword.lower())

    def test_r01c_zero_yield_without_depth_counts(self):
        # A zero-yield record with no depth must still count as zero yield.
        res = _run([_row(None, 0, "a"), _row(200, 20, "b")])
        self.assertEqual(res["yield_count"], 2)
        self.assertEqual(res["yield_gpm"]["min"], 0)
        self.assertEqual(res["yield_gpm"]["max"], 20)
        self.assertEqual(res["zero_yield_count"], 1)

    def test_r01d_depth_chart_all_depths_unknown(self):
        wells = {"wells": [
            {"address": "Synthetic record 1", "depth_ft": None,
             "yield_gpm": None},
            {"address": "Synthetic record 2", "depth_ft": None,
             "yield_gpm": 5},
        ]}
        html = report._depth_bars(wells)
        self.assertNotIn("deepest nearby", html)
        self.assertNotIn("1 ft", html)
        self.assertIn("depth n/a", html)
        self.assertIn("No usable depth", html)

    def test_r01e_missing_depths_no_false_no_wells_statement(self):
        # Wells exist but carry no depth: verdict must not claim there are
        # no water-supply wells.
        res = _run([_row(None, None, "a"), _row(None, None, "b")])
        self.assertEqual(res["total_water_supply_wells"], 2)
        _vcls, _vword, vnote = report._verdict(res)
        self.assertNotIn("No nearby water-supply wells", vnote)
        self.assertNotIn("No water-supply wells", vnote)


class TestR01YieldWithoutDepthStats(unittest.TestCase):
    """PR #5 review, Finding 1 (2026-10-22): the R01 fix gated the
    'good' verdict branches on yield evidence but left them formatting
    the depth min-max unconditionally, so yield-bearing records with
    NO depth statistics crashed _verdict with a TypeError. The depth
    parenthetical may render only when depth statistics exist."""

    def test_r01f_yields_no_depths_end_to_end(self):
        # 3 records, all yield-bearing, none with a depth.
        res = _run([_row(None, 12, "a"), _row(None, 15, "b"),
                    _row(None, 20, "c")])
        self.assertEqual(res["yield_count"], 3)
        self.assertEqual(res["depth_count"], 0)
        vcls, vword, vnote = report._verdict(res)
        self.assertEqual(vcls, "good")
        self.assertEqual(vword, "Good water outlook")
        self.assertIn("12\u201320", vnote)
        self.assertNotIn("None", vnote)
        self.assertNotIn("depths", vnote)

    def test_r01g_yields_no_depths_tract(self):
        res = _run([_row(None, 12, "a"), _row(None, 15, "b"),
                    _row(None, 20, "c")])
        vcls, vword, vnote = report._verdict(res, parcel_acres=10)
        self.assertEqual(vcls, "good")
        self.assertIn("approvals decide", vword)
        self.assertIn("12\u201320", vnote)
        self.assertNotIn("None", vnote)
        self.assertNotIn("depths", vnote)

    def test_r01h_yields_no_depths_aggregate_only(self):
        # The brief's exact reproduction shape: aggregate keys only,
        # depth_ft an empty dict, depth_count 0.
        wells = {"total_water_supply_wells": 3,
                 "yield_gpm": {"min": 12, "median": 15, "max": 20},
                 "yield_count": 3, "zero_yield_count": 0,
                 "depth_ft": {}, "depth_count": 0}
        for kw in ({}, {"parcel_acres": 10}):
            vcls, vword, vnote = report._verdict(wells, **kw)
            self.assertEqual(vcls, "good")
            self.assertIn("12\u201320", vnote)
            self.assertNotIn("None", vnote)
            self.assertNotIn("depths", vnote)


def _agg(specs):
    """Aggregate wells dict in the shape nearby_wells() returns, built
    from (depth, yield, static, pumping) tuples — the audit's synthetic
    construction (borehole-storage audit, 2026-10-24)."""
    import statistics as _st
    ws = [{"address": f"{i} Synthetic St", "depth_ft": d, "yield_gpm": y,
           "static_level_ft": s, "pumping_level_ft": p,
           "yield_test_type": "Compressed Air", "yield_test_hours": 1.0,
           "company": "X", "date": "2024-01-01"}
          for i, (d, y, s, p) in enumerate(specs)]
    ys = [w["yield_gpm"] for w in ws]
    ds = [w["depth_ft"] for w in ws]
    return {"wells": ws, "total_water_supply_wells": len(ws),
            "yield_gpm": {"min": min(ys), "max": max(ys),
                          "median": _st.median(ys)},
            "depth_ft": {"min": min(ds), "max": max(ds),
                         "median": _st.median(ds)},
            "yield_count": len(ys), "depth_count": len(ds),
            "zero_yield_count": 0}


class TestBoreholeStorageVerdictNotes(unittest.TestCase):
    """Borehole-storage fix, Part 1 (2026-10-24): the storage fact is
    added to the verdict NOTES in both directions. Verdict CLASSES and
    gates did not change in Part 1. SUPERSEDED IN PART by the
    re-gate (2026-10-24, TestBoreholeStorageRegate below): for
    shapes where storage is KNOWN for gating, the class assertions
    here were updated to the gated contract; the sentence-level
    assertions stand wherever the gated class still carries them."""

    def test_thin_column_now_caps_to_caution(self):
        # Audit synthetic A: columns 15/22/15 ft (~22 gal median
        # reserve) under yields of 15-25 gpm. Part 1: class stayed
        # "good" with a caution sentence appended. Re-gate: known
        # thin storage CAPS the class to caution, headline naming
        # the thin reserve.
        res = _agg([(100, 15, 85, 95), (110, 20, 88, 100),
                    (95, 25, 80, 92)])
        vcls, vword, vnote = report._verdict(res)
        self.assertEqual(vcls, "caution")
        self.assertEqual(vword, "Strong flow, thin reserve \u2014 ask a driller before you commit")
        self.assertIn("typical standing reserve is only about 22 gal",
                      vnote)
        self.assertIn("can outrun the reserve even at these yields",
                      vnote)

    def test_thin_column_tract_now_caps_to_caution(self):
        res = _agg([(100, 15, 85, 95), (110, 20, 88, 100),
                    (95, 25, 80, 92)])
        vcls, vword, vnote = report._verdict(res, parcel_acres=10)
        self.assertEqual(vcls, "caution")
        self.assertIn("thin reserve", vword)
        self.assertIn("approvals decide", vword)

    def test_deep_column_good_gains_reserve_sentence(self):
        # Yields 12-20 gpm over columns of ~330-350 ft (~500 gal).
        res = _agg([(400, 12, 60, 300), (420, 15, 70, 320),
                    (380, 20, 50, 290)])
        vcls, vword, vnote = report._verdict(res)
        self.assertEqual(vcls, "good")
        self.assertIn("also stand deep", vnote)
        self.assertIn("reserve behind those yields is real", vnote)

    def test_deep_column_low_yield_now_lifts_to_good(self):
        # Audit synthetic B: columns ~440 ft (~647 gal) under yields
        # of 2-4 gpm. Part 1: class stayed caution with the
        # mitigation sentence appended. Re-gate: known deep storage
        # (median yield 3 >= 2, storage 647 >= 300) LIFTS the class
        # to good; the reserve fact now reads as the reason.
        res = _agg([(500, 2, 60, 300), (520, 3, 55, 320),
                    (480, 4, 70, 310)])
        vcls, vword, vnote = report._verdict(res)
        self.assertEqual(vcls, "good")
        self.assertEqual(vword,
                         "Workable for a house \u2014 low flow, deep reserve")
        self.assertIn("about 647 gallons", vnote)
        self.assertIn("while the well refills", vnote)
        self.assertIn("driller should confirm it on site", vnote)

    def test_unknown_storage_no_sentence(self):
        # Aggregate-only shape (no per-well rows): storage is unknown
        # and no storage sentence may be invented for the note.
        wells = {"total_water_supply_wells": 3,
                 "yield_gpm": {"min": 12, "median": 15, "max": 20},
                 "yield_count": 3, "zero_yield_count": 0,
                 "depth_ft": {}, "depth_count": 0}
        _vcls, _vword, vnote = report._verdict(wells)
        self.assertNotIn("standing reserve", vnote)
        self.assertNotIn("Borehole storage", vnote)


class TestBoreholeStorageRegate(unittest.TestCase):
    """Borehole-storage RE-GATE (2026-10-24, design approved by Marcin
    as proposed): known storage now gates the verdict class in both
    directions. Storage is KNOWN for gating only when sane
    depth+static pairs cover at least half the neighborhood's wells;
    below that share nothing gates. Thin cap: a would-be "good" drops
    to caution at median column < 50 ft. Deep lift: the
    complete-evidence caution rises to "good" only with median yield
    >= 2 gpm AND median storage >= 300 gal. Zero-yield branches are
    never gated."""

    def test_thin_cap_single_family(self):
        # Audit synthetic A: columns 15/22/15 ft (~22 gal median
        # reserve) under yields of 15-25 gpm. Was "good" in Part 1;
        # the cap must now drop it to caution, headline naming the
        # thin reserve.
        res = _agg([(100, 15, 85, 95), (110, 20, 88, 100),
                    (95, 25, 80, 92)])
        vcls, vword, vnote = report._verdict(res)
        self.assertEqual(vcls, "caution")
        self.assertEqual(vword, "Strong flow, thin reserve \u2014 ask a driller before you commit")
        self.assertIn("about 22 gal", vnote)
        self.assertIn("outrun the reserve", vnote)

    def test_thin_cap_tract(self):
        res = _agg([(100, 15, 85, 95), (110, 20, 88, 100),
                    (95, 25, 80, 92)])
        vcls, vword, vnote = report._verdict(res, parcel_acres=10)
        self.assertEqual(vcls, "caution")
        self.assertIn("thin reserve", vword)
        self.assertIn("approvals decide", vword)
        # Approvals still gate the tract in the note.
        self.assertIn("subdivision", vnote)

    def test_deep_lift_single_family(self):
        # Audit synthetic B: columns ~440 ft (~647 gal) under yields
        # of 2-4 gpm (median 3 >= 2, storage 647 >= 300, evidence
        # complete). Was caution in Part 1; the lift must now raise
        # it to good with the basis in the headline.
        res = _agg([(500, 2, 60, 300), (520, 3, 55, 320),
                    (480, 4, 70, 310)])
        vcls, vword, vnote = report._verdict(res)
        self.assertEqual(vcls, "good")
        self.assertEqual(vword,
                         "Workable for a house \u2014 low flow, deep reserve")
        # Honesty load: the reserve covers peaks, it does not raise
        # production; sustained yield stays unestablished; a driller
        # confirms on site.
        self.assertIn("does not raise", vnote)
        self.assertIn("sustained yield is unestablished", vnote)
        self.assertIn("driller should confirm", vnote)
        self.assertIn("about 647 gallons", vnote)

    def test_deep_lift_tract(self):
        res = _agg([(500, 2, 60, 300), (520, 3, 55, 320),
                    (480, 4, 70, 310)])
        vcls, vword, vnote = report._verdict(res, parcel_acres=10)
        self.assertEqual(vcls, "good")
        # CX pass (2026-10-29): the headline names the reserve in
        # buyer words now — exact-string assertion, as the
        # single-family lift tests use.
        self.assertEqual(vword,
                         "Workable water, deep reserve \u2014 approvals decide this tract")
        self.assertIn("sustained yield is unestablished", vnote)

    def test_lift_refused_median_yield_below_2(self):
        # Deep reserve (~647 gal) but median yield 1 gpm < 2 floor:
        # no lift, class stays the complete-evidence caution.
        res = _agg([(500, 1, 60, 300), (520, 1, 55, 320),
                    (480, 2, 70, 310)])
        vcls, vword, vnote = report._verdict(res)
        self.assertEqual(vcls, "caution")
        self.assertEqual(vword, "Water on record, amount unproven \u2014 get driller opinions before you commit")

    def test_lift_refused_storage_below_300(self):
        # Median yield 3 gpm clears the floor, but columns of 150 ft
        # hold only ~220 gal < 300: no lift.
        res = _agg([(300, 2, 150, 250), (320, 3, 170, 270),
                    (280, 4, 130, 230)])
        vcls, vword, vnote = report._verdict(res)
        self.assertEqual(vcls, "caution")
        self.assertEqual(vword, "Water on record, amount unproven \u2014 get driller opinions before you commit")

    def test_cap_refused_below_half_known_share(self):
        # 4 wells, strong yields, but only 1 of 4 carries a resting
        # level (a thin 15 ft column). Share 1/4 < half, so storage is
        # UNKNOWN for gating: no cap, the good verdict stands.
        res = _agg([(100, 15, 85, 95), (110, 20, None, None),
                    (95, 25, None, None), (105, 18, None, None)])
        vcls, vword, vnote = report._verdict(res)
        self.assertEqual(vcls, "good")
        self.assertEqual(vword, "Good water outlook")
        # Part 1's thin caution SENTENCE still prints from the known
        # subset — only the gate is share-gated, not the fact.
        self.assertIn("One caution inside the good news", vnote)

    def test_lift_refused_below_half_known_share(self):
        # 4 wells, median yield 3 gpm, but only 1 of 4 carries a
        # resting level (a deep 440 ft column). Share < half: no lift.
        res = _agg([(500, 2, 60, 300), (520, 3, None, None),
                    (480, 4, None, None), (510, 3, None, None)])
        vcls, vword, vnote = report._verdict(res)
        self.assertEqual(vcls, "caution")
        self.assertEqual(vword, "Water on record, amount unproven \u2014 get driller opinions before you commit")

    def test_cap_fires_at_exactly_half_known_share(self):
        # 2 of 4 wells carry sane (thin) pairs: share = half, which
        # the design counts as known ("at least half"). Cap fires.
        res = _agg([(100, 15, 85, 95), (110, 20, 88, 100),
                    (95, 25, None, None), (105, 18, None, None)])
        vcls, vword, vnote = report._verdict(res)
        self.assertEqual(vcls, "caution")
        self.assertEqual(vword, "Strong flow, thin reserve \u2014 ask a driller before you commit")

    def test_zero_yield_deep_storage_never_lifted(self):
        # A recorded zero is a red flag storage cannot wash out: deep
        # reserve on every well, one zero yield -> the zero-yield
        # caution stands, no lift.
        res = _agg([(500, 0, 60, 300), (520, 3, 55, 320),
                    (480, 4, 70, 310)])
        vcls, vword, vnote = report._verdict(res)
        self.assertEqual(vcls, "caution")
        self.assertTrue(vword.startswith("Zero-yield records"))


if __name__ == "__main__":
    unittest.main()
