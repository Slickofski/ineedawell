#!/usr/bin/env python3
"""Regression tests for Codex R06 (2026-10-22 P2 pass): an imported
order_ref must never escape the orders/outbox storage roots.

Before the fix, intake.new_order_id returned a supplied order_ref
verbatim, and the runners joined it straight into storage paths:
an absolute ref discarded the root entirely and '../' refs wrote
artifacts outside the retention inventory (overwriting files).

Contract after the fix:
  - order_ref is a single safe path component (letters/digits/dash/
    underscore, no separators, no dots, bounded length) — anything
    else is rejected by new_order_id BEFORE any directory is made;
  - intake.order_paths() resolves the order/outbox dirs and asserts
    containment directly under the storage roots.
Run: python3 -m unittest test_codex_r06
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import intake


class TestOrderRefValidation(unittest.TestCase):
    BAD_REFS = [
        "../escape",
        "../../etc/passwd",
        "..",
        ".",
        "/absolute/path",
        "a/b",
        "a\\b",
        "..\\windows",
        "ORD-20261022-ok/../../escape",
        "has space",
        "dot.name",
        "x" * 200,
        "-leading-dash",
    ]

    def test_empty_ref_is_not_a_ref(self):
        # An empty/blank order_ref is "absent", not an attack: the
        # generator fallback runs (pre-existing behavior). But the
        # validator itself rejects blanks if one ever reaches it.
        for ref in ("", "   "):
            with self.subTest(ref=ref):
                with self.assertRaises(ValueError):
                    intake.validate_order_ref(ref)
                with self.assertRaises(ValueError):
                    intake.order_paths("/nonexistent-base", ref)

    def test_traversal_and_absolute_refs_rejected(self):
        for ref in self.BAD_REFS:
            with self.subTest(ref=ref):
                with self.assertRaises(ValueError):
                    intake.new_order_id({"order_ref": ref})

    def test_nonstring_ref_rejected(self):
        for ref in (123, ["ORD-1"], {"x": 1}):
            with self.subTest(ref=ref):
                with self.assertRaises(ValueError):
                    intake.new_order_id({"order_ref": ref})

    def test_legitimate_refs_accepted(self):
        for ref in ("ORD-20261022-AbC123xYz",
                    "ORD-20260918-calibration-test-1540-sullivan-ave",
                    "ORD-20260918-test-user-193-wells-rd"):
            with self.subTest(ref=ref):
                self.assertEqual(
                    ref, intake.new_order_id({"order_ref": ref}))

    def test_generated_fallback_id_is_valid(self):
        oid = intake.new_order_id(
            {"name": "Test Person", "property_address": "1 Main St, X, CT"})
        self.assertEqual(oid, intake.new_order_id({"order_ref": oid}))

    # Codex review of PR #7, Finding 2: the fallback branch of
    # new_order_id() could mint an ID its own validator rejects —
    # slugify caps each component at 60, so "ORD-" + date + "-" +
    # 60 + "-" + 60 = 134 chars > the 128-char ORDER_REF_RE cap.
    LONG_NAME = "Bartholomew Alexander Christopher " * 3
    LONG_STREET = ("12345 Somewhere Over The Rainbow Boulevard "
                   "Extravaganza")
    LONG_ADDRESS = LONG_STREET + ", East Windsor, CT 06088"

    def test_generated_fallback_id_respects_validator_limit(self):
        raw = {"name": self.LONG_NAME,
               "property_address": self.LONG_ADDRESS}
        ref = intake.new_order_id(raw)
        self.assertLessEqual(len(ref), 128)
        self.assertEqual(intake.validate_order_ref(ref), ref)
        # Deterministic: same input, same ID.
        self.assertEqual(intake.new_order_id(raw), ref)
        self.assertTrue(
            ref.startswith("ORD-" + f"{date.today():%Y%m%d}"))

    def test_intake_long_name_and_street_writes_order(self):
        raw = {"name": self.LONG_NAME,
               "email": "customer@example.com",
               "property_address": self.LONG_ADDRESS,
               "tier": "free"}
        with tempfile.TemporaryDirectory() as base:
            order_dir = os.path.join(base, "order")
            os.mkdir(order_dir)
            order = intake.intake(raw, order_dir)
            order_json = os.path.join(order_dir, "order.json")
            self.assertTrue(os.path.exists(order_json))
            with open(order_json) as f:
                written = json.load(f)
            self.assertEqual(written["order_id"], order["order_id"])
            self.assertEqual(
                intake.validate_order_ref(order["order_id"]),
                order["order_id"])


class TestOrderPathsContainment(unittest.TestCase):
    def test_paths_resolve_directly_under_roots(self):
        with tempfile.TemporaryDirectory() as base:
            order_dir, outbox_dir = intake.order_paths(base, "ORD-20261022-abc")
            self.assertEqual(os.path.dirname(os.path.realpath(order_dir)),
                             os.path.realpath(os.path.join(base, "orders")))
            self.assertEqual(os.path.dirname(os.path.realpath(outbox_dir)),
                             os.path.realpath(os.path.join(base, "outbox")))

    def test_traversal_ref_creates_nothing(self):
        with tempfile.TemporaryDirectory() as base:
            with self.assertRaises(ValueError):
                intake.order_paths(base, "../escape")
            self.assertEqual([], os.listdir(base))


if __name__ == "__main__":
    unittest.main()
