#!/usr/bin/env python3
"""Regression tests for Codex R19 (2026-10-22 P2 pass): optional
triage must never crash on valid JSON with the wrong schema.

Before the fix, a payload of [], {"answers": []}, or an array where
the address_scope object belonged escaped as AttributeError —
payload.get sat outside the RuntimeError boundary and triage only
caught RuntimeError — halting the free runner. Invalid JSON itself
already degraded correctly.

Contract: every malformed-shape or out-of-range provider response
degrades to decision "skipped" with the reason recorded and
triage.json written — triage() never raises on provider output.
Run: python3 -m unittest test_codex_r19
"""
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.request
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import jev_triage

ORDER = {"property_address": "183 Chamberlain Rd, East Windsor, CT 06016",
         "tier": "free"}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _run_with_payload(payload):
    body = json.dumps(payload).encode()
    with tempfile.TemporaryDirectory() as d:
        with patch.object(jev_triage, "_resolve_auth",
                          return_value=("env", "synthetic-key")), \
                patch.object(urllib.request, "urlopen",
                             return_value=_Resp(body)):
            result = jev_triage.triage(ORDER, d, api_key="<redacted>")
        written = os.path.exists(os.path.join(d, "triage.json"))
    return result, written


class TestWrongSchemaDegrades(unittest.TestCase):
    def _assert_skipped(self, payload):
        result, written = _run_with_payload(payload)
        self.assertEqual("skipped", result["decision"])
        self.assertTrue(result["reason"])
        self.assertTrue(written)

    def test_payload_is_a_list(self):
        self._assert_skipped([])

    def test_answers_is_a_list(self):
        self._assert_skipped({"answers": []})

    def test_address_scope_is_a_list(self):
        self._assert_skipped({"answers": {
            "address_scope": [],
            "genuine_request": {"noul": 0.9}}})

    def test_genuine_request_is_a_list(self):
        self._assert_skipped({"answers": {
            "address_scope": {"choice": "connecticut_address",
                              "confidence": 0.9},
            "genuine_request": []}})

    def test_confidence_out_of_range(self):
        self._assert_skipped({"answers": {
            "address_scope": {"choice": "out_of_state",
                              "confidence": 1.7},
            "genuine_request": {"noul": 0.9}}})

    def test_wellformed_still_proceeds(self):
        result, _ = _run_with_payload({"answers": {
            "address_scope": {"choice": "connecticut_address",
                              "confidence": 0.9},
            "genuine_request": {"noul": 0.95}}})
        self.assertEqual("proceed", result["decision"])


if __name__ == "__main__":
    unittest.main()
