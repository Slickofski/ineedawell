"""Stage 8 — Quote comparison + refund trigger.

QUOTE_FIELDS: the standardized fields every driller reply is reduced to, so
quotes are comparable in plain English.

ingest flow (see ingest_quotes.py):
  - reads orders/<id>/quote_replies.json:
      {"replies": [{"company": str, "total_price_usd": num|null,
                    "depth_included_ft": num|null, "price_per_ft_beyond_usd": num|null,
                    "lead_time_weeks": str|null, "warranty_terms": str|null,
                    "exclusions": str|null, "notes": str|null}]}
  - 2+ usable replies  -> writes quote_comparison.md (side-by-side) and
                         finalizes the delivery draft.
  - fewer than 2      -> writes refund_instruction.md: refund the $100 to the
                         original payment method (needs Stripe) and notify the buyer.

A reply counts as a usable quote only if it has a total price AND an included
depth (or an explicit per-foot rate). Vague "call us" replies don't count.

Validation discipline (Codex R14, 2026-10-22): the numbers must be
positive and finite — empty strings, NaN/Infinity, zero/negative and
booleans are not numbers (numeric strings that parse ARE accepted;
operators transcribe replies by hand). And the MIN_QUOTES threshold
counts DISTINCT companies: duplicate entries for one company — the
second being a revision of the first — can never satisfy the
two-comparable-quotes promise on their own.
"""
import json
import math

QUOTE_FIELDS = [
    ("total_price_usd", "Total price (first attempt)"),
    ("depth_included_ft", "Depth included (ft)"),
    ("price_per_ft_beyond_usd", "Price per extra foot"),
    ("lead_time_weeks", "Lead time"),
    ("warranty_terms", "Warranty terms"),
    ("exclusions", "Excludes"),
]

MIN_QUOTES = 2


def _positive_number(v):
    """Return v as a float when it is a positive finite number (or a
    string that parses as one); otherwise None. Booleans are not
    numbers; empty/blank strings are not numbers."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
    elif isinstance(v, str) and v.strip():
        try:
            f = float(v.strip().replace(",", "").replace("$", ""))
        except ValueError:
            return None
    else:
        return None
    return f if math.isfinite(f) and f > 0 else None


def _usable(r: dict) -> bool:
    return _positive_number(r.get("total_price_usd")) is not None and (
        _positive_number(r.get("depth_included_ft")) is not None
        or _positive_number(r.get("price_per_ft_beyond_usd")) is not None
    )


def _company_key(r: dict) -> str:
    return " ".join(str(r.get("company") or "").split()).casefold()


def _fmt(v, money=False):
    if v is None or v == "":
        return "— not stated —"
    if money and isinstance(v, (int, float)):
        return f"${v:,.0f}"
    return str(v)


def build_comparison(order, replies: list) -> tuple[str, list]:
    # Distinct companies only (R14): when a company appears more than
    # once, the LAST entry is its current revision and supersedes
    # the earlier ones; duplicates never inflate the count.
    by_company = {}
    for r in replies:
        if _usable(r):
            by_company[_company_key(r)] = r
    usable = list(by_company.values())
    lines = [
        "# Driller quotes — side by side",
        "",
        f"Property: {order['property_address']}",
        f"{len(usable)} comparable quote(s) received.",
        "",
        "| | " + " | ".join(r["company"] for r in usable) + " |",
        "|" + "|".join(["---"] * (len(usable) + 1)) + "|",
    ]
    for key, label in QUOTE_FIELDS:
        money = "price" in key
        lines.append(
            f"| {label} | " + " | ".join(_fmt(r.get(key), money) for r in usable) + " |"
        )
    lines += [
        "",
        "## Plain-English read",
        "",
        "Compare total price first, then what depth that price covers — a cheaper "
        "quote with fewer included feet can cost more once drilling goes deep. "
        "Ask each driller what happens, in writing, if the hole is dry or low-yield: "
        "under the standard contract you still pay for the attempt.",
        "",
        "This comparison is information, not a recommendation. The drilling "
        "contract is between you and the driller you choose.",
    ]
    return "\n".join(lines) + "\n", usable


def build_refund_instruction(order, state=None) -> str:
    c = order["customer"]
    if state is None:
        state_block = ("Verified state: NOT COMPUTED — this instruction "
                       "was generated without the send-log/refund checks, "
                       "so the trigger below is UNVERIFIED.")
    else:
        lines = ["Verified state (from send_log.json + "
                 "refund_confirmed.json):"]
        lines.append(f"  - 14-day window closed: "
                     f"{'YES' if state['window_closed'] else 'NO'}")
        lines.append(f"  - initial + 2 follow-ups to every driller: "
                     f"{'YES' if state['followups_complete'] else 'NO'}")
        rec = state.get("refund_record")
        lines.append(f"  - refund confirmed: "
                     f"{'YES (' + str(rec['reference']) + ')' if rec else 'NO'}")
        state_block = "\n".join(lines)
    return f"""# REFUND INSTRUCTION — {order['order_id']}

Buyer: {c['name']} <{c['email']}> — {c['phone']}
Amount: $100.00 (concierge tier)

{state_block}

Trigger (only claim this once the state above verifies it): fewer than
{MIN_QUOTES} usable driller quotes arrived within the 14-day outreach
window (initial + 2 follow-ups to each ranked driller).

Action required (needs Stripe):
  1. Refund $100.00 to the buyer's original payment method.
  2. Write orders/<id>/refund_confirmed.json with the amount,
     the Stripe refund reference, and the date.
  3. Re-run ingest_quotes.py — the refund notice sends only when the
     window, the follow-ups, AND the confirmed refund all verify.

Do not retry outreach without the buyer's explicit go-ahead.
"""
