#!/usr/bin/env python3
"""Local-layout PII cleanup for the fulfillment tree (Codex R07 fix).

WHY THIS EXISTS
---------------
The pilot runbook's cleanup step used to name scrub_pii.py. That script
was retired 2026-10-21 (it refuses unconditionally — its old version was
non-atomic and touched zero local files). Its canonical replacement —
the worker repo's scrub_pii.py + worker/operations.py and the database
functions well_scrub / well_retire_undelivered — covers the worker's
UUID-contained attempt trees and the Supabase rows. It does NOT cover
THIS tree's local layout:

    orders/<order_ref>/    order record + stage artifacts
    outbox/<order_ref>/    per-driller draft emails (initial + follow-ups)

This script is the cleanup path for exactly that local layout. It never
touches the database and never sends anything.

RETENTION RULE (pilot)
----------------------
The street address is removed. The customer email, town, and ZIP are
retained (the record stays useful for the pilot tracker).

PER-FILE-TYPE ACTIONS (orders/<order_ref>/)
-------------------------------------------
- Structured files (*.json) are REDACTED IN PLACE:
  * `property_address` values are replaced with the retained form
    "<Town>, <ST> <ZIP>" (the street segment before the first comma is
    dropped);
  * `display_name` values that belong to the customer's geocode result
    (they start with the street number) are replaced the same way;
  * the customer's STRUCTURED geocode components are removed: a dict
    carrying `house_number` / `road` keys that match this order's
    street number and road name (the geocoder's geocode.json shape —
    also embedded in screen.json's coordinates block) has those keys
    dropped. Split across two fields they still spell the street
    address, so neither may survive (Codex PR #5 review, Finding 2);
  * any other string that contains the full address or the exact street
    segment (e.g. a send_log subject line) has that substring replaced;
  * customer coordinates (`lat`/`lon`/`latitude`/`longitude` outside
    third-party business records — dicts carrying a `company` key) are
    rounded to 2 decimal places (~1 km). Neighbouring wells' public
    CT DCP records (their own addresses/coords) are NOT customer PII
    and are preserved; only strings matching THIS order's address are
    redacted. Everything else in the record is preserved.
  * A .json file with no address hit and no unrounded customer
    coordinates is KEPT byte-for-byte.
- Rendered artifacts (*.html, *.htm, *.md, *.txt) are DELETED. They
  embed the address in prose, subjects, and map links and cannot be
  reliably redacted in place; they are delivery copies, not the record.
- Any other file type, any subdirectory, and any symlink inside the
  order folder is UNKNOWN: execute refuses loudly rather than guessing.

OUTBOX
------
Every file under outbox/<order_ref>/ is DELETED and the folder removed.
These are pre-send drafts; the sent copy lives in Gmail (see below).

WHAT THIS DOES NOT COVER (no implied coverage)
----------------------------------------------
- Gmail: the sent delivery email and the request thread in the pilot
  inbox are outside local reach — handle them in Gmail.
- The Supabase database: rows are scrubbed through the worker repo /
  DB path (well_scrub / well_retire_undelivered), not here.
- Google Forms responses and the pilot tracker sheet, if any: separate
  surfaces, cleaned separately.
Run this only for the local copies, and attest delivery (below) only
when the delivery actually happened.

DISCIPLINE
----------
- Plan mode is the default: full inventory, exact per-file actions,
  nothing changes.
- Execute requires BOTH --execute AND --delivered-confirmed (cleanup
  only after confirmed delivery, matching the pilot flow). Execute
  without the attestation refuses and changes nothing.
- Each action is performed, then VERIFIED by re-reading (address
  string absent / coordinates rounded / file gone). The cleanup
  receipt (orders/<order_ref>/cleanup_receipt.json — deliberately
  free of the street address; the containing folder identifies the
  order) is published ONLY after every verification passes AND the
  address-bearing state file (below) has been removed, so a
  verified receipt never coexists with it (Codex PR #6 review).
  Publication is a two-step: the verified receipt is first STAGED
  at orders/<order_ref>/cleanup_receipt.staged.json together with
  evidence of the verification (SHA-256 of every remaining order
  file, outbox confirmed absent); only then is the state file
  deleted and the staged receipt published at its final name.
  If the run is interrupted after the state file is removed but
  before publication, the staged record remains: a retry completes
  certification FROM that evidence — re-checking that the files on
  disk still hash to exactly what was verified, that no rendered
  artifact or unrounded coordinates are present, and that the
  outbox is still absent — never from a fresh context-free receipt.
- Execute PRESERVES the address context before touching anything:
  orders/<order_ref>/cleanup_state.json holds the parsed context
  (it contains the street address — it is a work file, not a
  record). If an execute run is interrupted after order.json has
  been redacted, the next run restores the context from that file
  instead of re-deriving it from the (already-redacted) order.json —
  a retry can never certify files clean merely because it forgot
  the address (Codex PR #5 review, Finding 3). The state file is
  deleted BEFORE the final receipt is published; a leftover state
  file means the cleanup is INCOMPLETE, and no verified receipt
  exists alongside it.
- Any failure: nonzero exit, loud error listing exactly what was and
  was not done, and NO receipt. Partial state is disclosed, never
  hidden; already-redacted files stay redacted (redaction is safe to
  leave in place; deletion is not rolled back either — re-run plan to
  see the remaining work).
- Unknown order_ref, missing folders, or nothing to do: clear message,
  nonzero exit — never silent success.

RESIDUAL (honest limit): the order_ref / order_id slug itself encodes
the street (e.g. ...-556-simsbury-rd) and is the folder name and record
key everywhere (tracker, manifests). This script does not rename refs;
the full street address string is what gets removed.

Usage:
    python3 cleanup_local.py <order_ref>                      # plan
    python3 cleanup_local.py <order_ref> --execute --delivered-confirmed
    python3 cleanup_local.py <order_ref> --base-dir <dir>     # tests
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import intake  # noqa: E402

COORD_DECIMALS = 2  # ~1 km
REDACTED_STREET = "[street address removed]"
RECEIPT_NAME = "cleanup_receipt.json"
STAGED_NAME = "cleanup_receipt.staged.json"
STATE_NAME = "cleanup_state.json"
RENDERED_SUFFIXES = {".html", ".htm", ".md", ".txt"}
COORD_KEYS = {"lat", "lon", "latitude", "longitude"}

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_NOTHING = 3
EXIT_FAILED = 4

NOT_COVERED = [
    "Gmail sent copies and the pilot-inbox request thread (outside local reach)",
    "Supabase database rows (use the worker repo / well_scrub DB path)",
    "Google Forms responses and the pilot tracker (separate surfaces)",
]


class CleanupError(Exception):
    """A step failed; the caller reports partial state and exits nonzero."""


_STREET_SUFFIXES = {
    "st", "street", "rd", "road", "ave", "avenue", "blvd", "boulevard",
    "dr", "drive", "ln", "lane", "ct", "court", "cir", "circle", "way",
    "hwy", "highway", "pike", "turnpike", "trl", "trail", "pl", "place",
    "ter", "terrace", "sq", "square", "ext", "aly", "alley", "xing",
}


def _looks_like_street(segment):
    """A street segment starts with a house number or names a road type.
    A retained town form ("East Windsor, CT 06088") does neither — that
    distinction keeps a re-run from mistaking the retained town for a
    street and stripping it."""
    if not segment:
        return False
    tokens = [t.strip(".").lower() for t in segment.replace(",", " ").split()]
    if tokens and tokens[0][:1].isdigit():
        return True
    return any(t in _STREET_SUFFIXES for t in tokens)


_ROAD_SUFFIXES = {
    "st": "street", "street": "street", "rd": "road", "road": "road",
    "ave": "avenue", "avenue": "avenue", "ln": "lane", "lane": "lane",
    "dr": "drive", "drive": "drive", "ct": "court", "court": "court",
    "cir": "circle", "circle": "circle", "blvd": "boulevard",
    "boulevard": "boulevard", "ter": "terrace", "terrace": "terrace",
    "pl": "place", "place": "place", "trl": "trail", "trail": "trail",
    "hwy": "highway", "highway": "highway", "aly": "alley",
    "alley": "alley", "sq": "square", "square": "square",
    "xing": "crossing", "crossing": "crossing", "ext": "extension",
    "extension": "extension", "pike": "pike", "turnpike": "turnpike",
    "way": "way",
}


def _normalize_road(name):
    """Canonical road name for comparison: casefolded, punctuation
    stripped, trailing road-type suffix expanded — so the order's
    'Main St' and the geocoder's 'Main Street' compare equal."""
    tokens = [t.strip(".") for t in (name or "").casefold().replace(
        ",", " ").split()]
    if tokens and tokens[-1] in _ROAD_SUFFIXES:
        tokens[-1] = _ROAD_SUFFIXES[tokens[-1]]
    return " ".join(tokens)


