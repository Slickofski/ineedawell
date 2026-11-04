#!/usr/bin/env python3
"""CX-audit fix pass regression tests (2026-10-29).

Covers: Finding 2 (internal rules-table ID leaking into customer
text), Findings 4-5 (answer-first verdict headlines; storage stat
leads on a lifted verdict), Finding 7 (packet status column in
plain words), Finding 8 (draft-file rename wiring; one shared pull
per property so snapshot and packet agree).

Synthetic data only; provider calls are mocked. No customer data.
Run: python3 -m unittest test_cx_pass -v  (from fulfillment/)
"""
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import nearby_wells, packet, report, screen, setbacks  # noqa: E402
from pipeline import water_quality  # noqa: E402

LEAK_ID = "ct_band_basis_operating_assumption"
SEE_REF = re.compile(r"\(see [a-z][a-z0-9_]*\)")


def _order(order_id="ORD-20261029-cx"):
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


def _real_setbacks(tmp):
    return setbacks.setback_screen(_order(), tmp)


class TestLeakScrub(unittest.TestCase):
    """Finding 2: the internal ID must not reach ANY rendered
    surface; the machinery status must not print in the packet."""

    def _surfaces(self, tmp):
        sb = _real_setbacks(tmp)
        wells = _pull([_row(200, 5, 40), _row(260, 7, 55)], _order(),
                      _geo(), tmp)
        outs = {}
        outs["screen.html"] = Path(
            screen.build_screen(_order(), _geo(), wells, _wq(), sb,
                                tmp)).read_text()
        outs["screen.json"] = Path(
            screen.build_screen_json(_order(), _geo(), wells, _wq(),
                                     sb, tmp)).read_text()
        outs["report.html"] = Path(
            report.build_report(_order(), _geo(), wells, _wq(), sb,
                                {"drillers": []}, tmp)).read_text()
        outs["packet.md"] = Path(
            packet.build_packet(_order(), _geo(), wells, _wq(), sb,
                                tmp)).read_text()
        outs["delivery.html"] = report.build_delivery_html(
            _order(), wells, _wq())
        return outs

    def test_no_internal_id_on_any_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in self._surfaces(tmp).items():
                self.assertNotIn(LEAK_ID, text, name)
                self.assertIsNone(SEE_REF.search(text), name)

    def test_setback_item_conditions_are_clean_at_the_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            sb = _real_setbacks(tmp)
            for it in sb["items"]:
                self.assertNotIn(LEAK_ID, it.get("condition") or "")
                self.assertNotIn("(see ", it.get("condition") or "")

    def test_packet_status_column_is_plain_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            sb = _real_setbacks(tmp)
            wells = _pull([_row(200, 5, 40)], _order(), _geo(), tmp)
            md = Path(packet.build_packet(_order(), _geo(), wells,
                                          _wq(), sb, tmp)).read_text()
            self.assertIn("To confirm on the site plan", md)
            self.assertNotIn("needs_field_verification", md)

    def test_fresh_pull_stamps_the_snapshot_source_line(self):
        # Item 7: a fresh pull carries pulled_at, and the snapshot's
        # source line prints it (legacy stored pulls have no stamp
        # and print nothing — no date is ever invented).
        with tempfile.TemporaryDirectory() as tmp:
            sb = _real_setbacks(tmp)
            wells = _pull([_row(200, 5, 40)], _order(), _geo(), tmp)
            self.assertTrue(wells.get("pulled_at"))
            html = Path(screen.build_screen(_order(), _geo(), wells,
                                            _wq(), sb, tmp)).read_text()
            self.assertIn(f"(records pulled {wells['pulled_at']})", html)
            md = Path(packet.build_packet(_order(), _geo(), wells,
                                          _wq(), sb, tmp)).read_text()
            self.assertIn(f"(records pulled {wells['pulled_at']})", md)

    def test_verify_badge_is_now_to_confirm(self):
        with tempfile.TemporaryDirectory() as tmp:
            sb = _real_setbacks(tmp)
            wells = _pull([_row(200, 5, 40)], _order(), _geo(), tmp)
            html = Path(screen.build_screen(_order(), _geo(), wells,
                                            _wq(), sb, tmp)).read_text()
            self.assertIn(">to confirm</span>", html)
            self.assertNotIn(">verify</span>", html)
            self.assertIn(
                "Your engineer confirms this distance on the site "
                "plan before filing.", html)
            self.assertNotIn("Field verification required on the "
                             "engineered site plan", html)


