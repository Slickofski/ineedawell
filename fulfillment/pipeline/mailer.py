"""Send-capable mailer. DRY-RUN BY DEFAULT.

Live sending requires ALL of:
  1. --live flag passed on the CLI invocation
  2. A real paid order: order['paid'] is True, order['test'] is False,
     and order['stripe_payment_id'] is set
  3. Complete SMTP configuration in environment variables

Environment (credentials NEVER in code or files):
  CONCIERGE_SMTP_HOST   - SMTP server hostname (required for live)
  CONCIERGE_SMTP_PORT   - port, default 587
  CONCIERGE_SMTP_USER   - username (required for live)
  CONCIERGE_SMTP_PASS   - password / app password (required for live)
  CONCIERGE_FROM_EMAIL  - envelope + From address (required for live)
  CONCIERGE_FROM_NAME   - display name, default "Well Concierge"
  CONCIERGE_SMTP_TLS    - "1"/"0", default "1" (STARTTLS)

Every send attempt — and every dry-run skip — is appended to
orders/<order_id>/send_log.json for audit.

Header safety (Codex R04/R05 fix, 2026-10-21, Remy):
  - gate() is strict: test must be the boolean False and paid the
    boolean True — a string "false" test flag blocks, it never sends.
  - Recipient/subject/body travel as STRUCTURED values. send_email()
    validates them directly: CR/LF in a header-derived value is a
    hard ValueError, and when expected_recipient is given the final
    recipient must equal it exactly or the send aborts before SMTP.
  - parse_draft() remains for legacy draft files (send_followups.py,
    ingest_quotes.py) but is hardened: duplicate To/Subject headers
    or any header after the first blank line are rejected, so a
    re-parsed draft can no longer overwrite the recipient.
"""
import json
import os
import re
import smtplib
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage

REQUIRED_ENV = ["CONCIERGE_SMTP_HOST", "CONCIERGE_SMTP_USER",
                "CONCIERGE_SMTP_PASS", "CONCIERGE_FROM_EMAIL"]

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def validate_header_value(field: str, value) -> str:
    """Return the stripped value, or raise ValueError on CR/LF or a
    non-string. Every header-derived value passes through here."""
    if not isinstance(value, str):
        raise ValueError(
            f"{field} must be a string, got {type(value).__name__}: {value!r}")
    if "\r" in value or "\n" in value:
        raise ValueError(f"{field} must not contain CR/LF: {value!r}")
    return value.strip()


def verify_recipient(to, expected_recipient) -> str:
    """Verify the final recipient equals the selected record's email.

    Returns the normalized recipient; raises ValueError (loud abort)
    on any mismatch or on CR/LF in either value.
    """
    to_v = validate_header_value("recipient", to)
    exp_v = validate_header_value("expected recipient", expected_recipient)
    if to_v.casefold() != exp_v.casefold():
        raise ValueError(
            f"recipient verification failed: refusing to send to {to_v!r}; "
            f"expected the selected recipient {exp_v!r}")
    return to_v


def smtp_config():
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        return None, missing
    return {
        "host": os.environ["CONCIERGE_SMTP_HOST"],
        "port": int(os.environ.get("CONCIERGE_SMTP_PORT", "587")),
        "user": os.environ["CONCIERGE_SMTP_USER"],
        "password": os.environ["CONCIERGE_SMTP_PASS"],
        "from_email": os.environ["CONCIERGE_FROM_EMAIL"],
        "from_name": os.environ.get("CONCIERGE_FROM_NAME", "Well Concierge"),
        "tls": os.environ.get("CONCIERGE_SMTP_TLS", "1") == "1",
    }, []


def gate(order: dict, live_flag: bool):
    """Return (allowed, reason). Default-deny, strict types (R04):
    test must be boolean False (an explicit test=true is a permanent
    block, and a non-boolean test flag blocks too), paid must be
    boolean True, and stripe_payment_id must be a nonempty string."""
    if not live_flag:
        return False, "dry-run: --live flag not passed"
    test_val = order.get("test", True)
    if test_val is not False:
        if isinstance(test_val, bool):
            return False, "dry-run: order is marked as test"
        return False, (f"dry-run: order test flag is not a strict "
                       f"boolean false (got {test_val!r})")
    if order.get("paid") is not True:
        return False, "dry-run: order is not a paid order"
    pid = order.get("stripe_payment_id")
    if not isinstance(pid, str) or not pid.strip():
        return False, "dry-run: no stripe_payment_id on order"
    cfg, missing = smtp_config()
    if cfg is None:
        return False, f"dry-run: SMTP env incomplete, missing {missing}"
    return True, "live: paid order + --live + SMTP configured"


def parse_draft(path: str):
    """Strip the DRAFT header block, return (to, subject, body).

    Hardened (R05): only the header block before the first blank line
    is parsed, and a duplicate To or Subject header raises ValueError
    instead of silently overwriting the first one — that overwrite
    was the recipient-swap mechanism.
    """
    with open(path) as f:
        text = f.read()
    if "-" * 20 in text:
        _, email_part = text.split("-" * 20, 1)
    else:
        email_part = text
    lines = email_part.strip().splitlines()
    to = subject = None
    seen = {"to": 0, "subject": 0}
    body_start = len(lines)
    for i, ln in enumerate(lines):
        if ln == "":
            body_start = i + 1
            break
        low = ln.lower()
        if low.startswith("to:"):
            seen["to"] += 1
            if seen["to"] > 1:
                raise ValueError(
                    "duplicate To header in draft — possible header injection")
            to = ln[3:].strip()
        elif low.startswith("subject:"):
            seen["subject"] += 1
            if seen["subject"] > 1:
                raise ValueError(
                    "duplicate Subject header in draft — possible header injection")
            subject = ln[8:].strip()
    if to is not None:
        to = validate_header_value("draft recipient", to)
    if subject is not None:
        subject = validate_header_value("draft subject", subject)
    return to, subject, "\n".join(lines[body_start:])