class AddressContext:
    """The customer's address, parsed from order.json's property_address."""

    def __init__(self, full):
        raw = (full or "").strip()
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        street = parts[0] if parts else ""
        if street and _looks_like_street(street):
            self.full = raw
            self.street = street
            self.retained = (", ".join(parts[1:]) if len(parts) > 1
                             else "[REDACTED]")
            self.street_number = street.split()[0]
            # Road name without the house number, for matching the
            # geocoder's separate `road` field (Finding 2).
            self.road_name = _normalize_road(
                street[len(self.street_number):])
        else:
            # No street segment: the address is already in retained
            # (town/ZIP) form, or absent. Nothing to hunt by string;
            # the retained form is the value itself.
            self.full = ""
            self.street = ""
            self.street_number = ""
            self.road_name = ""
            self.retained = raw

    def hits(self, text):
        if not text:
            return False
        return bool((self.full and self.full in text)
                    or (self.street and self.street in text))


def _is_business_record(d):
    return isinstance(d, dict) and "company" in d


def _display_is_customers(value, ctx):
    """A geocode display_name for THIS parcel starts with its street number."""
    if not isinstance(value, str) or not ctx.street_number:
        return False
    first = value.split(",")[0].strip()
    return first == ctx.street_number or (ctx.street and ctx.street in value)


