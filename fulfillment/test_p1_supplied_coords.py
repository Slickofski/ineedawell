"""P1 regression tests — caller-supplied coordinates skip geocoding.

Stranger-test finding B1 (2026-10-29): fulfillment/README.md documents
"An order may include optional lat/lon to skip geocoding", but
geocode() ran the geocoders anyway and the ambiguity gate dead-ended
183 Chamberlain Rd even when the order carried the known listing
coordinates. The fix: when an order carries BOTH lat and lon, the
supplied pair is validated (numeric, finite, inside the CT bounding
box) and used as the run's coordinates — the geocoders and the
ambiguity gate are not consulted — and provenance is recorded in
geocode.json (source "order_supplied"). Orders WITHOUT coordinates
keep the fail-closed verified-geocoder path (guarded by
test_codex_r02.py, including its ambiguity test).

Run: python3 -m unittest test_p1_supplied_coords -v  (from fulfillment/)
No network: the geocoder HTTP layer is rigged to raise if touched.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, ".")
from pipeline import geocode  # noqa: E402
import run_screen  # noqa: E402

CHAMBERLAIN = "183 Chamberlain Rd, East Windsor, CT 06016"
LISTING_LAT, LISTING_LON = 41.9006, -72.5235


def _no_network(url, **kwargs):
    raise AssertionError(
        f"geocoder HTTP must not be called for supplied coords ({url})")


class TestSuppliedCoordsSkipGeocoding(unittest.TestCase):
    def test_chamberlain_with_listing_coords_completes_geocode_stage(self):
        order = {"property_address": CHAMBERLAIN,
                 "lat": LISTING_LAT, "lon": LISTING_LON}
        with tempfile.TemporaryDirectory() as d, \
                patch.object(geocode.requests, "get",
                             side_effect=_no_network), \
                patch.object(geocode, "_rate_limit", lambda: None):
            result = geocode.geocode(order, d)
            # geocode.json on disk carries the supplied provenance.
            with open(os.path.join(d, "geocode.json")) as f:
                on_disk = json.load(f)
        self.assertEqual(result["lat"], LISTING_LAT)
        self.assertEqual(result["lon"], LISTING_LON)
        self.assertEqual(on_disk["source"], "order_supplied")
        self.assertTrue(on_disk["supplied_coordinates"])
        self.assertEqual(on_disk["lat"], LISTING_LAT)
        self.assertEqual(on_disk["lon"], LISTING_LON)
        self.assertEqual(on_disk["town"], "East Windsor")
        self.assertEqual(on_disk["state_code"], "CT")
        # The run's next gate (run_screen scope guard) accepts it:
        # structured fields verify and the supplied pair agrees.
        self.assertIsNone(run_screen.scope_check(result, order))

    def test_supplied_coords_outside_ct_still_held(self):
        # Boston coordinates for a CT address: fail-closed, no run.
        order = {"property_address": CHAMBERLAIN,
                 "lat": 42.3601, "lon": -71.0589}
        with tempfile.TemporaryDirectory() as d, \
                patch.object(geocode.requests, "get",
                             side_effect=_no_network):
            with self.assertRaises(geocode.AddressError):
                geocode.geocode(order, d)
            self.assertFalse(os.path.exists(
                os.path.join(d, "geocode.json")))

    def test_supplied_coords_non_numeric_held(self):
        order = {"property_address": CHAMBERLAIN,
                 "lat": "north", "lon": LISTING_LON}
        with tempfile.TemporaryDirectory() as d, \
                patch.object(geocode.requests, "get",
                             side_effect=_no_network):
            with self.assertRaises(geocode.AddressError):
                geocode.geocode(order, d)


if __name__ == "__main__":
    unittest.main()
