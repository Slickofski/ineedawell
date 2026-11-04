"""Stage 1 — Order intake: validate and normalize the order JSON.

Input:  {"name": str, "email": str, "phone": str, "property_address": str,
          "lat": float (optional), "lon": float (optional)}
Output: normalized order record written to orders/<order_id>/order.json

Validation discipline (Codex R04/R05 fix, 2026-10-21, Remy):
  - ALL fields are validated and ALL errors are collected BEFORE
    anything is written; any error rejects the order with the full
    list and nothing is normalized silently.
  - paid / test are STRICT booleans: only real JSON booleans count.
    The string "false" is a type error, never True. An explicit
    test=true is permanent — paid=true can never clear it, so a test
    order can never become live-send eligible here or in the mailer.
  - Header-derived values (name, email, property_address, phone) must
    not contain CR/LF: a multiline value is how an injected
    "To:"/"Subject:" line used to hijack outreach drafts (R05).
"""
import json
import os
import re
from datetime import date

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

# An order_ref becomes a directory name under orders/ and outbox/.
# It must stay a single safe path component (Codex R06): no
# separators, no dots (so no "." / ".." components), no leading
# punctuation, bounded length. The site emits ORD-<date>-<alnum/dash>
# and the fallback below emits ORD-<date>-<slug>; both fit this shape.
ORDER_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def validate_order_ref(ref) -> str:
    """Return the order_ref unchanged, or raise ValueError.

    Called at the import border (new_order_id) and again at every
    path construction (order_paths): an unvalidated ref must never
    reach a mkdir/open, because an absolute ref discards the storage
    root entirely and a '../' ref writes outside the retention
    inventory (R06)."""
    if not isinstance(ref, str):
        raise ValueError(
            f"order_ref must be a string, got {type(ref).__name__}: {ref!r}")
    ref = ref.strip()
    if not ORDER_REF_RE.match(ref):
        raise ValueError(
            f"unsafe order_ref {ref!r}: must be a single path component "
            "of letters/digits/dash/underscore, 1-128 chars, no dots "
            "or separators")
    return ref


def order_paths(base: str, order_id: str):
    """(order_dir, outbox_dir) for an order, with resolved containment
    enforced: each dir must sit DIRECTLY under its storage root."""
    import os
    ref = validate_order_ref(order_id)
    out = []
    for root_name in ("orders", "outbox"):
        root = os.path.realpath(os.path.join(base, root_name))
        candidate = os.path.realpath(os.path.join(root, ref))
        if os.path.dirname(candidate) != root:
            raise ValueError(
                f"order_ref {ref!r} escapes the {root_name} storage root")
        out.append(candidate)
    return out[0], out[1]

TIER_DEFAULTS = {"concierge": 100, "report": 39, "free": 0}


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError(f"phone number must have 10 digits, got {raw!r}")
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return re.sub(r"-+", "-", s)[:60]


def supersede_failure_marker(order_dir: str):
    """Success-path state hygiene (Codex R23): an earlier failed /
    held / declined attempt leaves fulfillment_failed.json behind,
    and a successful retry used to write its fulfilled summary
    alongside it — two contradictory live states, and the pilot
    checklist keys off the failure file. Rename the marker to
    fulfillment_failed.superseded.json (history preserved, live
    name freed) and return its mode/stage for the summary, or None
    when there was nothing to supersede."""
    marker = os.path.join(order_dir, "fulfillment_failed.json")
    if not os.path.exists(marker):
        return None
    try:
        with open(marker) as f:
            old = json.load(f)
    except Exception:
        old = {}
    os.replace(marker, os.path.join(
        order_dir, "fulfillment_failed.superseded.json"))
    return {"mode": old.get("mode"), "stage": old.get("stage")}


def new_order_id(raw: dict) -> str:
    # The landing page generates order_ref client-side; reuse it so the DB row,
    # the fulfillment directory, and the customer's receipt all match.
    # The supplied ref is validated, never trusted (R06): it becomes a
    # directory name, so an absolute or traversing ref is rejected here,
    # before any runner builds a path from it.
    if raw.get("order_ref"):
        return validate_order_ref(raw["order_ref"])
    name = (raw.get("name") or "").strip()
    address = (raw.get("property_address") or "").strip()
    name_slug = slugify(name)
    street_slug = slugify(address.split(',')[0])
    # Budget the slugs to the validator's 128-char cap (PR #7
    # Finding 2): the fixed prefix "ORD-YYYYMMDD-" is 13 chars, so
    # the two slugs plus their joining dash must fit in 115 chars
    # (name + street <= 114). Without this, two 60-char slugs mint
    # a 134-char ID that validate_order_ref rejects. Truncate the
    # street first; truncate the name only if it alone exceeds
    # the budget (impossible today — slugify caps it at 60 — but
    # coded defensively). Normal-length inputs are unchanged.
    if len(name_slug) + len(street_slug) > 114:
        if len(name_slug) > 114:
            name_slug = name_slug[:114].rstrip("-")
        street_slug = street_slug[:114 - len(name_slug)].rstrip("-")
    return validate_order_ref(
        f"ORD-{date.today():%Y%m%d}-{name_slug}-{street_slug}")