def _redact_geocode_components(d, ctx):
    """Drop the customer's structured geocode components (Finding 2).

    A dict carrying `house_number` / `road` keys for THIS parcel —
    the geocoder's shape — spells the street address split across
    two fields, invisible to full-string matching. A component is
    dropped only when it matches the context; when both fields are
    present they must corroborate each other, so a coincidental
    same-number field on a different road is left alone. Returns
    (new_dict, changed)."""
    if not ctx.street_number:
        return d, False
    hn = d.get("house_number")
    road = d.get("road")
    hn_match = hn is not None and str(hn).strip() == ctx.street_number
    road_match = (road is not None
                  and _normalize_road(str(road)) == ctx.road_name
                  and bool(ctx.road_name))
    drop = []
    if hn_match and (road is None or road_match):
        drop.append("house_number")
    if road_match and (hn is None or hn_match):
        drop.append("road")
    if not drop:
        return d, False
    return {k: v for k, v in d.items() if k not in drop}, True


def redact_value(obj, ctx, in_business=False):
    """Return (new_obj, changed). Redact the customer's address/coords only."""
    if isinstance(obj, dict):
        business = in_business or _is_business_record(obj)
        changed = False
        if not business:
            obj, changed = _redact_geocode_components(obj, ctx)
        out = {}
        for key, val in obj.items():
            if key == "property_address" and isinstance(val, str):
                new = ctx.retained if val.strip() != ctx.retained else val
                out[key] = new
                changed = changed or new != val
            elif key == "display_name" and _display_is_customers(val, ctx):
                out[key] = ctx.retained
                changed = True
            elif (key in COORD_KEYS and isinstance(val, (int, float))
                  and not isinstance(val, bool) and not business):
                new = round(float(val), COORD_DECIMALS)
                out[key] = new
                changed = changed or new != val
            else:
                new, sub = redact_value(val, ctx, business)
                out[key] = new
                changed = changed or sub
        return out, changed
    if isinstance(obj, list):
        changed = False
        out = []
        for item in obj:
            new, sub = redact_value(item, ctx, in_business)
            out.append(new)
            changed = changed or sub
        return out, changed
    if isinstance(obj, str):
        if ctx.full and ctx.full in obj:
            return obj.replace(ctx.full, ctx.retained), True
        if ctx.street and ctx.street in obj:
            return obj.replace(ctx.street, REDACTED_STREET), True
    return obj, False


def has_unrounded_customer_coords(obj, in_business=False):
    if isinstance(obj, dict):
        business = in_business or _is_business_record(obj)
        for key, val in obj.items():
            if (key in COORD_KEYS and isinstance(val, (int, float))
                    and not isinstance(val, bool) and not business
                    and round(float(val), COORD_DECIMALS) != float(val)):
                return True
            if has_unrounded_customer_coords(val, business):
                return True
    elif isinstance(obj, list):
        return any(has_unrounded_customer_coords(v, in_business) for v in obj)
    return False


def find_address_context(order_dir):
    order_json = order_dir / "order.json"
    if order_json.is_file():
        try:
            data = json.loads(order_json.read_text(errors="replace"))
        except (OSError, ValueError):
            return AddressContext("")
        if isinstance(data, dict):
            return AddressContext(data.get("property_address") or "")
    return AddressContext("")


