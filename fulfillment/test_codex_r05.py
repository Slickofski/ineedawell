"""Codex R05 regression tests — header injection / recipient swap.

Defect covered: outreach interpolated the property address into a
text header block (the Subject line), and mailer.parse_draft accepted
repeated headers, overwriting the driller recipient. A multiline
address containing "\\nTo: attacker@example.invalid\\nSubject: x"
therefore redirected the live send to the attacker and leaked the
customer's email in the body/From line of the draft.

Required behavior: recipient/subject/body travel as structured values
into the mailer; CR/LF in any header-derived value is a hard
rejection; parse_draft rejects duplicate/conflicting headers; and
before any send the final recipient is verified equal to the selected
driller record's email.

Also covers the R02 follow-on scope guard for run_pipeline.py
(run_pipeline.scope_check, mirroring run_screen/run_report).

Synthetic data only. FAKE SMTP only: smtplib.SMTP is replaced with an
in-memory fake — nothing is ever sent anywhere.

Run: python3 -m unittest test_codex_r05 -v  (from fulfillment/)
"""
import inspect
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, ".")
from pipeline import intake, mailer, outreach  # noqa: E402
import run_pipeline  # noqa: E402

DRILLER_EMAIL = "driller@example.com"
ATTACKER = "attacker@example.invalid"
CLEAN_ADDRESS = "183 Chamberlain Rd, East Windsor, CT 06016"
EVIL_ADDRESS = (CLEAN_ADDRESS
                + "\nTo: attacker@example.invalid\nSubject: injected")

CFG = {"host": "smtp.example.invalid", "port": 587,
       "user": "synthetic-user", "password": "synthetic-pass",
       "from_email": "concierge@example.com",
       "from_name": "Well Concierge", "tls": False}


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg):
        self.sent.append(msg)

    @classmethod
    def all_recipients(cls):
        return [str(m["To"]) for inst in cls.instances for m in inst.sent]


def make_order(address=CLEAN_ADDRESS, **cust_over):
    cust = {"name": "Synthetic Customer", "email": "customer@example.com",
            "phone": "(860) 555-1234"}
    cust.update(cust_over)
    return {"order_id": "ORD-SYNTHETIC", "tier": "concierge",
            "customer": cust, "property_address": address}


def make_ranked():
    driller = {"company": "Synthetic Drilling LLC", "slug": "synthetic",
               "email": DRILLER_EMAIL, "channel": "email",
               "website": "https://driller.example.invalid",
               "phone": "(860) 555-9999", "distance_miles": 3.2}
    return {"drillers": [driller]}


GEO = {"lat": 41.90218, "lon": -72.54082, "source": "synthetic"}
WELLS = {}


def extract_initial(item):
    """(to, subject, body) for an outreach manifest item, using the
    structured messages when the pipeline provides them and falling
    back to the legacy draft-file parse otherwise."""
    messages = item.get("messages") or {}
    if "initial" in messages:
        m = messages["initial"]
        return m["to"], m["subject"], m["body"]
    return mailer.parse_draft(item["files"]["initial"])


def send_initial(to, subject, body, order_dir):
    """Send through the mailer the way run_pipeline must: with the
    selected driller's email as the verified expected recipient when
    the mailer supports it."""
    if "expected_recipient" in inspect.signature(
            mailer.send_email).parameters:
        return mailer.send_email(to, subject, body, CFG, order_dir,
                                 "initial", expected_recipient=DRILLER_EMAIL)
    return mailer.send_email(to, subject, body, CFG, order_dir, "initial")


class TestMultilineAddressInjection(unittest.TestCase):
    def setUp(self):
        FakeSMTP.instances = []

    def test_a_outreach_path_never_sends_to_attacker(self):
        # (R05a) Drive outreach -> draft/structured -> fake-SMTP send.
        # Success = the run aborts (ValueError anywhere in the path)
        # OR the only recipient is the selected driller.
        order = make_order(address=EVIL_ADDRESS)
        with tempfile.TemporaryDirectory() as d:
            try:
                manifest = outreach.build_outreach(
                    order, GEO, WELLS, make_ranked(), d)
                to, subject, body = extract_initial(manifest["outreach"][0])
                self.assertEqual(to, DRILLER_EMAIL)
                with patch.object(mailer.smtplib, "SMTP", FakeSMTP):
                    res = send_initial(to, subject, body, d)
                self.assertTrue(res["ok"])
            except ValueError:
                pass  # hard rejection anywhere in the path is a pass
        self.assertNotIn(ATTACKER, FakeSMTP.all_recipients())
        for rcpt in FakeSMTP.all_recipients():
            self.assertEqual(rcpt, DRILLER_EMAIL)

    def test_a2_intake_path_rejects_evil_address(self):
        # (R05a, intake layer) The same address arriving as an order
        # must be rejected at intake, before any outreach exists.
        raw = {"name": "Synthetic Customer",
               "email": "customer@example.com", "phone": "8605551234",
               "property_address": EVIL_ADDRESS, "tier": "concierge"}
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                intake.intake(raw, d)
            self.assertFalse(os.path.exists(os.path.join(d, "order.json")))


