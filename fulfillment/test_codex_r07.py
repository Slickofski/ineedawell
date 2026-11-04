#!/usr/bin/env python3
"""Tests for fulfillment/cleanup_local.py (Codex finding R07, 2026-10-21).

R07: the pilot runbook's cleanup step pointed at scrub_pii.py, which was
retired and refuses unconditionally, and its canonical replacement covers
the worker repo's UUID/attempt-tree layout — not this tree's local layout
(orders/<order_ref>/ + outbox/<order_ref>/). cleanup_local.py is the
replacement for THIS layout.

These tests build a SYNTHETIC order + outbox tree under a temp dir
(mirroring the real artifact names/shapes found in orders/ and outbox/)
and never touch a real order_ref.

Forced-failure note: the suite runs as root on this VM, and root ignores
file mode bits, so a chmod read-only file is NOT a reliable failure.
The failure test therefore uses a corrupt (unparseable) .json artifact,
which must fail deterministically on any user.

Run: python3 -m unittest test_codex_r07 -v  (from fulfillment/)
"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, ".")

import cleanup_local  # noqa: E402

REF = "ORD-TEST-r07-123-main-st"
FULL_ADDRESS = "123 Main St, East Windsor, CT 06088"
STREET_SEGMENT = "123 Main St"
DISPLAY_NAME = ("123, Main Street, Scantic, East Windsor, Capitol Planning "
                "Region, Connecticut, 06088, United States")
LAT, LON = 41.9107955, -72.5748722
EMAIL = "customer@example.com"


def build_layout(base: Path, corrupt_json: bool = False,
                 structured_geocode: bool = False) -> Path:
    """Create a synthetic orders/<ref> + outbox/<ref> tree under base."""
    order_dir = base / "orders" / REF
    outbox_dir = base / "outbox" / REF
    order_dir.mkdir(parents=True)
    outbox_dir.mkdir(parents=True)

    (order_dir / "order.json").write_text(json.dumps({
        "order_id": REF,
        "tier": "free",
        "customer": {"name": "Test Customer", "email": EMAIL, "phone": ""},
        "property_address": FULL_ADDRESS,
        "lat": LAT,
        "lon": LON,
        "status": "intake_complete",
    }, indent=2))
    geo = {
        "lat": LAT, "lon": LON, "source": "nominatim",
        "display_name": DISPLAY_NAME, "town": "East Windsor",
    }
    coords = {"lat": LAT, "lon": LON,
              "display_name": DISPLAY_NAME,
              "source": "nominatim"}
    if structured_geocode:
        # The current geocoder's stored shape (Codex PR #5, Finding 2):
        # house number and road as SEPARATE structured fields. Neither
        # contains the full street string.
        geo.update({"house_number": "123", "road": "Main Street"})
        coords.update({"house_number": "123", "road": "Main Street"})
    (order_dir / "geocode.json").write_text(json.dumps(geo, indent=2))
    screen = {
        "order_id": REF,
        "property_address": FULL_ADDRESS,
        "coordinates": coords,
        "verdict": {"headline": "Workable"},
        # Public CT DCP records for OTHER parcels — not customer PII,
        # must survive redaction untouched.
        "nearby_wells": {"wells": [
            {"address": "9 Other Rd East Windsor", "city": "East Windsor",
             "depth_ft": 300.0}]},
    }
    (order_dir / "screen.json").write_text(
        "{ this is not json" if corrupt_json else json.dumps(screen, indent=2))
    (order_dir / "send_log.json").write_text(json.dumps([{
        "kind": "initial", "to": "driller@example.com",
        "subject": "Well drilling quote request — " + FULL_ADDRESS,
    }], indent=2))
    # No-PII structured file: must be kept byte-for-byte.
    (order_dir / "water_quality.json").write_text(json.dumps(
        {"source": "Water Quality Portal", "arsenic": {"result_rows": 842}},
        indent=2))
    # Rendered artifacts embedding the address: deletion targets.
    (order_dir / "screen.html").write_text(
        "<html><body>Snapshot for " + FULL_ADDRESS + "</body></html>")
    (order_dir / "delivery_email_draft.txt").write_text(
        "Subject: Your well snapshot for " + FULL_ADDRESS + "\n\nHi.\n")
    (order_dir / "packet.md").write_text(
        "# Packet\n\n- **Address:** " + FULL_ADDRESS + "\n")
    # Outbox drafts embedding the address: deletion targets.
    (outbox_dir / "driller_initial.txt").write_text(
        "Quote request for " + FULL_ADDRESS + "\n")
    (outbox_dir / "driller_followup1.txt").write_text(
        "Following up on " + FULL_ADDRESS + "\n")
    return order_dir


def snapshot_tree(base: Path) -> dict:
    snap = {}
    for p in sorted(base.rglob("*")):
        rel = str(p.relative_to(base))
        if p.is_dir():
            snap[rel] = "<dir>"
        else:
            snap[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return snap


def run_cli(base: Path, *extra: str, ref: str = REF) -> int:
    return cleanup_local.main([ref, "--base-dir", str(base), *extra])


class TestCleanupLocalR07(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.order_dir = build_layout(self.base)

    def tearDown(self):
        self._tmp.cleanup()

    def test_plan_mode_changes_nothing(self):
        before = snapshot_tree(self.base)
        rc = run_cli(self.base)
        self.assertEqual(rc, 0)
        self.assertEqual(snapshot_tree(self.base), before)
        self.assertFalse((self.order_dir / "cleanup_receipt.json").exists())

    def test_execute_without_attestation_refuses(self):
        before = snapshot_tree(self.base)
        rc = run_cli(self.base, "--execute")
        self.assertNotEqual(rc, 0)
        self.assertEqual(snapshot_tree(self.base), before)
        self.assertIn(STREET_SEGMENT,
                      (self.order_dir / "order.json").read_text())
        self.assertFalse((self.order_dir / "cleanup_receipt.json").exists())

    def test_execute_with_attestation_cleans_and_receipts(self):
        rc = run_cli(self.base, "--execute", "--delivered-confirmed")
        self.assertEqual(rc, 0)

        order = json.loads((self.order_dir / "order.json").read_text())
        self.assertNotIn(STREET_SEGMENT, order["property_address"])
        self.assertNotIn("123", order["property_address"])
        # Retention rule: email + town + ZIP kept.
        self.assertEqual(order["customer"]["email"], EMAIL)
        self.assertIn("East Windsor", order["property_address"])
        self.assertIn("06088", order["property_address"])
        self.assertEqual(order["lat"], round(LAT, 2))
        self.assertEqual(order["lon"], round(LON, 2))

        geo = json.loads((self.order_dir / "geocode.json").read_text())
        self.assertEqual(geo["lat"], round(LAT, 2))
        self.assertEqual(geo["lon"], round(LON, 2))
        self.assertNotIn("123", geo["display_name"])
        self.assertNotIn("Main", geo["display_name"])
        self.assertEqual(geo["town"], "East Windsor")

        screen = json.loads((self.order_dir / "screen.json").read_text())
        self.assertNotIn(STREET_SEGMENT, screen["property_address"])
        self.assertEqual(screen["coordinates"]["lat"], round(LAT, 2))
        self.assertNotIn("123", screen["coordinates"]["display_name"])
        # Neighbours' public records are not customer PII: untouched.
        self.assertEqual(screen["nearby_wells"]["wells"][0]["address"],
                         "9 Other Rd East Windsor")

        send_log = json.loads((self.order_dir / "send_log.json").read_text())
        self.assertNotIn(STREET_SEGMENT, send_log[0]["subject"])
        self.assertIn("East Windsor", send_log[0]["subject"])

        # No-PII JSON kept.
        self.assertTrue((self.order_dir / "water_quality.json").exists())

        # Rendered artifacts and the whole outbox tree are deleted.
        for name in ("screen.html", "delivery_email_draft.txt", "packet.md"):
            self.assertFalse((self.order_dir / name).exists(), name)
        self.assertFalse((self.base / "outbox" / REF).exists())

        # No file left in the order dir carries the street address.
        for p in self.order_dir.rglob("*"):
            if p.is_file():
                self.assertNotIn(STREET_SEGMENT, p.read_text(errors="replace"),
                                 str(p))
                self.assertNotIn(FULL_ADDRESS, p.read_text(errors="replace"),
                                 str(p))

        # Verified receipt exists and itself carries no street address.
        receipt_path = self.order_dir / "cleanup_receipt.json"
        self.assertTrue(receipt_path.exists())
        receipt_text = receipt_path.read_text()
        self.assertNotIn(STREET_SEGMENT, receipt_text)
        self.assertNotIn(FULL_ADDRESS, receipt_text)
        receipt = json.loads(receipt_text)
        self.assertTrue(receipt["verified"])
        self.assertTrue(receipt["actions"])

    def test_forced_failure_no_receipt(self):
        # Rebuild with a corrupt structured artifact.
        self._tmp.cleanup()
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.order_dir = build_layout(self.base, corrupt_json=True)
        rc = run_cli(self.base, "--execute", "--delivered-confirmed")
        self.assertNotEqual(rc, 0)
        self.assertFalse((self.order_dir / "cleanup_receipt.json").exists())

    def test_failure_midflight_no_receipt_partial_disclosed(self):
        # A delete that blows up mid-execution (patched in, because as
        # root neither chmod nor a corrupt file can force a mid-flight
        # failure): nonzero exit, NO receipt, earlier redactions stand.
        from unittest import mock
        real_delete = cleanup_local.do_delete
        calls = {"n": 0}

        def fail_on_second(action):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("simulated failure")
            return real_delete(action)

        with mock.patch.object(cleanup_local, "do_delete",
                               side_effect=fail_on_second):
            rc = run_cli(self.base, "--execute", "--delivered-confirmed")
        self.assertNotEqual(rc, 0)
        self.assertFalse((self.order_dir / "cleanup_receipt.json").exists())
        # Partial state is real and disclosed, not rolled back or hidden:
        # work before the failure stands, work after it was not done.
        order = json.loads((self.order_dir / "order.json").read_text())
        self.assertNotIn(STREET_SEGMENT, order["property_address"])
        self.assertTrue((self.order_dir / "screen.html").exists())

    def test_unknown_ref_is_not_silent_success(self):
        rc = run_cli(self.base, ref="ORD-DOES-NOT-EXIST")
        self.assertNotEqual(rc, 0)
        rc = run_cli(self.base, "--execute", "--delivered-confirmed",
                     ref="ORD-DOES-NOT-EXIST")
        self.assertNotEqual(rc, 0)

    def test_finding2_structured_geocode_components_removed(self):
        # Codex PR #5 review, Finding 2 (2026-10-22): house_number and
        # road stored as separate geocode fields spell the street
        # address split across two fields; cleanup must remove them
        # (when they match this order's address) before receipting.
        self._tmp.cleanup()
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.order_dir = build_layout(self.base, structured_geocode=True)
        rc = run_cli(self.base, "--execute", "--delivered-confirmed")
        self.assertEqual(rc, 0)

        geo_text = (self.order_dir / "geocode.json").read_text()
        geo = json.loads(geo_text)
        self.assertNotIn("house_number", geo)
        self.assertNotIn("road", geo)
        self.assertNotIn("123", geo_text)
        self.assertNotIn("Main", geo_text)
        # Town/ZIP/source survive; coords rounded as before.
        self.assertEqual(geo["town"], "East Windsor")
        self.assertEqual(geo["lat"], round(LAT, 2))

        # The same structured pair embedded in screen.json's
        # coordinates block is removed too.
        screen = json.loads((self.order_dir / "screen.json").read_text())
        self.assertNotIn("house_number", screen["coordinates"])
        self.assertNotIn("road", screen["coordinates"])

        self.assertTrue(
            (self.order_dir / "cleanup_receipt.json").exists())

    def test_finding3_retry_after_failure_catches_the_rest(self):
        # Codex PR #5 review, Finding 3 (2026-10-22): an execute that
        # fails AFTER order.json is redacted must not let a retry
        # certify the order clean — the retry has to recover the
        # address context and catch what the first run left behind
        # (here: send_log.json and screen.json still carry the full
        # address at retry time).
        from unittest import mock
        real_delete = cleanup_local.do_delete
        calls = {"n": 0}

        def fail_on_second(action):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("simulated failure")
            return real_delete(action)

        with mock.patch.object(cleanup_local, "do_delete",
                               side_effect=fail_on_second):
            rc = run_cli(self.base, "--execute", "--delivered-confirmed")
        self.assertNotEqual(rc, 0)
        self.assertFalse((self.order_dir / "cleanup_receipt.json").exists())
        # The trap is armed: order.json is already redacted (so a
        # context re-derived from it alone has no street), while later
        # artifacts still carry the full address.
        order = json.loads((self.order_dir / "order.json").read_text())
        self.assertNotIn(STREET_SEGMENT, order["property_address"])
        self.assertIn(FULL_ADDRESS,
                      (self.order_dir / "send_log.json").read_text())

        # Retry, unpatched. It must finish the job — not receipt over
        # the leftover address.
        rc = run_cli(self.base, "--execute", "--delivered-confirmed")
        self.assertEqual(rc, 0)
        self.assertTrue(
            (self.order_dir / "cleanup_receipt.json").exists())
        send_log = json.loads((self.order_dir / "send_log.json").read_text())
        self.assertNotIn(STREET_SEGMENT, send_log[0]["subject"])
        self.assertIn("East Windsor", send_log[0]["subject"])
        for p in self.order_dir.rglob("*"):
            if p.is_file():
                self.assertNotIn(STREET_SEGMENT,
                                 p.read_text(errors="replace"), str(p))
                self.assertNotIn(FULL_ADDRESS,
                                 p.read_text(errors="replace"), str(p))

    def test_pr6_state_unlink_failure_no_verified_receipt_coexists(self):
        # Codex review of PR #6 (2026-10-22), the follow-up P2: the
        # verified receipt was written BEFORE the address-bearing
        # cleanup_state.json was deleted, so a failed state deletion
        # left a receipt claiming verified cleanup sitting next to
        # the full street address. Force the state unlink to fail
        # (the suite's patched-failure style; chmod cannot fail as
        # root) after an otherwise-successful execute.
        from unittest import mock
        real_unlink = Path.unlink

        def failing_state_unlink(self, *args, **kwargs):
            if self.name == cleanup_local.STATE_NAME:
                raise PermissionError("simulated state unlink failure")
            return real_unlink(self, *args, **kwargs)

        with mock.patch.object(Path, "unlink", failing_state_unlink):
            rc = run_cli(self.base, "--execute", "--delivered-confirmed")
        self.assertNotEqual(rc, 0)
        state_path = self.order_dir / cleanup_local.STATE_NAME
        receipt_path = self.order_dir / "cleanup_receipt.json"
        # The state file is intact and still address-bearing (the
        # Finding-3 retry path must be able to resume from it) ...
        self.assertTrue(state_path.exists())
        self.assertIn(STREET_SEGMENT, state_path.read_text())
        # ... and NO verified receipt may coexist with it. Stronger
        # form of the invariant: no final receipt at all.
        self.assertFalse(receipt_path.exists(),
                         "verified receipt coexists with the "
                         "address-bearing state file")

    def test_pr6_interrupt_after_state_removal_retry_uses_staged(self):
        # The new ordering's hard edge: interruption AFTER the state
        # file is removed but BEFORE the receipt is published. The
        # retry has no address context left to re-derive (order.json
        # is redacted, state is gone), so it must complete from the
        # STAGED receipt evidence written before the state removal —
        # re-checked against the files on disk — never from a fresh
        # context-free receipt. Simulated by failing the publish
        # step on the first run.
        from unittest import mock
        with mock.patch.object(
                cleanup_local, "_publish_receipt",
                side_effect=OSError("simulated interruption "
                                    "after state removal")):
            rc = run_cli(self.base, "--execute", "--delivered-confirmed")
        self.assertNotEqual(rc, 0)
        state_path = self.order_dir / cleanup_local.STATE_NAME
        staged_path = self.order_dir / cleanup_local.STAGED_NAME
        receipt_path = self.order_dir / "cleanup_receipt.json"
        # Stranded in exactly the designed window: state gone, no
        # final receipt, staged evidence present and address-free.
        self.assertFalse(state_path.exists())
        self.assertFalse(receipt_path.exists())
        self.assertTrue(staged_path.exists())
        staged_text = staged_path.read_text()
        self.assertNotIn(STREET_SEGMENT, staged_text)
        self.assertNotIn(FULL_ADDRESS, staged_text)
        staged = json.loads(staged_text)
        staged_actions = staged["receipt"]["actions"]
        self.assertTrue(staged_actions)

        # Retry, unpatched: honest completion from the staged
        # evidence, with its action record preserved.
        rc = run_cli(self.base, "--execute", "--delivered-confirmed")
        self.assertEqual(rc, 0)
        self.assertTrue(receipt_path.exists())
        receipt = json.loads(receipt_path.read_text())
        self.assertTrue(receipt["verified"])
        self.assertEqual(receipt["actions"], staged_actions)
        # Completed cleanup: no state file, no stray staging file,
        # and no file anywhere carries the street address.
        self.assertFalse(state_path.exists())
        self.assertFalse(staged_path.exists())
        for p in self.order_dir.rglob("*"):
            if p.is_file():
                self.assertNotIn(STREET_SEGMENT,
                                 p.read_text(errors="replace"), str(p))
                self.assertNotIn(FULL_ADDRESS,
                                 p.read_text(errors="replace"), str(p))

    def test_pr6_staged_retry_refuses_when_files_changed(self):
        # Companion to the staged-retry test: the retry re-checks
        # the evidence instead of trusting it blind. If a file
        # changed after the staged record was written, the retry
        # must refuse — no receipt, staged record preserved.
        from unittest import mock
        with mock.patch.object(
                cleanup_local, "_publish_receipt",
                side_effect=OSError("simulated interruption "
                                    "after state removal")):
            rc = run_cli(self.base, "--execute", "--delivered-confirmed")
        self.assertNotEqual(rc, 0)
        staged_path = self.order_dir / cleanup_local.STAGED_NAME
        self.assertTrue(staged_path.exists())
        # Tamper with a file the staged evidence covers.
        wq = self.order_dir / "water_quality.json"
        data = json.loads(wq.read_text())
        data["arsenic"]["result_rows"] = 999999
        wq.write_text(json.dumps(data, indent=2))
        rc = run_cli(self.base, "--execute", "--delivered-confirmed")
        self.assertNotEqual(rc, 0)
        self.assertFalse(
            (self.order_dir / "cleanup_receipt.json").exists())
        self.assertTrue(staged_path.exists())

    def _arm_stranded_staged(self):
        """Arm the stranded state: interrupt after state removal,
        before receipt publication (same patch as the PR #6 tests)."""
        from unittest import mock
        with mock.patch.object(
                cleanup_local, "_publish_receipt",
                side_effect=OSError("simulated interruption "
                                    "after state removal")):
            rc = run_cli(self.base, "--execute", "--delivered-confirmed")
        self.assertNotEqual(rc, 0)
        state_path = self.order_dir / cleanup_local.STATE_NAME
        staged_path = self.order_dir / cleanup_local.STAGED_NAME
        receipt_path = self.order_dir / "cleanup_receipt.json"
        self.assertFalse(state_path.exists())
        self.assertTrue(staged_path.exists())
        self.assertFalse(receipt_path.exists())
        return staged_path, receipt_path

    def _regenerate_artifacts(self):
        """Regenerate artifacts fresh, as a delivery re-run would:
        send_log.json with the original full-address subject and
        screen.json with the full address + precise coordinates
        (same shapes as build_layout)."""
        (self.order_dir / "send_log.json").write_text(json.dumps([{
            "kind": "initial", "to": "driller@example.com",
            "subject": "Well drilling quote request — " + FULL_ADDRESS,
        }], indent=2))
        coords = {"lat": LAT, "lon": LON,
                  "display_name": DISPLAY_NAME,
                  "source": "nominatim"}
        screen = {
            "order_id": REF,
            "property_address": FULL_ADDRESS,
            "coordinates": coords,
            "verdict": {"headline": "Workable"},
            "nearby_wells": {"wells": [
                {"address": "9 Other Rd East Windsor",
                 "city": "East Windsor", "depth_ft": 300.0}]},
        }
        (self.order_dir / "screen.json").write_text(
            json.dumps(screen, indent=2))

    def test_pr7_stale_staged_evidence_refused_without_context(self):
        # Codex review of PR #7, Finding 1: cleanup interrupted after
        # state removal but before receipt publication; artifacts are
        # then REGENERATED carrying the full street address. The retry
        # derives its context from the already-redacted order.json, so
        # ctx.street is empty, old staged evidence mismatches and is
        # discarded, and the run certifies a verified receipt while
        # send_log.json — classified "keep" because string redaction
        # needs a context — still contains the full street address.
        staged_path, receipt_path = self._arm_stranded_staged()
        self._regenerate_artifacts()
        snap = snapshot_tree(self.base)
        staged_bytes = staged_path.read_bytes()
        send_log_bytes = (self.order_dir / "send_log.json").read_bytes()
        screen_bytes = (self.order_dir / "screen.json").read_bytes()

        rc = run_cli(self.base, "--execute", "--delivered-confirmed")
        self.assertNotEqual(rc, 0)
        self.assertFalse(receipt_path.exists())
        self.assertTrue(staged_path.exists())
        self.assertEqual(staged_path.read_bytes(), staged_bytes)
        self.assertEqual(
            (self.order_dir / "send_log.json").read_bytes(),
            send_log_bytes)
        self.assertEqual(
            (self.order_dir / "screen.json").read_bytes(), screen_bytes)
        self.assertEqual(snapshot_tree(self.base), snap)

    def test_pr7_staged_mismatch_with_recovered_context_reverifies(self):
        # Pinning test: same arming + regeneration, but the address
        # context IS recoverable (order.json restored to the full
        # address), so the existing discard-and-re-verify path must
        # still run — the PR #7 guard fires ONLY without context.
        # (This pin may pass on pre-fix code — that is fine.)
        staged_path, receipt_path = self._arm_stranded_staged()
        self._regenerate_artifacts()
        order_path = self.order_dir / "order.json"
        order = json.loads(order_path.read_text())
        order["property_address"] = FULL_ADDRESS
        order_path.write_text(json.dumps(order, indent=2))

        rc = run_cli(self.base, "--execute", "--delivered-confirmed")
        self.assertEqual(rc, 0)
        self.assertTrue(receipt_path.exists())
        send_log = json.loads(
            (self.order_dir / "send_log.json").read_text())
        self.assertNotIn(STREET_SEGMENT, send_log[0]["subject"])
        for p in self.order_dir.rglob("*"):
            if p.is_file():
                self.assertNotIn(STREET_SEGMENT,
                                 p.read_text(errors="replace"), str(p))
                self.assertNotIn(FULL_ADDRESS,
                                 p.read_text(errors="replace"), str(p))
        self.assertFalse(staged_path.exists())

    def test_nothing_to_do_is_not_silent_success(self):
        # Second execute after a successful cleanup: nothing left to do.
        rc = run_cli(self.base, "--execute", "--delivered-confirmed")
        self.assertEqual(rc, 0)
        receipt_before = (self.order_dir / "cleanup_receipt.json").read_bytes()
        rc = run_cli(self.base, "--execute", "--delivered-confirmed")
        self.assertNotEqual(rc, 0)
        self.assertEqual(
            (self.order_dir / "cleanup_receipt.json").read_bytes(),
            receipt_before)
        # And the re-run must not mistake the retained town for a
        # street and strip it (regression: retained form parses back).
        order = json.loads((self.order_dir / "order.json").read_text())
        self.assertEqual(order["property_address"],
                         "East Windsor, CT 06088")


class TestInventoryRefGuardB02Residual(unittest.TestCase):
    """B-02 residual (builder pack 2026-10-29): inventory() joined the
    operator-supplied order_ref into a path without validation, so a
    traversal ref classified files OUTSIDE the base as delete
    candidates at plan stage. inventory() must now validate the ref
    (pipeline/intake.validate_order_ref) and raise BEFORE classifying
    anything; a normal ref still inventories."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.base = root / "base"
        (self.base / "orders").mkdir(parents=True)
        (self.base / "outbox").mkdir(parents=True)
        # A victim folder OUTSIDE the cleanup base — the traversal
        # target. Its rendered artifact would classify as "delete".
        self.victim = root / "victim"
        self.victim.mkdir()
        (self.victim / "order.json").write_text(json.dumps(
            {"property_address": FULL_ADDRESS}))
        (self.victim / "screen.html").write_text("<html>x</html>")

    def tearDown(self):
        self._tmp.cleanup()

    def test_hostile_refs_raise_before_classifying(self):
        for ref in ("../../victim", "/tmp/victim", "..", "a/b",
                    "ORD ok?", ""):
            with self.assertRaises(cleanup_local.CleanupError,
                                   msg=f"ref {ref!r} must raise"):
                cleanup_local.inventory(self.base, ref)

    def test_normal_ref_still_inventories(self):
        order_dir = build_layout(self.base)
        actions, ctx = cleanup_local.inventory(self.base, REF)
        self.assertTrue(actions)
        self.assertEqual(ctx.street, STREET_SEGMENT)
        self.assertTrue(order_dir.is_dir())


if __name__ == "__main__":
    unittest.main()
