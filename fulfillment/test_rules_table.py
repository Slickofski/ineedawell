"""Rules-table schema test (rules package step 6, 2026-10-16, Remy).

The rules table is the single source of truth for CT well rules; this
test is the tripwire that keeps it honest:
  - every rule carries a citation, a confidence label from the enum,
    and a verified_date;
  - rule ids are unique and every town maps to a real district;
  - the B51d band distances are exactly 75 / 150 / 200 by demand band;
  - the new-well testing panel matches current CGS 19a-37(c)(2):
    15 baseline parameters + conditional first-draw lead (R17,
    corrected 2026-10-22 — the earlier 16-count came from the 2022
    enactment/circular and was superseded);
  - no dead phrase from the audit ships in any rule text
    (M1 yield framing, M7 count, "Table 9").
Run: python3 -m unittest test_rules_table -v  (from fulfillment/)
"""
import sys
import unittest

sys.path.insert(0, ".")
from pipeline import rules  # noqa: E402

DEAD_PHRASES = [
    "yields 10",
    "does not automatically include arsenic",
    "never below 25 ft",
    "Table 9",
    "17-item",
    "pumping <10 gpm",
]


class TestRulesTable(unittest.TestCase):
    def setUp(self):
        self.table = rules.load()
        self.by_id = {r["id"]: r for r in self.table["rules"]}

    def test_every_rule_has_citation_confidence_date(self):
        for r in self.table["rules"]:
            self.assertTrue(r.get("citation"), r["id"])
            self.assertIn(r.get("confidence"),
                          self.table["confidence_values"], r["id"])
            self.assertTrue(r.get("verified_date"), r["id"])
            self.assertTrue(r.get("subject"), r["id"])
            self.assertTrue(r.get("condition"), r["id"])

    def test_ids_unique(self):
        ids = [r["id"] for r in self.table["rules"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_towns_map_to_real_districts(self):
        for town, key in self.table["town_district"].items():
            self.assertIn(key, self.table["districts"], town)
        for key, d in self.table["districts"].items():
            self.assertIn(d["docs_state"],
                          ("harvested", "not_yet_harvested"), key)

    def test_b51d_bands_are_demand_keyed_75_150_200(self):
        self.assertEqual(
            self.by_id["ct_b51d_pollution_under_10"]["value_ft"], 75)
        self.assertEqual(
            self.by_id["ct_b51d_pollution_10_50"]["value_ft"], 150)
        self.assertEqual(
            self.by_id["ct_b51d_pollution_over50"]["value_ft"], 200)
        for suffix in ("under_10", "10_50", "over50"):
            cond = self.by_id[f"ct_b51d_pollution_{suffix}"]["condition"]
            self.assertIn("required withdrawal rate", cond)

    def test_operating_assumption_keeps_opinion_label(self):
        r = self.by_id["ct_band_basis_operating_assumption"]
        self.assertEqual(r["confidence"], "OPINION")

    def test_panel_matches_cgs_19a_37_c2(self):
        # Current CGS 19a-37(c)(2) (verified 2026-10-22): 15 baseline
        # parameters; lead is NOT baseline — a first-draw lead sample
        # is required only when the well serves an existing structure.
        panel = rules.testing_panel()
        self.assertEqual(len(panel), 15)
        self.assertNotIn("lead", panel)
        for p in ("arsenic", "uranium", "coliform"):
            self.assertIn(p, panel)
        cond = rules.testing_panel_conditional()
        self.assertEqual([c["parameter"] for c in cond], ["lead"])
        self.assertIn("existing structure", cond[0]["condition"])
        self.assertIn("first draw", cond[0]["condition"])

    def test_doubling_rule_present_and_uncomputed(self):
        r = self.by_id["ct_b51d_doubling"]
        self.assertEqual(r["multiplier"], 2)
        self.assertIn("1.0", r["condition"])
        self.assertIn("8 ft", r["condition"])

    def test_ncdhd_guidance_labeled_guidance_not_code(self):
        r = self.by_id["ncdhd_well_boundary_guidance"]
        self.assertEqual(r["confidence"], "GUIDANCE")
        self.assertEqual(r["value_ft"], 25)

    def test_cost_entries_carry_full_sourcing(self):
        # Cost-source pass (2026-11-04): cost entries meet the
        # setbacks' sourcing standard (citation / confidence /
        # verified_date, already asserted above for every rule) plus
        # the cost-specific fields: a figure, a scope, a source URL
        # and source date, and a buyer sentence.
        cost = [r for r in self.table["rules"]
                if r["applies_to"] == "cost"]
        self.assertEqual(len(cost), 8)
        for r in cost:
            self.assertTrue(r.get("scope"), r["id"])
            self.assertTrue(r.get("source_url"), r["id"])
            self.assertTrue(r.get("source_date"), r["id"])
            self.assertTrue(r.get("buyer_sentence"), r["id"])
            self.assertTrue(r.get("band"), r["id"])
            self.assertTrue(
                r.get("rate_usd_per_ft") is not None
                or r.get("amount_usd") is not None
                or r.get("amount_range_usd") is not None, r["id"])

    def test_cost_figures_are_the_approved_ones(self):
        # The rates did not change in the cost-source pass — their
        # SCOPE did ("drilling and casing", not the complete-system
        # scope the published $25-$65 range is quoted under).
        self.assertEqual(
            self.by_id["ct_cost_drilling_casing_low"]["rate_usd_per_ft"],
            25)
        plan = self.by_id["ct_cost_drilling_casing_plan"]
        self.assertEqual(plan["rate_usd_per_ft"], 45)
        self.assertEqual(plan["confidence"], "OPINION")
        self.assertEqual(
            self.by_id["ct_cost_drilling_casing_high"]
            ["rate_usd_per_ft"], 65)
        self.assertEqual(
            self.by_id["ct_cost_dry_hole_contingency"]
            ["amount_range_usd"], [10000, 25000])
        self.assertEqual(
            self.by_id["ct_cost_dry_hole_contingency"]["confidence"],
            "OPINION")
        self.assertEqual(
            self.by_id["ct_fee_well_permit_ncdhd"]["amount_usd"], 200)
        self.assertEqual(
            self.by_id["ct_fee_site_investigation_ncdhd"]["amount_usd"],
            250)
        self.assertEqual(
            self.by_id["ct_fee_well_only_whbhd"]["amount_usd"], 150)
        self.assertEqual(
            self.by_id["ct_fee_site_testing_whbhd"]["amount_usd"], 300)
        for r in self.table["rules"]:
            if r["applies_to"] == "cost" and r.get("rate_usd_per_ft"):
                self.assertIn("Drilling and casing", r["scope"], r["id"])

    def test_no_dead_phrases_in_rule_text(self):
        blob = " ".join(
            f"{r.get('subject', '')} {r.get('condition', '')} "
            f"{r.get('buyer_sentence', '')}"
            for r in self.table["rules"]
        )
        for phrase in DEAD_PHRASES:
            self.assertNotIn(phrase, blob, phrase)

    def test_reservoir_rule_is_leaching_side(self):
        r = self.by_id["ct_techstd_leaching_reservoir"]
        self.assertEqual(r["applies_to"], "leaching")
        ids = [i["id"] for i in rules.well_setback_items(town="East Windsor")]
        self.assertNotIn("reservoir", ids)


if __name__ == "__main__":
    unittest.main()