def inventory(base_dir, order_ref):
    """Classify every artifact for order_ref. Raises CleanupError on
    unknown ref / unsafe entries. Returns (actions, ctx) where each
    action is a dict: path (Path), rel, kind, reason."""
    # B-02 residual: the ref becomes a path below — validate it BEFORE
    # any classification, with the same validator intake enforces
    # (Codex R06). A traversal or absolute ref must never reach the
    # joins: an absolute component discards the base entirely and a
    # '../' ref classifies files outside it as delete candidates.
    try:
        order_ref = intake.validate_order_ref(order_ref)
    except ValueError as exc:
        raise CleanupError(str(exc)) from exc
    base = Path(base_dir)
    order_dir = base / "orders" / order_ref
    outbox_dir = base / "outbox" / order_ref
    if order_dir.is_symlink() or outbox_dir.is_symlink():
        raise CleanupError("order/outbox folder is a symlink — refusing")
    if not order_dir.is_dir() and not outbox_dir.is_dir():
        raise CleanupError(
            f"unknown order_ref {order_ref!r}: no orders/{order_ref}/ "
            f"or outbox/{order_ref}/ folder under {base}")
    ctx = find_address_context(order_dir)
    if not ctx.street:
        # Finding 3: order.json may already be redacted by an
        # interrupted execute — recover the context it saved.
        saved = _load_context(order_dir)
        if saved is not None:
            ctx = saved
    actions = []

    if order_dir.is_dir():
        for entry in sorted(order_dir.iterdir(), key=lambda p: p.name):
            rel = f"orders/{order_ref}/{entry.name}"
            if entry.name == RECEIPT_NAME:
                actions.append({"path": entry, "rel": rel, "kind": "keep",
                                "reason": "existing cleanup receipt"})
                continue
            if entry.name == STATE_NAME:
                actions.append({"path": entry, "rel": rel, "kind": "keep",
                                "reason": "cleanup state from an "
                                          "interrupted execute — "
                                          "preserved address context; "
                                          "removed before the final "
                                          "receipt is published"})
                continue
            if entry.name == STAGED_NAME:
                actions.append({"path": entry, "rel": rel, "kind": "keep",
                                "reason": "staged receipt from an "
                                          "interrupted execute — "
                                          "verification evidence, NOT "
                                          "a verified receipt; consumed "
                                          "when the final receipt is "
                                          "published"})
                continue
            if entry.is_symlink():
                actions.append({"path": entry, "rel": rel, "kind": "blocked",
                                "reason": "symlink — refusing to guess"})
                continue
            if entry.is_dir():
                actions.append({"path": entry, "rel": rel, "kind": "blocked",
                                "reason": "unexpected subdirectory — "
                                          "refusing to guess"})
                continue
            suffix = entry.suffix.lower()
            if suffix == ".json":
                try:
                    data = json.loads(entry.read_text(errors="replace"))
                except ValueError:
                    actions.append({"path": entry, "rel": rel,
                                    "kind": "blocked",
                                    "reason": "unparseable JSON — cannot "
                                              "redact or verify safely"})
                    continue
                _, changed = redact_value(data, ctx)
                if changed:
                    actions.append({"path": entry, "rel": rel,
                                    "kind": "redact",
                                    "reason": "structured record carries "
                                              "the street address and/or "
                                              "precise coordinates"})
                else:
                    actions.append({"path": entry, "rel": rel, "kind": "keep",
                                    "reason": "no street address, no "
                                              "unrounded customer coordinates"})
            elif suffix in RENDERED_SUFFIXES:
                actions.append({"path": entry, "rel": rel, "kind": "delete",
                                "reason": "rendered delivery artifact — "
                                          "embeds the address in prose/links; "
                                          "deleted, not redacted"})
            else:
                actions.append({"path": entry, "rel": rel,
                                "kind": "blocked",
                                "reason": "unknown file type "
                                          f"{suffix or '(none)'} - "
                                          "refusing to guess"})
    if outbox_dir.is_dir():
        for entry in sorted(outbox_dir.rglob("*"),
                            key=lambda p: str(p)):
            if entry.is_dir():
                continue
            rel = f"outbox/{order_ref}/" + str(
                entry.relative_to(outbox_dir))
            kind = "blocked" if entry.is_symlink() else "delete_outbox"
            reason = ("symlink - refusing to guess" if kind == "blocked"
                      else "pre-send draft - deleted; the sent copy "
                           "lives in Gmail, outside local reach")
            actions.append({"path": entry, "rel": rel, "kind": kind,
                            "reason": reason})
    return actions, ctx


def print_plan(actions, order_ref, execute):
    mode = "EXECUTE" if execute else "PLAN (no changes)"
    print(f"cleanup_local - {mode} - order_ref: {order_ref}")
    for a in actions:
        print(f"  [{a['kind'].upper():12}] {a['rel']} - {a['reason']}")
    print("  NOT COVERED by this script:")
    for item in NOT_COVERED:
        print(f"    - {item}")


def _atomic_write_json(path, data):
    tmp = path.with_name(path.name + ".tmp-cleanup")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def do_redact(action, ctx):
    path = action["path"]
    data = json.loads(path.read_text(errors="replace"))
    redacted, changed = redact_value(data, ctx)
    if not changed:
        raise CleanupError(f"{action['rel']}: inventory said redact "
                           "but nothing changed on re-read")
    _atomic_write_json(path, redacted)
    # Verify by re-reading from disk.
    after_text = path.read_text(errors="replace")
    after = json.loads(after_text)
    if ctx.hits(after_text):
        raise CleanupError(f"{action['rel']}: street address still "
                           "present after redaction")
    # Structured leftovers (e.g. geocode components) are invisible
    # to the string check: if a second redaction pass would still
    # change anything, the file is not clean.
    _, still_changed = redact_value(after, ctx)
    if still_changed:
        raise CleanupError(f"{action['rel']}: address material still "
                           "present after redaction")
    if has_unrounded_customer_coords(after):
        raise CleanupError(f"{action['rel']}: unrounded customer "
                           "coordinates remain after redaction")