def _str_field(raw: dict, key: str, errors: list) -> str:
    """String field with CR/LF injection rejection (R05)."""
    v = raw.get(key)
    if v is None:
        return ""
    if not isinstance(v, str):
        errors.append(f"{key} must be a string, got {type(v).__name__}: {v!r}")
        return ""
    if "\r" in v or "\n" in v:
        errors.append(f"{key} must not contain CR/LF (header injection risk)")
    return v.strip()


def _strict_bool(raw: dict, key: str, default: bool, errors: list) -> bool:
    """Only a real boolean counts (R04). Strings/ints are type errors;
    a missing or null value falls back to the documented default."""
    if key not in raw or raw[key] is None:
        return default
    v = raw[key]
    if isinstance(v, bool):
        return v
    errors.append(
        f"{key} must be a boolean (true/false), got {type(v).__name__}: {v!r}")
    return default


def intake(raw: dict, order_dir: str) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("order must be a JSON object")
    errors = []

    name = _str_field(raw, "name", errors)
    email = _str_field(raw, "email", errors)
    phone = _str_field(raw, "phone", errors)
    address = _str_field(raw, "property_address", errors)

    # Tier: unknown tiers are validation errors, never remapped.
    tier_raw = raw.get("tier")
    if tier_raw is None or (isinstance(tier_raw, str)
                            and not tier_raw.strip()):
        tier = "concierge"
    elif not isinstance(tier_raw, str):
        errors.append(
            f"tier must be a string, got {type(tier_raw).__name__}: {tier_raw!r}")
        tier = "concierge"
    else:
        tier = tier_raw.strip().lower()
    if tier not in TIER_DEFAULTS:
        errors.append(
            f"unknown tier: {tier!r} (expected 'free', 'report' or 'concierge')")

    if not name and tier != "free":
        errors.append("name is required")
    if not EMAIL_RE.match(email):
        errors.append(f"invalid email: {email!r}")
    if tier == "free" and not phone:
        phone_norm = ""  # free tier: phone optional, requesters won't give one
    else:
        try:
            phone_norm = normalize_phone(phone)
        except ValueError as e:
            errors.append(str(e))
            phone_norm = phone
    if not address:
        errors.append("property_address is required")

    # Payment state. Hand-supplied orders default to test/dry-run.
    # A real Stripe webhook sets paid=True, test=False, stripe_payment_id=<pi_...>.
    # test is PERMANENT: an explicit test=true is preserved exactly;
    # paid=true never clears it (R04).
    paid = _strict_bool(raw, "paid", False, errors)
    test = _strict_bool(raw, "test", True, errors)
    stripe_payment_id = raw.get("stripe_payment_id")
    if stripe_payment_id is not None:
        if not isinstance(stripe_payment_id, str):
            errors.append(
                f"stripe_payment_id must be a string, got "
                f"{type(stripe_payment_id).__name__}: {stripe_payment_id!r}")
            stripe_payment_id = None
        else:
            stripe_payment_id = stripe_payment_id.strip() or None
    if paid and not stripe_payment_id:
        errors.append("paid orders require stripe_payment_id (test orders stay unpaid)")

    # Amount: a real (non-bool) integer number of dollars, >= 0.
    # Integral floats from JSON numbers are accepted; strings, bools,
    # fractional or negative values are errors — never coerced.
    if "amount_usd" not in raw or raw["amount_usd"] is None:
        amount_usd = TIER_DEFAULTS.get(tier, TIER_DEFAULTS["concierge"])
    else:
        a = raw["amount_usd"]
        if isinstance(a, bool) or not isinstance(a, (int, float)):
            errors.append(f"invalid amount_usd: {a!r}")
            amount_usd = TIER_DEFAULTS.get(tier, TIER_DEFAULTS["concierge"])
        elif isinstance(a, float) and not a.is_integer():
            errors.append(f"invalid amount_usd: {a!r}")
            amount_usd = TIER_DEFAULTS.get(tier, TIER_DEFAULTS["concierge"])
        elif a < 0:
            errors.append(f"invalid amount_usd: {a!r}")
            amount_usd = TIER_DEFAULTS.get(tier, TIER_DEFAULTS["concierge"])
        else:
            amount_usd = int(a)

    # Single gate: every error above is reported together and NOTHING
    # has been written yet.
    if errors:
        raise ValueError("order validation failed: " + "; ".join(errors))

    order_id = new_order_id(raw)
    order = {
        "order_id": order_id,
        "tier": tier,
        "amount_usd": amount_usd,
        "paid": paid,
        "test": test,
        "stripe_payment_id": stripe_payment_id,
        "customer": {"name": name, "email": email, "phone": phone_norm},
        "property_address": address,
        # Optional parcel size (acres) when the source knows it — pre-scout
        # listings do. report._verdict uses it to frame tracts (>= 5 ac) for
        # developers instead of single-family buyers. (2026-10-14, Remy, at
        # Marcin's decision: keep tracts, rewrite packets for developers.)
        "parcel_acres": raw.get("parcel_acres"),
        "lat": raw.get("lat"),
        "lon": raw.get("lon"),
        "status": "intake_complete",
    }
    with open(f"{order_dir}/order.json", "w") as f:
        json.dump(order, f, indent=2)
    return order
