"""Stage 7 — Outreach: standardized bid-request drafts per driller.

Generates, per ranked driller:
  - initial bid-request email (personalized: property, buyer basics, nearby-well
    context, standardized spec so replies are comparable)
  - follow-up #1 (day 5): polite nudge
  - follow-up #2 (day 10): final nudge; request window closing

DRAFTS ONLY — written to outbox/<order_id>/. Nothing is sent.
Drillers without a harvested email get the same body labeled for their
available channel (web contact form / phone call script).

Structured messages (Codex R05 fix, 2026-10-21, Remy): each draft is
built FIRST as structured {to, subject, body, from_display} values
(build_message), and the manifest carries them under "messages" so
callers send them directly through the mailer — no text-header-block
round-trip re-parsing attacker-controlled text. The .txt drafts are
rendered from those same validated values for human review and for
the legacy follow-up sender. Every header-derived value (recipient,
subject source data, customer name/email, property address, driller
company/channel data) is CR/LF-checked via mailer.validate_header_value
before use: CR/LF anywhere is a hard ValueError, never a new header.
"""
import os

from pipeline import mailer

FOLLOWUP_DAYS = [5, 10]


def _check(field, value):
    """CR/LF hard rejection for a header-derived value (R05)."""
    if value is None:
        return None
    return mailer.validate_header_value(field, value)


def _geology_line(wells: dict) -> str:
    d, y = wells.get("depth_ft"), wells.get("yield_gpm")
    n = wells.get("wells_with_depth_yield", 0)
    if d and y:
        return (
            f"Public CT well records show {n} nearby water-supply wells drilled "
            f"{d['min']:g}-{d['max']:g} ft (median {d['median']:g} ft), yielding "
            f"{y['min']:g}-{y['max']:g} gpm. Please quote against this geology."
        )
    return "Limited public well records near this parcel: please quote on your local knowledge of the area."


def build_message(order, geo, wells, driller, kind: str, step: int = 0) -> dict:
    """Build one outreach message as structured values.

    Returns {"to", "subject", "body", "from_display"}. Raises
    ValueError if any header-derived value contains CR/LF.
    kind is "initial", "followup1" or "followup2" (step 1/2).
    """
    c = order["customer"]
    name = _check("customer name", c["name"])
    email = _check("customer email", c["email"])
    phone = _check("customer phone", c["phone"])
    address = _check("property_address", order["property_address"])
    company = _check("driller company", driller["company"])
    to = _check("driller email", driller["email"]) if driller.get("email") else None
    from_display = f"{name} <{email}>"

    if kind == "initial":
        subject = _check(
            "subject", f"Well drilling quote request: {address}")
        geo_line = _geology_line(wells)
        body = f"""Hello {company},

I'm looking for a quote to drill a private drinking-water well at:

    {address}
    (approx. {geo['lat']:.5f}, {geo['lon']:.5f})

Proposed use: domestic single-family residence (new construction), private
well under 10 gpm. {geo_line}

To keep quotes comparable, please reply with:
  1. Total price for the first attempt, and how many feet of drilling it includes
  2. Price per additional foot beyond the included depth
  3. Current lead time (weeks from signed contract to drilling)
  4. Warranty terms (yield, workmanship, and what happens on a dry/low-yield hole)
  5. Anything your price excludes (permit fees, pump/tank, water testing, etc.)

I'm collecting a small number of comparable quotes and will decide quickly.
Thank you,

{name}
{phone} | {email}
"""
    else:
        day = FOLLOWUP_DAYS[step - 1]
        if step == 1:
            lead = (
                "Just floating this back up: I'm still collecting quotes for the well "
                "drill at the address below and would like to include yours."
            )
        else:
            lead = (
                "Last nudge: I'm closing the quote request window in a few days and "
                "making a decision. If you'd like to bid, a reply with your total "
                "price, included depth, per-foot-beyond rate, lead time, and warranty "
                "terms is all I need."
            )
        subject = _check(
            "subject", f"Re: Well drilling quote request: {address}")
        body = f"""Hello {company},

{lead}

    {address}

Thank you,
{name}
{phone} | {email}
"""
    return {"to": to, "subject": subject, "body": body,
            "from_display": from_display}


def _render(msg: dict, driller) -> str:
    """Render a structured message as the legacy draft text format."""
    to_line = msg["to"] or ('[no email: use ' + driller['channel'] + ']')
    return (f"To: {to_line}\n"
            f"From: {msg['from_display']}\n"
            f"Subject: {msg['subject']}\n"
            f"\n"
            f"{msg['body']}")


def _initial(order, geo, wells, driller) -> str:
    return _render(build_message(order, geo, wells, driller, "initial"),
                   driller)


def _followup(order, driller, step: int) -> str:
    return _render(build_message(order, None, {}, driller,
                                  f"followup{step}", step), driller)


def _channel_note(driller) -> str:
    if driller["channel"] == "email":
        return f"Send to {driller['email']} (harvested from {driller['website']})."
    if driller["channel"] == "webform":
        return (
            f"No public email found: paste the email body into the contact form at "
            f"{driller['website']}. Backup: call {driller['phone']}."
        )
    return (
        f"No website/email: read the email body as a phone script when calling "
        f"{driller['phone']}."
    )


def build_outreach(order, geo, wells, ranked, outbox_dir) -> dict:
    os.makedirs(outbox_dir, exist_ok=True)
    manifest = []
    for d in ranked["drillers"]:
        # Validate the channel-note inputs too: the note sits in the
        # draft file above the separator and must stay single-line.
        _check("driller website", d.get("website"))
        _check("driller phone", d.get("phone"))
        messages = {
            "initial": build_message(order, geo, wells, d, "initial"),
            "followup1": build_message(order, geo, wells, d, "followup1", 1),
            "followup2": build_message(order, geo, wells, d, "followup2", 2),
        }
        files = {}
        for kind, msg in messages.items():
            path = os.path.join(outbox_dir, f"{d['slug']}_{kind}.txt")
            header = (
                "DRAFT: NOT SENT. "
                + _channel_note(d)
                + "\n" + ("-" * 70) + "\n"
            )
            with open(path, "w") as f:
                f.write(header + _render(msg, d))
            files[kind] = path
        manifest.append(
            {
                "company": d["company"],
                "channel": d["channel"],
                "to": d["email"] or d["phone"],
                "distance_miles": d["distance_miles"],
                "files": files,
                "messages": messages,
                "send_schedule": {"initial": "day 0", "followup1": "day 5", "followup2": "day 10"},
            }
        )
    return {"outreach": manifest, "window_days": 14,
            "note": "Drafts only. Sending requires authorized email infrastructure + human approval."}