def _save_context(order_dir, ctx):
    """Persist the address context next to the order (Finding 3).

    Written BEFORE any artifact is touched, so an interrupted
    execute never loses the only copy of what it is hunting."""
    _atomic_write_json(order_dir / STATE_NAME, {
        "full": ctx.full, "street": ctx.street,
        "retained": ctx.retained, "street_number": ctx.street_number,
    })


def _load_context(order_dir):
    """Restore a context saved by an interrupted execute, or None.
    A corrupt state file is a loud error, never a silent skip."""
    state = order_dir / STATE_NAME
    if not state.is_file():
        return None
    try:
        data = json.loads(state.read_text(errors="replace"))
    except (OSError, ValueError) as exc:
        raise CleanupError(f"saved cleanup state is unreadable: {exc}")
    if not isinstance(data, dict) or not data.get("full"):
        return None
    ctx = AddressContext(data["full"])
    if not ctx.street or ctx.street != data.get("street"):
        raise CleanupError("saved cleanup state does not parse back "
                           "to the same street — refusing to trust it")
    return ctx


def _evidence_snapshot(base_dir, order_ref):
    """Fingerprint of the cleaned order, taken at verification time.

    Hashes every remaining file in the order folder (the state,
    staged and final receipt files are the cleanup's own machinery
    and are excluded) and records whether the outbox is gone. A
    later retry compares the live tree against this snapshot: an
    exact match means the files are byte-for-byte what a
    context-carrying run verified clean."""
    order_dir = Path(base_dir) / "orders" / order_ref
    files = {}
    for entry in sorted(order_dir.iterdir(), key=lambda p: p.name):
        if entry.name in (STATE_NAME, STAGED_NAME, RECEIPT_NAME):
            continue
        if entry.is_symlink() or entry.is_dir():
            raise CleanupError(
                f"unexpected entry {entry.name!r} in the order "
                "folder — refusing to fingerprint it")
        files[entry.name] = hashlib.sha256(entry.read_bytes()).hexdigest()
    outbox_dir = Path(base_dir) / "outbox" / order_ref
    return {"files_sha256": files,
            "outbox_absent": not outbox_dir.exists()}


def _write_staged_receipt(order_dir, receipt, base_dir, order_ref, ctx):
    """Stage the verified receipt + its evidence (PR #6 fix).

    The staged file is NOT the receipt: it lives under a different
    name and makes no claim at the receipt's final name. It is
    written BEFORE the address-bearing state file is removed, so
    the evidence survives the one deletion that destroys the
    address context."""
    staged = {"staged": True, "receipt": receipt,
              "evidence": _evidence_snapshot(base_dir, order_ref)}
    _atomic_write_json(order_dir / STAGED_NAME, staged)
    text = (order_dir / STAGED_NAME).read_text(errors="replace")
    if json.loads(text) != staged:
        raise CleanupError("staged receipt does not read back as "
                           "written")
    if ctx.hits(text):
        raise CleanupError("staged receipt contains the street "
                           "address")
    return staged


def _load_staged(order_dir):
    """Read a staged receipt record, or None. Corrupt or malformed
    staged evidence is a loud error, never a silent skip."""
    path = order_dir / STAGED_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(errors="replace"))
    except (OSError, ValueError) as exc:
        raise CleanupError(f"staged receipt is unreadable: {exc}")
    if (not isinstance(data, dict) or data.get("staged") is not True
            or not isinstance(data.get("receipt"), dict)
            or not isinstance(data.get("evidence"), dict)):
        raise CleanupError("staged receipt is malformed — refusing "
                           "to trust it")
    return data


def _verify_staged_evidence(base_dir, order_ref, staged, ctx=None):
    """Re-check a staged record before relying on it. Returns the
    receipt it carries. Raises CleanupError on any mismatch — a
    retry must never certify from evidence that no longer matches
    the files on disk."""
    receipt = staged["receipt"]
    if (receipt.get("verified") is not True
            or receipt.get("tool") != "cleanup_local.py"
            or not isinstance(receipt.get("actions"), list)
            or any(not isinstance(a, dict) or a.get("verified") is not True
                   for a in receipt["actions"])):
        raise CleanupError("staged receipt does not carry a verified "
                           "cleanup record")
    evidence = staged["evidence"]
    current = _evidence_snapshot(base_dir, order_ref)
    if current != evidence:
        raise CleanupError(
            "files on disk no longer match the staged verification "
            "evidence — refusing to certify from it")
    if evidence.get("outbox_absent") is not True:
        raise CleanupError("staged evidence does not record the "
                           "outbox as removed")
    order_dir = Path(base_dir) / "orders" / order_ref
    for name in evidence["files_sha256"]:
        path = order_dir / name
        if path.suffix.lower() in RENDERED_SUFFIXES:
            raise CleanupError(
                f"staged evidence covers rendered artifact {name!r} "
                "— refusing to certify")
        if path.suffix.lower() == ".json":
            try:
                data = json.loads(path.read_text(errors="replace"))
            except ValueError as exc:
                raise CleanupError(
                    f"{name}: unparseable JSON at certification "
                    f"time: {exc}")
            if has_unrounded_customer_coords(data):
                raise CleanupError(
                    f"{name}: unrounded customer coordinates at "
                    "certification time")
            if ctx is not None and ctx.hits(
                    path.read_text(errors="replace")):
                raise CleanupError(
                    f"{name}: street address present at "
                    "certification time")
    return receipt