class TestCRLFHardRejection(unittest.TestCase):
    def setUp(self):
        FakeSMTP.instances = []

    def test_b1_intake_rejects_crlf_in_name_and_address(self):
        base = {"name": "Synthetic Customer",
                "email": "customer@example.com", "phone": "8605551234",
                "property_address": CLEAN_ADDRESS, "tier": "concierge"}
        for over in ({"property_address": "183 Chamberlain Rd\r\nTo: x@y.z"},
                     {"name": "Evil\nTo: attacker@example.invalid"},
                     {"name": "Evil\rCustomer"}):
            raw = dict(base, **over)
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaises(ValueError, msg=str(over)):
                    intake.intake(raw, d)
                self.assertFalse(
                    os.path.exists(os.path.join(d, "order.json")), str(over))

    def test_b2_parse_draft_rejects_duplicate_headers(self):
        draft = (
            "DRAFT — NOT SENT. synthetic\n" + "-" * 70 + "\n"
            f"To: {DRILLER_EMAIL}\n"
            "From: Synthetic Customer <customer@example.com>\n"
            f"Subject: Well drilling quote request — {CLEAN_ADDRESS}\n"
            f"To: {ATTACKER}\n"
            "Subject: injected\n"
            "\nBody text\n"
        )
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "draft.txt")
            with open(path, "w") as f:
                f.write(draft)
            with self.assertRaises(ValueError):
                mailer.parse_draft(path)

    def test_b3_send_email_rejects_crlf_recipient(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(mailer.smtplib, "SMTP", FakeSMTP):
                with self.assertRaises(ValueError):
                    mailer.send_email(
                        f"{DRILLER_EMAIL}\r\nTo: {ATTACKER}",
                        "Quote request", "body", CFG, d, "initial")
        self.assertEqual(FakeSMTP.all_recipients(), [])
        self.assertEqual(FakeSMTP.instances, [])

    def test_b4_send_email_rejects_crlf_subject(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(mailer.smtplib, "SMTP", FakeSMTP):
                with self.assertRaises(ValueError):
                    mailer.send_email(
                        DRILLER_EMAIL,
                        f"Quote request\nTo: {ATTACKER}",
                        "body", CFG, d, "initial")
        self.assertEqual(FakeSMTP.all_recipients(), [])
        self.assertEqual(FakeSMTP.instances, [])


class TestRecipientVerification(unittest.TestCase):
    def setUp(self):
        FakeSMTP.instances = []

    def test_c_recipient_mismatch_aborts_before_send(self):
        # (R05c) The final recipient must equal the selected driller
        # record's email; a mismatch aborts before any SMTP activity.
        self.assertIn("expected_recipient",
                      inspect.signature(mailer.send_email).parameters,
                      "send_email must support recipient verification")
        with tempfile.TemporaryDirectory() as d:
            with patch.object(mailer.smtplib, "SMTP", FakeSMTP):
                with self.assertRaises(ValueError):
                    mailer.send_email(
                        ATTACKER, "Quote request", "body", CFG, d,
                        "initial", expected_recipient=DRILLER_EMAIL)
        self.assertEqual(FakeSMTP.instances, [])
        self.assertEqual(FakeSMTP.all_recipients(), [])


class TestCleanFlowStillWorks(unittest.TestCase):
    def setUp(self):
        FakeSMTP.instances = []

    def test_d_clean_order_flows_through_fake_smtp(self):
        # (R05d) Guard against over-blocking: a clean synthetic order
        # still produces a draft and a successful fake-SMTP send to
        # exactly the selected driller.
        order = make_order()
        with tempfile.TemporaryDirectory() as d:
            manifest = outreach.build_outreach(
                order, GEO, WELLS, make_ranked(), d)
            item = manifest["outreach"][0]
            to, subject, body = extract_initial(item)
            self.assertEqual(to, DRILLER_EMAIL)
            self.assertIn("Well drilling quote request", subject)
            self.assertIn(CLEAN_ADDRESS, body)
            with patch.object(mailer.smtplib, "SMTP", FakeSMTP):
                res = send_initial(to, subject, body, d)
            self.assertTrue(res["ok"])
            with open(os.path.join(d, "send_log.json")) as f:
                log = json.load(f)
            self.assertEqual(log[-1]["to"], DRILLER_EMAIL)
        self.assertEqual(FakeSMTP.all_recipients(), [DRILLER_EMAIL])


class TestRunPipelineScopeGuard(unittest.TestCase):
    """R02 follow-on: run_pipeline must carry the same structured
    scope guard as run_screen/run_report."""

    VERIFIED = {"lat": 41.9021838, "lon": -72.5408222, "source": "nominatim",
                "display_name": "183, Chamberlain Road, East Windsor, "
                                "Connecticut, 06016, United States",
                "town": "East Windsor", "state": "Connecticut",
                "state_code": "CT", "country_code": "us",
                "house_number": "183", "road": "Chamberlain Road",
                "zip": "06016", "precision": "house"}

    def test_scope_check_exists(self):
        self.assertTrue(hasattr(run_pipeline, "scope_check"),
                        "run_pipeline.scope_check is missing")

    def test_scope_check_rejects_boston_geo_with_ct_text(self):
        legacy_geo = {"lat": 42.3601, "lon": -71.0589,
                      "source": "order_supplied",
                      "display_name": "Boston, Massachusetts, United States",
                      "town": "Boston"}
        reason = run_pipeline.scope_check(
            legacy_geo, "100 Main St, Hartford, CT 06103")
        self.assertIsNotNone(reason)

    def test_scope_check_accepts_verified_geo(self):
        self.assertIsNone(run_pipeline.scope_check(
            dict(self.VERIFIED), CLEAN_ADDRESS))


if __name__ == "__main__":
    unittest.main()