class TestHeadlines(unittest.TestCase):
    """Findings 4-5: answer-first headlines (the borehole gate
    behavior itself is covered by test_codex_r01; these pin the
    new customer wording)."""

    def _verdict(self, rows, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            wells = _pull(rows, _order(), _geo(), tmp)
            return report._verdict(wells, **kw)

    def test_deep_lift_headline_and_note_shape(self):
        vcls, vword, vnote = self._verdict(
            [_row(500, 2, 60), _row(520, 3, 55), _row(480, 4, 70)])
        self.assertEqual(vcls, "good")
        self.assertEqual(
            vword, "Workable for a house — low flow, deep reserve")
        # Household sentence leads the note; the hedge sits behind
        # the reserve sentence, never beside the headline.
        self.assertLess(vnote.index("can carry a household"),
                        vnote.index("sustained yield is unestablished"))
        self.assertIn("gallons per minute (gpm)", vnote)

    def test_thin_cap_headline_names_the_action(self):
        vcls, vword, _ = self._verdict(
            [_row(100, 15, 85), _row(110, 20, 88), _row(95, 25, 80)])
        self.assertEqual(vcls, "caution")
        self.assertEqual(
            vword,
            "Strong flow, thin reserve — ask a driller before you commit")

    def test_recorded_yields_headline(self):
        vcls, vword, _ = self._verdict(
            [_row(500, 1, 60), _row(520, 1, 55), _row(480, 2, 70)])
        self.assertEqual(vcls, "caution")
        self.assertEqual(
            vword,
            "Water on record, amount unproven — get driller opinions "
            "before you commit")

    def test_no_record_headline(self):
        vcls, vword, _ = self._verdict([])
        self.assertEqual(
            vword, "No local record to lean on — budget for the deep end")

    def test_tract_lift_headline(self):
        vcls, vword, _ = self._verdict(
            [_row(500, 2, 60), _row(520, 3, 55), _row(480, 4, 70)],
            parcel_acres=10)
        self.assertEqual(vcls, "good")
        self.assertEqual(
            vword,
            "Workable water, deep reserve — approvals decide this tract")


class TestStorageStatsLead(unittest.TestCase):
    """Finding 4: under a storage-lifted 'good', the stats row
    leads with the reserve figure; median yield is demoted, kept."""

    def _stats_segment(self, html):
        start = html.index('<div class="stats">')
        end = html.index('<div class="card">', start)
        return html[start:end]

    def test_lift_stats_lead_with_storage(self):
        rows = [_row(500, 2, 60), _row(520, 3, 55), _row(480, 4, 70)]
        with tempfile.TemporaryDirectory() as tmp:
            wells = _pull(rows, _order(), _geo(), tmp)
            sb = _real_setbacks(tmp)
            html = Path(screen.build_screen(_order(), _geo(), wells,
                                            _wq(), sb, tmp)).read_text()
            stats = self._stats_segment(html)
            self.assertIn("standing reserve in the bore", stats)
            self.assertLess(stats.index("standing reserve in the bore"),
                            stats.index("median yield"))

    def test_plain_good_stats_have_no_storage_lead(self):
        rows = [_row(400, 12, 60), _row(420, 15, 70), _row(380, 20, 50)]
        with tempfile.TemporaryDirectory() as tmp:
            wells = _pull(rows, _order(), _geo(), tmp)
            sb = _real_setbacks(tmp)
            html = Path(screen.build_screen(_order(), _geo(), wells,
                                            _wq(), sb, tmp)).read_text()
            stats = self._stats_segment(html)
            self.assertNotIn("standing reserve in the bore", stats)
            self.assertIn("median yield", stats)


class TestOnePullPerProperty(unittest.TestCase):
    """Finding 8: a second flow for the same property reuses the
    stored pull — same wells, counts, storage, and pull date."""

    ROWS = [_row(500, 2, 60, "10 A St"), _row(520, 3, 55, "11 A St"),
            _row(480, 4, 70, "12 A St")]

    def test_second_flow_reuses_pull_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.join(tmp, "fulfillment")
            os.makedirs(base)
            d1, d2 = os.path.join(tmp, "o1"), os.path.join(tmp, "o2")
            os.makedirs(d1); os.makedirs(d2)
            geo = {"lat": 41.0, "lon": -72.0}
            first = _pull(self.ROWS, _order("ORD-1"), geo, d1, base)
            # Second flow: any network call is a failure of the test.
            boom = Mock(side_effect=AssertionError("network called"))
            order2 = _order("ORD-2")
            with patch.object(nearby_wells.requests, "get", boom):
                second = nearby_wells.nearby_wells(order2, geo, d2, base)
            self.assertEqual(second["total_water_supply_wells"],
                             first["total_water_supply_wells"])
            self.assertEqual(second["wells"], first["wells"])
            self.assertEqual(second["pulled_at"], first["pulled_at"])
            self.assertEqual(second["pull_reused_for"], "ORD-2")
            # The per-order copy still lands in the second order dir.
            with open(os.path.join(d2, "nearby_wells.json")) as f:
                copied = json.load(f)
            self.assertEqual(copied["total_water_supply_wells"], 3)
            # Snapshot vs packet read the same numbers (Finding 8).
            from pipeline import nearby_wells as nw
            s_first = nw.storage_summary(first["wells"])
            s_second = nw.storage_summary(second["wells"])
            self.assertEqual(s_first["median_storage_gal"],
                             s_second["median_storage_gal"])

    def test_other_property_pulls_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.join(tmp, "fulfillment")
            os.makedirs(base)
            d1, d3 = os.path.join(tmp, "o1"), os.path.join(tmp, "o3")
            os.makedirs(d1); os.makedirs(d3)
            geo = {"lat": 41.0, "lon": -72.0}
            _pull(self.ROWS, _order("ORD-1"), geo, d1, base)
            far_order = _order("ORD-3")
            far_order["property_address"] = "99 Far Rd, Suffield, CT"
            far_geo = {"lat": 42.0, "lon": -72.9}
            third = _pull([_row(300, 9, 30)], far_order, far_geo, d3, base)
            self.assertEqual(third["total_water_supply_wells"], 1)
            self.assertNotIn("pull_reused_for", third)


class TestDraftRenameWiring(unittest.TestCase):
    """Finding 8: each flow writes its draft under a name that says
    what the document is; the delivery name holds only deliveries."""

    HERE = os.path.dirname(os.path.abspath(__file__))

    def _src(self, name):
        return io.open(os.path.join(self.HERE, name),
                       encoding="utf-8").read()

    def test_run_screen_reply_drafts_renamed(self):
        src = self._src("run_screen.py")
        self.assertEqual(src.count('"customer_reply_draft.txt"'), 3)
        # The one remaining delivery_draft write is the success path.
        self.assertEqual(src.count('"delivery_email_draft.txt"'), 2)

    def test_run_pipeline_concierge_names(self):
        src = self._src("run_pipeline.py")
        self.assertIn('"concierge_delivery_draft.txt"', src)
        self.assertNotIn("delivery_email_draft.txt", src)

    def test_ingest_final_renamed(self):
        src = self._src("ingest_quotes.py")
        self.assertIn('"concierge_delivery_final.txt"', src)
        self.assertNotIn("delivery_email_final.txt", src)

    def test_run_report_clarification_is_a_reply(self):
        src = self._src("run_report.py")
        self.assertEqual(src.count('"customer_reply_draft.txt"'), 1)
        # Report-tier success keeps the delivery name.
        self.assertEqual(src.count('"delivery_email_draft.txt"'), 2)


if __name__ == "__main__":
    unittest.main()