def _publish_receipt(order_dir, receipt, ctx):
    """Write the receipt at its FINAL name and verify the write.
    Called only once the address-bearing state file is gone."""
    receipt_path = order_dir / RECEIPT_NAME
    _atomic_write_json(receipt_path, receipt)
    text = receipt_path.read_text(errors="replace")
    if json.loads(text) != receipt:
        raise CleanupError("receipt on disk does not match the "
                           "verified receipt")
    if ctx is not None and ctx.hits(text):
        raise CleanupError("receipt itself contains the street "
                           "address")


def _complete_from_staged(base_dir, order_ref, ctx=None):
    """Finish a cleanup interrupted after state removal (PR #6 fix).

    There is no address context left — order.json is redacted and
    the state file is gone — so this path certifies ONLY from the
    staged record a context-carrying run left behind, after
    _verify_staged_evidence re-checks it against the live tree."""
    order_dir = Path(base_dir) / "orders" / order_ref
    staged_path = order_dir / STAGED_NAME
    receipt_path = order_dir / RECEIPT_NAME
    try:
        staged = _load_staged(order_dir)
        if staged is None:
            print("ERROR: no staged receipt to complete from.",
                  file=sys.stderr)
            return EXIT_FAILED
        receipt = _verify_staged_evidence(base_dir, order_ref,
                                          staged, ctx)
        if receipt_path.is_file():
            existing = json.loads(
                receipt_path.read_text(errors="replace"))
            if existing != receipt:
                print("ERROR: an existing cleanup receipt does not "
                      "match the staged verification record — "
                      "refusing to overwrite it.", file=sys.stderr)
                return EXIT_FAILED
            # Already published by the interrupted run; only the
            # staged file's removal was left undone.
        else:
            _publish_receipt(order_dir, receipt, ctx)
    except (OSError, ValueError, CleanupError) as exc:
        print(f"ERROR: could not complete certification for "
              f"{order_ref} from the staged record: {exc}",
              file=sys.stderr)
        print("  No new receipt was written by this run. The staged "
              "record is preserved — resolve the mismatch manually; "
              "do not claim a receipted cleanup.", file=sys.stderr)
        return EXIT_FAILED
    try:
        staged_path.unlink()
    except OSError as exc:
        print(f"ERROR: receipt published and verified for "
              f"{order_ref}, but the staged record "
              f"(orders/{order_ref}/{STAGED_NAME} — it contains no "
              f"street address) could not be removed: {exc}",
              file=sys.stderr)
        print("  State: receipt stands; re-run execute to remove "
              "the staged record.", file=sys.stderr)
        return EXIT_FAILED
    print(f"Cleanup complete for {order_ref}: certified from the "
          "staged verification evidence.")
    print(f"Receipt: orders/{order_ref}/{RECEIPT_NAME}")
    return EXIT_OK


def do_delete(action):
    path = action["path"]
    path.unlink()
    if path.exists():
        raise CleanupError(f"{action['rel']}: file still exists "
                           "after deletion")