def _read_log(order_dir: str) -> list:
    path = f"{order_dir}/send_log.json"
    try:
        with open(path) as f:
            log = json.load(f)
        return log if isinstance(log, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _log(order_dir: str, entry: dict):
    path = f"{order_dir}/send_log.json"
    log = _read_log(order_dir)
    entry["at"] = datetime.now(timezone.utc).isoformat()
    log.append(entry)
    with open(path, "w") as f:
        json.dump(log, f, indent=2)


_ATTACHMENT_TYPES = {".md": ("text", "markdown"),
                     ".txt": ("text", "plain"),
                     ".pdf": ("application", "pdf")}


def _prepare_attachments(attachment_paths) -> list:
    """Read + hash attachment files BEFORE any intent or SMTP
    activity (R12): a missing/unreadable attachment aborts the send
    outright — a delivery email that promises an attachment is
    never sent text-only. The SHA-256 binds the exact artifact to
    the send intent in the audit log."""
    import hashlib
    prepared = []
    for p in attachment_paths or []:
        with open(p, "rb") as f:
            data = f.read()
        name = os.path.basename(p)
        maintype, subtype = _ATTACHMENT_TYPES.get(
            os.path.splitext(name)[1].lower(),
            ("application", "octet-stream"))
        prepared.append({"filename": name,
                         "sha256": hashlib.sha256(data).hexdigest(),
                         "bytes": len(data),
                         "maintype": maintype, "subtype": subtype,
                         "_data": data})
    return prepared


def _attachment_meta(prepared) -> list:
    return [{k: v for k, v in a.items() if not k.startswith("_")}
            for a in prepared]


def send_email(to: str, subject: str, body: str, cfg: dict,
               order_dir: str, kind: str,
               expected_recipient: str = None,
               attachment_paths=None) -> dict:
    """Send one structured message. Raises ValueError BEFORE any SMTP
    activity on CR/LF in header-derived values, a malformed recipient,
    or — when expected_recipient is given — a recipient that does not
    equal the selected record's email (R05).

    Idempotency (Codex R10): the durable intent for an order is the
    (kind, recipient) pair in its send_log.json.
      - A live ok entry for the pair already exists -> the message
        was sent by an earlier run; return already_sent WITHOUT
        touching SMTP (replaying a command never duplicates mail).
      - An intent entry with no linked outcome -> a previous attempt
        crashed after recording intent but before recording the
        outcome; the mail may or may not have gone out. Refuse with
        uncertain=True — a human reconciles, never an automatic
        resend.
      - Otherwise an intent entry is appended BEFORE SMTP and the
        outcome entry after, linked by intent_id, so the uncertain
        window above is detectable instead of silent."""
    to = validate_header_value("recipient", to)
    if not EMAIL_RE.match(to):
        raise ValueError(f"invalid recipient email: {to!r}")
    subject = validate_header_value("subject", subject)
    if expected_recipient is not None:
        to = verify_recipient(to, expected_recipient)
    attachments = _prepare_attachments(attachment_paths)
    prior = [e for e in _read_log(order_dir)
             if e.get("kind") == kind and e.get("mode") == "live"
             and str(e.get("to", "")).casefold() == to.casefold()]
    if any(e.get("ok") for e in prior):
        return {"ok": True, "already_sent": True}
    outcome_ids = {e.get("intent_id") for e in prior
                   if e.get("intent_id") and e.get("status") != "intent"}
    if any(e.get("status") == "intent"
           and e.get("intent_id") not in outcome_ids for e in prior):
        return {"ok": False, "uncertain": True,
                "error": "a previous send attempt for this "
                         "(kind, recipient) recorded intent but no "
                         "outcome — the message may already have been "
                         "sent. Reconcile send_log.json manually "
                         "before resending."}
    from_email = validate_header_value("from_email", cfg["from_email"])
    from_name = validate_header_value("from_name", cfg.get("from_name", ""))
    msg = EmailMessage()
    msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    for a in attachments:
        msg.add_attachment(a["_data"], maintype=a["maintype"],
                           subtype=a["subtype"], filename=a["filename"])
    intent_id = uuid.uuid4().hex
    _log(order_dir, {"kind": kind, "to": to, "subject": subject,
                     "mode": "live", "status": "intent",
                     "intent_id": intent_id,
                     "attachments": _attachment_meta(attachments)})
    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as s:
            if cfg["tls"]:
                s.starttls()
            s.login(cfg["user"], cfg["password"])
            s.send_message(msg)
        result = {"ok": True}
    except Exception as e:
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    _log(order_dir, {"kind": kind, "to": to, "subject": subject,
                     "mode": "live", "intent_id": intent_id,
                     "attachments": _attachment_meta(attachments),
                     **result})
    return result


def dry_skip(to: str, subject: str, order_dir: str, kind: str, reason: str):
    if isinstance(to, str):
        to = validate_header_value("recipient", to)
    if isinstance(subject, str):
        subject = validate_header_value("subject", subject)
    _log(order_dir, {"kind": kind, "to": to, "subject": subject,
                     "mode": "dry_run", "ok": True, "note": reason})
