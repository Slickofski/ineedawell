"""Codex R04 regression tests — intake payment/test state integrity.

Defects covered:
  - intake forcibly cleared an explicit test=true whenever paid was
    truthy, making a test order live-send eligible;
  - paid="false" (the string) was bool()-ed to True;
  - unknown-tier and invalid-amount errors were appended AFTER the
    only validation check, so malformed input was silently normalized
    and written instead of rejected.

Required behavior: strict boolean types (ambiguous values are errors),
test=true is a permanent send block that paid can never clear, ALL
validation errors are collected and reported BEFORE anything is
written, and the mailer's missing-payment-id guard keeps working.

Synthetic data only. No network, no SMTP (gate checks only consult
the environment, which is patched with fake values).

Run: python3 -m unittest test_codex_r04 -v  (from fulfillment/)
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, ".")
from pipeline import intake, mailer  # noqa: E402

SMTP_ENV = {
    "CONCIERGE_SMTP_HOST": "smtp.example.invalid",
    "CONCIERGE_SMTP_PORT": "587",
    "CONCIERGE_SMTP_USER": "synthetic-user",
    "CONCIERGE_SMTP_PASS": "synthetic-pass",
    "CONCIERGE_FROM_EMAIL": "concierge@example.com",
}


def base_raw(**over):
    raw = {
        "name": "Synthetic Customer",
        "email": "customer@example.com",
        "phone": "8605551234",
        "property_address": "183 Chamberlain Rd, East Windsor, CT 06016",
        "tier": "concierge",
    }
    raw.update(over)
    return raw


def run_intake(raw):
    """Run intake in a fresh temp order dir; return (order, order_dir).

    The TemporaryDirectory is kept alive by the caller via the returned
    path only inside this helper's context — so this helper returns the
    parsed order plus whether order.json exists, then cleans up.
    """
    with tempfile.TemporaryDirectory() as d:
        order = intake.intake(raw, d)
        written = os.path.exists(os.path.join(d, "order.json"))
        on_disk = None
        if written:
            with open(os.path.join(d, "order.json")) as f:
                on_disk = json.load(f)
        return order, written, on_disk


class TestTestFlagIsPermanent(unittest.TestCase):
    def test_a_paid_plus_test_stays_test_and_blocked(self):
        # (R04a) paid=true + test=true + a payment id: the test flag
        # must survive intake intact and the mailer must still refuse.
        raw = base_raw(paid=True, test=True,
                       stripe_payment_id="pi_synthetic_123")
        order, written, on_disk = run_intake(raw)
        self.assertTrue(written)
        self.assertIs(order["test"], True)
        self.assertIs(order["paid"], True)
        self.assertIs(on_disk["test"], True)
        with patch.dict(os.environ, SMTP_ENV):
            allowed, reason = mailer.gate(order, live_flag=True)
        self.assertFalse(allowed)
        self.assertIn("test", reason)

    def test_gate_blocks_nonboolean_test_flag(self):
        # Defense in depth: a hand-built order whose test flag is the
        # string "false" must never be live-eligible either.
        order = {"paid": True, "test": "false",
                 "stripe_payment_id": "pi_synthetic_123"}
        with patch.dict(os.environ, SMTP_ENV):
            allowed, _reason = mailer.gate(order, live_flag=True)
        self.assertFalse(allowed)


class TestStrictBooleans(unittest.TestCase):
    def test_b_paid_string_false_is_type_error_never_live(self):
        # (R04b) paid="false" must be rejected as a type error and
        # nothing may be written — bool("false") is True in the buggy
        # code, which made the order live-eligible.
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                intake.intake(base_raw(paid="false"), d)
            self.assertFalse(os.path.exists(os.path.join(d, "order.json")))

    def test_b2_paid_and_test_string_variants_rejected(self):
        for over in ({"paid": "true"}, {"test": "false"}, {"paid": 1},
                     {"test": 0}):
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaises(ValueError, msg=str(over)):
                    intake.intake(base_raw(**over), d)
                self.assertFalse(
                    os.path.exists(os.path.join(d, "order.json")), str(over))


class TestAllErrorsBeforeWriting(unittest.TestCase):
    def test_c_unknown_tier_rejected_nothing_written(self):
        # (R04c)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError) as ctx:
                intake.intake(base_raw(tier="platinum"), d)
            self.assertIn("tier", str(ctx.exception))
            self.assertFalse(os.path.exists(os.path.join(d, "order.json")))

    def test_d_invalid_amount_rejected_nothing_written(self):
        # (R04d)
        for bad in ("not-a-number", -10, 39.5, True):
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaises(ValueError, msg=repr(bad)):
                    intake.intake(base_raw(amount_usd=bad), d)
                self.assertFalse(
                    os.path.exists(os.path.join(d, "order.json")), repr(bad))

    def test_e_multiple_errors_all_reported_at_once(self):
        # (R04e) Unknown tier + invalid amount + non-boolean paid must
        # all appear in one rejection; nothing is written.
        raw = base_raw(tier="platinum", amount_usd="not-a-number",
                       paid="false")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError) as ctx:
                intake.intake(raw, d)
            msg = str(ctx.exception)
            self.assertIn("tier", msg)
            self.assertIn("amount", msg)
            self.assertIn("paid", msg)
            self.assertFalse(os.path.exists(os.path.join(d, "order.json")))


class TestPaymentGuardPreserved(unittest.TestCase):
    def test_f_missing_payment_id_mailer_still_refuses(self):
        # (R04f) The mailer guard that already worked must keep
        # working: paid + not test + NO payment id -> refused.
        order = {"paid": True, "test": False, "stripe_payment_id": None}
        with patch.dict(os.environ, SMTP_ENV):
            allowed, reason = mailer.gate(order, live_flag=True)
        self.assertFalse(allowed)
        self.assertIn("stripe_payment_id", reason)

    def test_f2_paid_without_payment_id_rejected_at_intake(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError) as ctx:
                intake.intake(base_raw(paid=True, test=False), d)
            self.assertIn("stripe_payment_id", str(ctx.exception))
            self.assertFalse(os.path.exists(os.path.join(d, "order.json")))


class TestCleanOrdersStillFlow(unittest.TestCase):
    def test_clean_paid_order_passes_gate(self):
        # Guard against over-blocking: a genuine paid, non-test order
        # with a payment id is live-eligible when SMTP is configured.
        raw = base_raw(paid=True, test=False,
                       stripe_payment_id="pi_synthetic_123")
        order, written, _ = run_intake(raw)
        self.assertTrue(written)
        self.assertIs(order["test"], False)
        self.assertEqual(order["amount_usd"], 100)
        with patch.dict(os.environ, SMTP_ENV):
            allowed, _reason = mailer.gate(order, live_flag=True)
        self.assertTrue(allowed)

    def test_default_hand_order_stays_test_unpaid(self):
        order, _, _ = run_intake(base_raw())
        self.assertIs(order["test"], True)
        self.assertIs(order["paid"], False)

    def test_free_tier_defaults_still_work(self):
        raw = {"email": "customer@example.com",
               "property_address":
                   "183 Chamberlain Rd, East Windsor, CT 06016",
               "tier": "free", "amount_usd": 0}
        order, _, _ = run_intake(raw)
        self.assertEqual(order["tier"], "free")
        self.assertEqual(order["amount_usd"], 0)


if __name__ == "__main__":
    unittest.main()
