"""Stage 10 — Customer delivery email.

Builds the delivery email draft. Two states:
  - quotes ready: comparison summary + packet attached + boundary language.
  - refund: fewer than 2 quotes -> refund notice + boundary language.

In live mode this sends DIRECTLY to the customer — nothing routes through
Marcin for approval. In dry-run it stays a draft file.
"""
from datetime import date

BOUNDARY = """Where our job ends: the $100 concierge covered finding drillers,
collecting comparable quotes, and preparing your packet. From here the deal is
between you and the driller you choose. We don't supervise the work, we don't
guarantee water yield, water quality, or permit approval, and we're not a party
to your drilling contract."""


def build_delivery_draft(order, comparison_md=None, refund=False,
                         refund_record=None) -> str:
    """Build the delivery draft.

    The refund variant asserts completed facts — outreach happened,
    two follow-ups each, a refund was issued — so it REQUIRES a
    verified refund record (Codex R13): {amount_usd, reference,
    confirmed_at} from orders/<id>/refund_confirmed.json, written by
    the operator after the actual Stripe refund. Without one this
    raises ValueError instead of drafting claims nobody verified;
    the email states the record's actual amount and reference.
    """
    c = order["customer"]
    today = date.today().isoformat()
    if refund:
        if not refund_record:
            raise ValueError(
                "refund delivery requires a verified refund record "
                "(refund_confirmed.json) — refusing to draft an "
                "unverified refund announcement (R13)")
        amount = refund_record["amount_usd"]
        subject = (f"Your well concierge — ${amount} refund issued "
                   f"({order['property_address']})")
        body = f"""Hi {c['name']},

We reached out to local drillers about {order['property_address']} — initial
request plus two follow-ups each — and fewer than 2 usable quotes came back
within the window.

Per our promise (2+ comparable quotes or your money back), we've refunded
${amount} to your original payment method (refund reference
{refund_record['reference']}, issued {refund_record['confirmed_at']}).
Please allow 5-10 business days for it to appear.

Your prefilled permit packet is still yours to keep (attached) — the
nearby-well evidence, setback checklist, and filing steps are all in there
if you want to contact drillers directly.

{BOUNDARY}

— The well concierge
{today}"""
    elif comparison_md:
        # short summary: first table lines
        body = f"""Hi {c['name']},

Your driller outreach for {order['property_address']} is done. Here's what
came back:

{comparison_md}
Your prefilled permit packet is attached. Your next steps:
  1. Pick a driller from the comparison above.
  2. Sign your sections of the packet; the driller signs theirs.
  3. File with the health district and drill.

{BOUNDARY}

Questions about the packet or the quotes — just reply to this email.

— The well concierge
{today}"""
    else:
        subject = f"Your well quotes + permit packet — {order['property_address']} (AWAITING QUOTES)"
        body = f"""Hi {c['name']},

[DRAFT — quotes not yet ingested. This section fills in when driller replies
arrive via ingest_quotes.py.]

{BOUNDARY}

— The well concierge
{today}"""
        return f"To: {c['email']}\nSubject: {subject}\n\n{body}"

    if not refund:
        subject = (f"Your well quotes + permit packet — "
                   f"{order['property_address']}")
    return f"To: {c['email']}\nSubject: {subject}\n\n{body}"


def build_refund_pending_draft(order, missing: list) -> str:
    """Internal placeholder written INSTEAD of a refund notice when
    the refund state is not fully verified (R13). It asserts nothing
    to the customer — it is never sent; it records, in the artifact
    the operator reviews, exactly which verifications are missing."""
    c = order["customer"]
    return (f"To: {c['email']}\n"
            f"Subject: [NOT SENT — refund not yet verified] "
            f"{order['property_address']}\n\n"
            f"[DRAFT — DO NOT SEND. The refund notice for this order "
            f"is held until every item below is verified from durable "
            f"records:\n"
            + "".join(f"  - {m}\n" for m in missing)
            + f"]\n")
