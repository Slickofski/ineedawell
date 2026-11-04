#!/usr/bin/env python3
"""Regression tests for Codex R26 (2026-10-22 P2 pass): a partial
shared cache must not break subsequent screens.

Before the fix, the cache was overwritten with a direct json.dump
(a killed writer leaves a truncated file), and readers trusted a
fresh mtime + json.load with no recovery: the truncated cache
raised uncaught JSONDecodeError and stayed "fresh" for the whole
30-day window. Concurrent MCP calls share this path.

Contract: cache writes are atomic (temp file in the same directory
+ os.replace); an invalid or wrong-shape cache read is treated as
a cache MISS and refetched, never an exception.
Run: python3 -m unittest test_codex_r26 (from this directory)
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wellscreen

ROWS = [{"well_address": "1 A St", "type_of_report": "Water Supply Well"}]


class TestCacheRecovery(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = os.path.join(self._tmp.name, "ct_wells.json")
        self._patch = patch.object(wellscreen, "CACHE", self.cache)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def _fetch(self):
        with patch.object(wellscreen, "get", return_value=list(ROWS)):
            return wellscreen.ct_wells()

    def test_truncated_fresh_cache_is_a_miss(self):
        with open(self.cache, "w") as f:
            f.write('[{"well_address": "1 A St", "type_of_rep')  # torn
        rows, fetched = self._fetch()
        self.assertEqual(ROWS, rows)
        self.assertTrue(fetched)
        # ...and the rewritten cache is complete, valid JSON.
        with open(self.cache) as f:
            self.assertEqual(ROWS, json.load(f))

    def test_wrong_shape_cache_is_a_miss(self):
        with open(self.cache, "w") as f:
            json.dump({"not": "a list"}, f)
        rows, fetched = self._fetch()
        self.assertEqual(ROWS, rows)
        self.assertTrue(fetched)

    def test_valid_fresh_cache_is_used(self):
        with open(self.cache, "w") as f:
            json.dump(ROWS, f)
        with patch.object(wellscreen, "get",
                          side_effect=AssertionError("network touched")):
            rows, fetched = wellscreen.ct_wells()
        self.assertEqual(ROWS, rows)
        self.assertFalse(fetched)

    def test_no_temp_files_left_behind(self):
        self._fetch()
        names = os.listdir(self._tmp.name)
        self.assertEqual(["ct_wells.json"], names)


if __name__ == "__main__":
    unittest.main()
