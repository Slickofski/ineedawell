"""Codex R02 regression tests — structured geocode verification.

The local geocoder must verify the geocoder's STRUCTURED address
components (state, house number, road, town), never the submitted
address text; coarse / mismatched / coordinate-contradicting results
are held for clarification, never silently accepted.

Run: python3 -m unittest test_codex_r02 -v  (from fulfillment/)
All HTTP is mocked — no live network calls.
"""
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, ".")
from pipeline import geocode  # noqa: E402
import run_screen  # noqa: E402

CT_ADDRESS = "183 Chamberlain Rd, East Windsor, CT 06016"
BOSTON_LAT, BOSTON_LON = 42.3601, -71.0589


def nominatim_response(hits):
    r = Mock()
    r.json.return_value = hits
    r.raise_for_status.return_value = None
    return r


def ct_hit(**over):
    hit = {
        "lat": "41.9021838",
        "lon": "-72.5408222",
        "display_name": ("183, Chamberlain Road, East Windsor, "
                         "Connecticut, 06016, United States"),
        "address": {
            "house_number": "183",
            "road": "Chamberlain Road",
            "town": "East Windsor",
            "state": "Connecticut",
            "ISO3166-2-lvl4": "US-CT",
            "country_code": "us",
            "postcode": "06016",
        },
    }
    hit.update(over)
    return hit


def run_geocode(order, hits):
    """Call geocode.geocode with the HTTP layer mocked out."""
    def fake_get(url, **kwargs):
        if "reverse" in url:
            # Legacy path for caller-supplied coordinates.
            return _reverse_response()
        return nominatim_response(hits)

    with tempfile.TemporaryDirectory() as d, \
            patch.object(geocode.requests, "get", side_effect=fake_get), \
            patch.object(geocode, "_rate_limit", lambda: None):
        return geocode.geocode(order, d)


def _reverse_response():
    r = Mock()
    r.json.return_value = {"address": {"town": "East Windsor",
                                       "state": "Connecticut"}}
    r.raise_for_status.return_value = None
    return r


class TestStructuredState(unittest.TestCase):
    def test_a_submitted_ct_text_but_structured_match_is_ma(self):
        # (a) Submitted text says CT; the structured match is Boston, MA.
        boston = {
            "lat": str(BOSTON_LAT), "lon": str(BOSTON_LON),
            "display_name": "100, Main Street, Boston, Massachusetts, "
                            "02101, United States",
            "address": {"house_number": "100", "road": "Main Street",
                        "city": "Boston", "state": "Massachusetts",
                        "ISO3166-2-lvl4": "US-MA", "country_code": "us",
                        "postcode": "02101"},
        }
        with self.assertRaises(ValueError):
            run_geocode({"property_address":
                         "100 Main St, Hartford, CT 06103"}, [boston])

    def test_missing_structured_state_is_not_verified_from_text(self):
        hit = ct_hit()
        del hit["address"]["state"]
        del hit["address"]["ISO3166-2-lvl4"]
        with self.assertRaises(ValueError):
            run_geocode({"property_address": CT_ADDRESS}, [hit])


class TestPrecisionAndIdentity(unittest.TestCase):
    def test_b_coarse_street_level_match_held(self):
        # (b) Street-only hit: no house_number -> not house-level.
        coarse = ct_hit()
        del coarse["address"]["house_number"]
        coarse["display_name"] = ("Chamberlain Road, East Windsor, "
                                   "Connecticut, 06016, United States")
        with self.assertRaises(ValueError):
            run_geocode({"property_address": CT_ADDRESS}, [coarse])

    def test_b_town_centroid_match_held(self):
        centroid = {
            "lat": "41.92", "lon": "-72.54",
            "display_name": "East Windsor, Connecticut, United States",
            "address": {"town": "East Windsor", "state": "Connecticut",
                        "ISO3166-2-lvl4": "US-CT", "country_code": "us"},
        }
        with self.assertRaises(ValueError):
            run_geocode({"property_address": CT_ADDRESS}, [centroid])

    def test_c_house_number_mismatch_held(self):
        # (c) Structured house number contradicts the submitted one.
        wrong = ct_hit()
        wrong["address"] = dict(wrong["address"], house_number="999")
        with self.assertRaises(ValueError):
            run_geocode({"property_address": CT_ADDRESS}, [wrong])

    def test_street_mismatch_held(self):
        wrong = ct_hit()
        wrong["address"] = dict(wrong["address"], road="Wells Road")
        with self.assertRaises(ValueError):
            run_geocode({"property_address": CT_ADDRESS}, [wrong])

    def test_ambiguous_multiple_verified_hits_held(self):
        other = ct_hit(lat="41.91", lon="-72.55")
        with self.assertRaises(ValueError):
            run_geocode({"property_address": CT_ADDRESS},
                        [ct_hit(), other])

    def test_zero_house_number_vacant_lot_held(self):
        with self.assertRaises(ValueError):
            run_geocode(
                {"property_address":
                 "0 Chamberlain Rd, East Windsor, CT 06016"},
                [ct_hit()])