def execute_cleanup(base_dir, order_ref, actions, ctx):
    order_dir = Path(base_dir) / "orders" / order_ref
    # PR #7 Finding 1 — staged-evidence side door: if a staged
    # receipt is on disk but this run has NO recovered address
    # context (ctx.street is empty — e.g. order.json is already
    # redacted and the state file is gone), a stale staged record
    # whose evidence no longer matches must NOT be discarded and
    # replaced by re-verifying under the empty context: string
    # redaction needs the street, so artifacts regenerated after
    # the interruption (e.g. a fresh send_log.json carrying the
    # full street address) would be classified "keep" and then
    # certified clean. Refuse before touching anything — the
    # staged record stays byte-for-byte, no receipt is written,
    # no artifact is modified.
    if (order_dir / STAGED_NAME).is_file() and not ctx.street:
        print(f"ERROR: refusing to clean up {order_ref}: a stale "
              f"staged receipt (orders/{order_ref}/{STAGED_NAME}) "
              "exists on disk, but this run has no recovered "
              "address context (no street address could be derived "
              "from order.json or a saved cleanup state), so stale "
              "staged evidence cannot be replaced without the "
              "original address context. Nothing was changed: the "
              "staged record is preserved, no receipt was written, "
              "and no artifact was modified. Restore the original "
              "address context and re-run to finish the cleanup.",
              file=sys.stderr)
        return EXIT_FAILED
    done, pending = [], [a for a in actions
                         if a["kind"] in ("redact", "delete",
                                          "delete_outbox")]
    # Finding 3: preserve the matching context BEFORE touching any
    # artifact. If this fails, refuse to start — a run that cannot
    # preserve its context could strand a half-cleaned order whose
    # retry then certifies the leftovers clean.
    if ctx.street:
        try:
            _save_context(order_dir, ctx)
        except OSError as exc:
            print(f"ERROR: could not preserve the address context "
                  f"for {order_ref} ({exc}) — refusing to start; "
                  "nothing was changed.", file=sys.stderr)
            return EXIT_FAILED
    try:
        for action in list(pending):
            if action["kind"] == "redact":
                do_redact(action, ctx)
            else:
                do_delete(action)
            done.append(action)
            pending.remove(action)
        outbox_dir = Path(base_dir) / "outbox" / order_ref
        if outbox_dir.is_dir():
            for d in sorted((p for p in outbox_dir.rglob("*")
                             if p.is_dir()),
                            key=lambda p: len(str(p)), reverse=True):
                d.rmdir()
            outbox_dir.rmdir()
            if outbox_dir.exists():
                raise CleanupError("outbox folder still exists "
                                   "after cleanup")
    except (OSError, ValueError, CleanupError) as exc:
        print(f"ERROR: cleanup FAILED for {order_ref}: {exc}",
              file=sys.stderr)
        print("  DONE (verified):", file=sys.stderr)
        for a in done:
            print(f"    - {a['kind']}: {a['rel']}", file=sys.stderr)
        print("  NOT DONE:", file=sys.stderr)
        for a in pending:
            print(f"    - {a['kind']}: {a['rel']}", file=sys.stderr)
        print("  No cleanup receipt was written. The preserved "
              "address context (cleanup_state.json) lets a re-run "
              "finish the job. Re-run plan mode to see the "
              "remaining work.", file=sys.stderr)
        return EXIT_FAILED

    # Receipt publication (PR #6 fix). Every action is verified at
    # this point. The receipt is STAGED first, together with the
    # evidence of that verification; the address-bearing state
    # file is removed SECOND; the final receipt is published LAST.
    # At no point does a verified receipt at its final name coexist
    # with the address-bearing state file.
    state_path = order_dir / STATE_NAME
    staged_path = order_dir / STAGED_NAME
    receipt = None
    try:
        staged = _load_staged(order_dir)
    except CleanupError as exc:
        print(f"NOTE: staged receipt not reusable ({exc}); "
              "re-verifying under the current context.",
              file=sys.stderr)
        staged = None
    if staged is not None:
        try:
            # A staged record from an interrupted run, re-checked
            # against the tree this run just verified: reusing it
            # preserves the original action record.
            receipt = _verify_staged_evidence(base_dir, order_ref,
                                              staged, ctx)
        except CleanupError as exc:
            # This run HAS re-verified every artifact itself
            # (inventory classified each file and every pending
            # action verified on execution, under the context), so
            # a stale staged record is discarded and re-staged —
            # never trusted.
            print(f"NOTE: staged receipt not reusable ({exc}); "
                  "re-verifying under the current context.",
                  file=sys.stderr)
            receipt = None
    if receipt is None:
        receipt = {
            "tool": "cleanup_local.py",
            "cleaned_at": datetime.now(timezone.utc).isoformat(),
            "verified": True,
            "retention": "street address removed; customer email, "
                         "town and ZIP retained; customer "
                         "coordinates rounded to ~1 km",
            "actions": [{"file": a["path"].name,
                         "where": a["rel"].split("/")[0],
                         "action": a["kind"], "verified": True}
                        for a in done],
            "not_covered": NOT_COVERED,
        }
        try:
            _write_staged_receipt(order_dir, receipt, base_dir,
                                  order_ref, ctx)
        except (OSError, ValueError, CleanupError) as exc:
            print(f"ERROR: cleanup actions completed and verified "
                  f"for {order_ref}, but the verified receipt "
                  f"could not be staged: {exc}", file=sys.stderr)
            print("  State: local cleanup DONE, receipt MISSING — "
                  "do not claim a receipted cleanup. The preserved "
                  "address context (cleanup_state.json) lets a "
                  "re-run finish the job.", file=sys.stderr)
            return EXIT_FAILED
    # The preserved context has done its job. It contains the
    # street address, so it must be gone BEFORE any verified
    # receipt exists at its final name.
    if state_path.is_file():
        try:
            state_path.unlink()
        except OSError as exc:
            print(f"ERROR: cleanup verified and staged for "
                  f"{order_ref}, but the state file "
                  f"(orders/{order_ref}/{STATE_NAME} — it contains "
                  f"the street address) could not be removed: {exc}",
                  file=sys.stderr)
            print("  State: NO verified receipt was written — the "
                  "staged record is not a receipt. The state file "
                  "is intact; re-run execute to finish.",
                  file=sys.stderr)
            return EXIT_FAILED
    # State gone: publish the verified receipt at its final name.
    try:
        _publish_receipt(order_dir, receipt, ctx)
    except (OSError, ValueError, CleanupError) as exc:
        print(f"ERROR: cleanup actions completed and verified for "
              f"{order_ref}, and the state file is removed, but "
              f"the receipt could not be published: {exc}",
              file=sys.stderr)
        print(f"  State: receipt MISSING, address context GONE — "
              f"the staged record (orders/{order_ref}/"
              f"{STAGED_NAME}) preserves the verification "
              "evidence; re-run execute to complete certification "
              "from it. Do not claim a receipted cleanup.",
              file=sys.stderr)
        return EXIT_FAILED
    # Published: consume the staged record so a completed cleanup
    # leaves no stray staging files behind.
    if staged_path.is_file():
        try:
            staged_path.unlink()
        except OSError as exc:
            print(f"ERROR: cleanup verified and receipt written "
                  f"for {order_ref}, but the staged record "
                  f"(orders/{order_ref}/{STAGED_NAME} — it "
                  f"contains no street address) could not be "
                  f"removed: {exc}", file=sys.stderr)
            print("  State: receipt stands; re-run execute to "
                  "remove the staged record.", file=sys.stderr)
            return EXIT_FAILED
    print(f"Cleanup complete for {order_ref}: "
          f"{len(done)} action(s) verified.")
    print(f"Receipt: orders/{order_ref}/{RECEIPT_NAME}")
    return EXIT_OK


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Local-layout PII cleanup (orders/ + outbox/). "
                    "Plan mode by default.")
    parser.add_argument("order_ref")
    parser.add_argument("--execute", action="store_true",
                        help="actually perform the cleanup "
                             "(default: plan only)")
    parser.add_argument("--delivered-confirmed", action="store_true",
                        help="attest delivery was confirmed sent; "
                             "required with --execute")
    parser.add_argument("--base-dir", default=None,
                        help="fulfillment dir containing orders/ and "
                             "outbox/ (default: this script's dir)")
    args = parser.parse_args(argv)

    if (not args.order_ref or args.order_ref in (".", "..")
            or "/" in args.order_ref or "\\" in args.order_ref):
        print(f"ERROR: invalid order_ref {args.order_ref!r}",
              file=sys.stderr)
        return EXIT_REFUSED
    base_dir = (Path(args.base_dir) if args.base_dir
                else Path(__file__).resolve().parent)

    if args.execute and not args.delivered_confirmed:
        print("REFUSING: --execute requires --delivered-confirmed "
              "(cleanup only after confirmed delivery). Nothing "
              "was changed.", file=sys.stderr)
        return EXIT_REFUSED

    try:
        actions, ctx = inventory(base_dir, args.order_ref)
    except CleanupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_NOTHING

    print_plan(actions, args.order_ref, args.execute)

    blocked = [a for a in actions if a["kind"] == "blocked"]
    if blocked:
        print("ERROR: blocked item(s) above - refusing to guess. "
              "Resolve them manually; nothing was changed.",
              file=sys.stderr)
        return EXIT_FAILED

    work = [a for a in actions
            if a["kind"] in ("redact", "delete", "delete_outbox")]
    if not work:
        state_path = base_dir / "orders" / args.order_ref / STATE_NAME
        if args.execute and state_path.is_file():
            # Finding 3: an earlier execute was interrupted after
            # its last artifact was processed but before it could
            # receipt. Everything now verifies clean under the
            # preserved context (inventory just classified it so) —
            # complete the certification instead of stranding the
            # order receipt-less.
            print("No remaining actions — completing certification "
                  "for an interrupted earlier run.")
            return execute_cleanup(base_dir, args.order_ref,
                                   actions, ctx)
        staged_path = base_dir / "orders" / args.order_ref / STAGED_NAME
        if args.execute and staged_path.is_file():
            # PR #6 fix: an earlier execute was interrupted after
            # the address-bearing state file was removed but before
            # the receipt was published. No address context remains
            # to re-derive — complete ONLY from the staged
            # verification evidence, re-checked against the live
            # tree, instead of stranding the order receipt-less or
            # certifying it blind.
            print("No remaining actions and no preserved address "
                  "context — completing certification from the "
                  "staged verification evidence.")
            return _complete_from_staged(base_dir, args.order_ref, ctx)
        print(f"Nothing to do for {args.order_ref}: no street "
              "address, precise coordinates, rendered artifacts, or "
              "outbox drafts found. (Not a silent success - "
              "exit nonzero.)", file=sys.stderr)
        return EXIT_NOTHING

    if not args.execute:
        print(f"Plan only - {len(work)} action(s) would run. "
              "Re-run with --execute --delivered-confirmed to "
              "perform them.")
        return EXIT_OK

    return execute_cleanup(base_dir, args.order_ref, actions, ctx)


if __name__ == "__main__":
    sys.exit(main())
