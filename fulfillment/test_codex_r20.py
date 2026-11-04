#!/usr/bin/env python3
"""Regression tests for Codex R20 (2026-10-22 P2 pass): an HTTP-200
HTML error page must not become water-quality sample evidence.

Before the fix, water_quality._count fed any 200 body to
csv.DictReader and counted rows: a three-line HTML error page
became two "regional sample rows".

Contract (validator ported from the original worker pipeline):
  - the CSV must carry the expected WQP Result headers
    (MonitoringLocationIdentifier, CharacteristicName,
    ResultMeasureValue) and every row's CharacteristicName must
    match the queried characteristic;
  - statuses distinguish available / valid_empty / invalid /
    unavailable, and an invalid or unavailable count is None —
    unknown, never a fabricated zero.
Run: python3 -m unittest test_codex_r20
"""
import os
import sys
import unittest
from unittest.mock import patch

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import water_quality

HEADERS = ("MonitoringLocationIdentifier,CharacteristicName,"
           "ResultMeasureValue\n")
HTML = ("<html><head><title>Error</title></head>\n"
        "<body><h1>Service unavailable</h1></body>\n</html>\n")


class _Resp:
    def __init__(self, text, status=200):
        self.text = text
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(f"{self.status}")


def _get(text, status=200):
    return patch.object(water_quality.requests, "get",
                        return_value=_Resp(text, status))


class TestCountValidation(unittest.TestCase):
    def test_html_error_page_is_invalid_not_evidence(self):
        with _get(HTML):
            out = water_quality._count("Arsenic", 41.0, -72.0)
        self.assertEqual("invalid", out["status"])
        self.assertIsNone(out["result_rows"])

    def test_valid_csv_counts_rows(self):
        body = HEADERS + "USGS-1,Arsenic,3\nUSGS-2,Arsenic,5\n"
        with _get(body):
            out = water_quality._count("Arsenic", 41.0, -72.0)
        self.assertEqual("available", out["status"])
        self.assertEqual(2, out["result_rows"])

    def test_headers_only_is_valid_empty(self):
        with _get(HEADERS):
            out = water_quality._count("Arsenic", 41.0, -72.0)
        self.assertEqual("valid_empty", out["status"])
        self.assertEqual(0, out["result_rows"])

    def test_unrelated_characteristic_rows_are_invalid(self):
        body = HEADERS + "USGS-1,Lead,3\n"
        with _get(body):
            out = water_quality._count("Arsenic", 41.0, -72.0)
        self.assertEqual("invalid", out["status"])
        self.assertIsNone(out["result_rows"])

    def test_http_failure_is_unavailable(self):
        with _get("nope", status=503):
            out = water_quality._count("Arsenic", 41.0, -72.0)
        self.assertEqual("unavailable", out["status"])
        self.assertIsNone(out["result_rows"])

    def test_full_run_survives_an_invalid_response(self):
        import tempfile
        with _get(HTML), tempfile.TemporaryDirectory() as d:
            result = water_quality.water_quality({}, {"lat": 41.0,
                                                      "lon": -72.0}, d)
        self.assertIsNone(result["arsenic"]["result_rows"])
        self.assertIsNone(result["uranium"]["result_rows"])
        detail = result["flags"][0]["detail"]
        self.assertIn("unavailable (not zero)", detail)


if __name__ == "__main__":
    unittest.main()
