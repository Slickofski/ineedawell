#!/usr/bin/env python3
"""Tests for pipeline/jev_triage.py.

Mock mode (no key, no network) — exercises the decision/threshold
logic. The same seven mock scenarios run two ways:

    python3 -m unittest test_jev_triage     (standard discovery —
        the scenarios as TestCases; P4, stranger-test Works #2:
        discovery used to run ZERO of these because the file
        defined no TestCases)
    python3 test_jev_triage.py              (script mode, same
        scenarios through the check() harness)

Live mode (hits the real Jev API; costs ~a tenth of a cent total):
    TYPESAFE_API_KEY=... python3 test_jev_triage.py --live
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline import jev_triage  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results = []

ORDER = {"property_address": "183 Chamberlain Rd, East Windsor, CT 06016",
         "tier": "free"}
MA_ORDER = dict(ORDER, property_address="12 Example Ct, Springfield, MA")
JUNK_ORDER = dict(ORDER, property_address="asdf jkl; 123")

CT_ANSWERS = {
    "address_scope": {"type": "choice", "choice": "connecticut_address",
                      "probabilities": {"connecticut_address": 0.93,
                                        "out_of_state": 0.05,
                                        "unparseable": 0.02},
                      "confidence": 0.88},
    "genuine_request": {"type": "noul", "noul": 0.97},
}
MA_ANSWERS = {
    "address_scope": {"type": "choice", "choice": "out_of_state",
                      "probabilities": {"out_of_state": 0.91,
                                        "connecticut_address": 0.06,
                                        "unparseable": 0.03},
                      "confidence": 0.86},
    "genuine_request": {"type": "noul", "noul": 0.95},
}
LOW_CONF_ANSWERS = {
    "address_scope": {"type": "choice", "choice": "out_of_state",
                      "probabilities": {"out_of_state": 0.45,
                                        "connecticut_address": 0.40,
                                        "unparseable": 0.15},
                      "confidence": 0.35},
    "genuine_request": {"type": "noul", "noul": 0.60},
}
JUNK_ANSWERS = {
    "address_scope": {"type": "choice", "choice": "unparseable",
                      "probabilities": {"unparseable": 0.97,
                                        "connecticut_address": 0.02,
                                        "out_of_state": 0.01},
                      "confidence": 0.95},
    "genuine_request": {"type": "noul", "noul": 0.08},
}


def check(name, cond, detail=""):
    results.append((PASS if cond else FAIL, name, detail))


def fake_call_factory(answers):
    def _fake(state, api_key):
        return answers, 120
    return _fake


# --- Mock scenarios (shared by the unittest battery + script mode) ---

def scenario_no_key():
    """No key anywhere -> skipped. Returns (result, triage_json_written)."""
    real_resolve = jev_triage._resolve_auth
    jev_triage._resolve_auth = lambda explicit=None: (None, None)
    try:
        with tempfile.TemporaryDirectory() as d:
            r = jev_triage.triage(ORDER, d, api_key="")
            written = os.path.exists(os.path.join(d, "triage.json"))
        return r, written
    finally:
        jev_triage._resolve_auth = real_resolve


def scenario_with_answers(order, answers):
    real_call = jev_triage._call_jev
    jev_triage._call_jev = fake_call_factory(answers)
    try:
        with tempfile.TemporaryDirectory() as d:
            return jev_triage.triage(order, d, api_key="x")
    finally:
        jev_triage._call_jev = real_call


def scenario_api_error():
    def _boom(state, api_key):
        raise RuntimeError("jev api http 429: rate limited")
    real_call = jev_triage._call_jev
    jev_triage._call_jev = _boom
    try:
        with tempfile.TemporaryDirectory() as d:
            return jev_triage.triage(ORDER, d, api_key="x")
    finally:
        jev_triage._call_jev = real_call


class TestJevTriageMock(unittest.TestCase):
    def test_no_key_skipped(self):
        # 1. No key anywhere -> skipped, pipeline runs as before.
        r, _written = scenario_no_key()
        self.assertEqual(r["decision"], "skipped", r["reason"])

    def test_triage_json_written(self):
        _r, written = scenario_no_key()
        self.assertTrue(written)

    def test_ct_high_conf_proceed(self):
        # 2. High-confidence CT -> proceed (geocode still the hard gate).
        r = scenario_with_answers(ORDER, CT_ANSWERS)
        self.assertEqual(r["decision"], "proceed", r["reason"])

    def test_ma_high_conf_out_of_scope(self):
        # 3. The old substring bug: "12 Example Ct, Springfield, MA"
        #    must NOT pass as CT. High-confidence out_of_state.
        r = scenario_with_answers(MA_ORDER, MA_ANSWERS)
        self.assertEqual(r["decision"], "out_of_scope", r["reason"])

    def test_low_conf_proceed_fail_open(self):
        # 4. Low confidence -> proceed (fail open to humans/geocoder).
        r = scenario_with_answers(MA_ORDER, LOW_CONF_ANSWERS)
        self.assertEqual(r["decision"], "proceed", r["reason"])

    def test_junk_held_for_review(self):
        # 5. Gibberish -> junk check runs first:
        #    P(genuine)=0.08 <= 0.20 -> held_for_review.
        r = scenario_with_answers(JUNK_ORDER, JUNK_ANSWERS)
        self.assertEqual(r["decision"], "held_for_review", r["reason"])

    def test_api_error_skipped(self):
        # 6. API error -> skipped, never breaks fulfillment.
        r = scenario_api_error()
        self.assertEqual(r["decision"], "skipped", r["reason"])


def run_mock():
    r, written = scenario_no_key()
    check("no key -> skipped", r["decision"] == "skipped", r["reason"])
    check("triage.json written", written)

    r = scenario_with_answers(ORDER, CT_ANSWERS)
    check("CT high-conf -> proceed", r["decision"] == "proceed", r["reason"])

    r = scenario_with_answers(MA_ORDER, MA_ANSWERS)
    check("MA high-conf -> out_of_scope", r["decision"] == "out_of_scope",
          r["reason"])

    r = scenario_with_answers(MA_ORDER, LOW_CONF_ANSWERS)
    check("low-conf -> proceed (fail open)", r["decision"] == "proceed",
          r["reason"])

    r = scenario_with_answers(JUNK_ORDER, JUNK_ANSWERS)
    check("junk -> held_for_review", r["decision"] == "held_for_review",
          r["reason"])

    r = scenario_api_error()
    check("api error -> skipped", r["decision"] == "skipped", r["reason"])


LIVE_CASES = [
    ("183 Chamberlain Rd, East Windsor, CT 06016", "proceed"),
    ("12 Example Ct, Springfield, MA", "out_of_scope"),
    ("1600 Pennsylvania Avenue NW, Washington, DC", "out_of_scope"),
    ("asdf jkl; 999", "held_for_review|unparseable"),
    ("Broad Brook, CT", "proceed|unparseable"),  # village, no street: edge case
]


def run_live():
    # api_key=None -> env TYPESAFE_API_KEY only; unset means triage skips.
    mode, _ = jev_triage._resolve_auth(None)
    if mode is None:
        print("No API key: set TYPESAFE_API_KEY.")
        sys.exit(2)
    print(f"live mode via auth: {mode}")
    for addr, expect in LIVE_CASES:
        with tempfile.TemporaryDirectory() as d:
            r = jev_triage.triage(
                {"property_address": addr, "tier": "free"}, d)
        ok = any(e == r["decision"] for e in expect.split("|"))
        detail = (f"scope={r['address_scope']} conf={r['address_scope_confidence']} "
                  f"genuine_p={r['genuine_p']} latency={r['latency_ms']}ms")
        check(f"live: {addr[:45]!r} -> {r['decision']} (want {expect})",
              ok, detail)


if __name__ == "__main__":
    live = "--live" in sys.argv
    if live:
        run_live()
    else:
        run_mock()
    fails = [r for r in results if r[0] == FAIL]
    for status, name, detail in results:
        print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    print(f"\n{len(results) - len(fails)}/{len(results)} passed")
    sys.exit(1 if fails else 0)
