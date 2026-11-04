#!/usr/bin/env python3
"""Regression tests for Codex R18 (2026-10-22 P2 pass): the HTML
setback helpers dropped rule conditions/citations/confidence and
appended a generic "ft minimum" to every number — so NCDHD's
'if possible' guidance rendered as "25 ft minimum" on the same
page that called it guidance.

Contract (report._setback_rows and screen._setback_mini alike):
each rendered number carries its condition, its scaling note,
its source and its confidence; an item whose confidence is
GUIDANCE renders as guidance, never as a minimum.
Run: python3 -m unittest test_codex_r18
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import report, rules, screen


def _html():
    items = rules.well_setback_items(town="East Windsor")
    sb = {"items": [dict(it, status="needs_field_verification",
                         finding="field check") for it in items]}
    return report._setback_rows(sb), screen._setback_mini(sb)


class TestSetbackRendering(unittest.TestCase):
    def test_guidance_is_never_a_minimum(self):
        for html in _html():
            blocks = html.split('<div class="setback">')
            guidance = next(b for b in blocks
                            if "property boundary" in b)
            self.assertNotIn("25 ft minimum", guidance)
            self.assertIn("not a state minimum", guidance)
            # (25 ft minimum legitimately appears for the CODE
            # watercourse/sewer items — only guidance is mislabeled
            # by the old renderer.)

    def test_conditions_scales_and_sources_render(self):
        for html in _html():
            # septic condition + scaling + citation beside the number
            self.assertIn("required withdrawal rate under 10 gpm", html)
            self.assertIn("150 ft if the supply must deliver 10-50",
                          html)
            self.assertIn("19-13-B51d(a)(2)", html)
            # the doubling rule keeps its applicability condition
            self.assertIn("percolation rate faster than 1.0", html)
            # real minima still say minimum
            self.assertIn("75 ft minimum", html)


if __name__ == "__main__":
    unittest.main()