class TestSuppliedCoordinates(unittest.TestCase):
    def test_d_supplied_boston_coords_contradicting_address_held(self):
        # (d) Caller-supplied Boston coordinates for a CT address.
        order = {"property_address": CT_ADDRESS,
                 "lat": BOSTON_LAT, "lon": BOSTON_LON}
        with self.assertRaises(ValueError):
            run_geocode(order, [ct_hit()])

    def test_supplied_coords_consistent_with_geocode_accepted(self):
        order = {"property_address": CT_ADDRESS,
                 "lat": 41.9022, "lon": -72.5408}
        result = run_geocode(order, [ct_hit()])
        self.assertAlmostEqual(result["lat"], 41.9021838, places=4)

    def test_partial_supplied_coords_held(self):
        order = {"property_address": CT_ADDRESS, "lat": 41.9}
        with self.assertRaises(ValueError):
            run_geocode(order, [ct_hit()])


class TestCleanAndVillages(unittest.TestCase):
    def test_e_clean_ct_house_level_match_accepted(self):
        # (e) Clean CT house-level match: accepted, identity retained.
        result = run_geocode({"property_address": CT_ADDRESS}, [ct_hit()])
        self.assertEqual(result["state_code"], "CT")
        self.assertEqual(result["precision"], "house")
        self.assertEqual(result["town"], "East Windsor")
        self.assertEqual(result["house_number"], "183")
        self.assertAlmostEqual(result["lat"], 41.9021838)
        self.assertAlmostEqual(result["lon"], -72.5408222)

    def test_village_broad_brook_maps_to_east_windsor(self):
        hit = {
            "lat": "41.9107955", "lon": "-72.5748722",
            "display_name": "193, Wells Road, Broad Brook, East Windsor, "
                            "Connecticut, 06088, United States",
            "address": {"house_number": "193", "road": "Wells Road",
                        "hamlet": "Broad Brook", "town": "East Windsor",
                        "state": "Connecticut", "ISO3166-2-lvl4": "US-CT",
                        "country_code": "us", "postcode": "06088"},
        }
        result = run_geocode(
            {"property_address": "193 Wells Rd, Broad Brook, CT 06016"},
            [hit])
        self.assertEqual(result["town"], "East Windsor")

    def test_village_hazardville_maps_to_enfield(self):
        hit = {
            "lat": "41.99", "lon": "-72.55",
            "display_name": "10, Main Street, Hazardville, Enfield, "
                            "Connecticut, 06082, United States",
            "address": {"house_number": "10", "road": "Main Street",
                        "village": "Hazardville", "town": "Enfield",
                        "state": "Connecticut", "ISO3166-2-lvl4": "US-CT",
                        "country_code": "us", "postcode": "06082"},
        }
        result = run_geocode(
            {"property_address": "10 Main St, Hazardville, CT 06082"},
            [hit])
        self.assertEqual(result["town"], "Enfield")

    def test_village_moodus_maps_to_east_haddam(self):
        hit = {
            "lat": "41.50", "lon": "-72.45",
            "display_name": "10, Main Street, Moodus, East Haddam, "
                            "Connecticut, 06469, United States",
            "address": {"house_number": "10", "road": "Main Street",
                        "hamlet": "Moodus", "town": "East Haddam",
                        "state": "Connecticut", "ISO3166-2-lvl4": "US-CT",
                        "country_code": "us", "postcode": "06469"},
        }
        result = run_geocode(
            {"property_address": "10 Main St, Moodus, CT 06469"}, [hit])
        self.assertEqual(result["town"], "East Haddam")


class TestRunScreenScopeGuard(unittest.TestCase):
    def test_f_boston_geo_with_ct_in_submitted_text_not_accepted(self):
        # (f) run_screen-level guard: a legacy/unverified geo result
        # pointing at Boston must be rejected even though the submitted
        # address text contains "CT".
        legacy_geo = {"lat": BOSTON_LAT, "lon": BOSTON_LON,
                      "source": "order_supplied",
                      "display_name": "Boston, Massachusetts, "
                                      "United States",
                      "town": "Boston"}
        reason = run_screen.scope_check(
            legacy_geo, "100 Main St, Hartford, CT 06103")
        self.assertIsNotNone(reason)

    def test_f_verified_geo_passes_scope_check(self):
        result = run_geocode({"property_address": CT_ADDRESS}, [ct_hit()])
        self.assertIsNone(run_screen.scope_check(result, CT_ADDRESS))


if __name__ == "__main__":
    unittest.main()
