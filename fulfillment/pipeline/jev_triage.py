"""Stage 1.5 — Jev intake triage: fast pre-geocode classification.

Sits between intake and geocode. Asks Jev (TypeSafe's "System One" decision
model) two questions about the raw property address:

  - address_scope (Choice): connecticut_address | out_of_state | unparseable
  - genuine_request (Noul): does the address look like a real address a person
    typed, as opposed to gibberish, a joke, or a test entry?

Only HIGH-confidence answers short-circuit the pipeline:

  - out_of_state / unparseable with confidence >= 0.80 -> decline with the
    same graceful drafts the geocode/scope checks already write
  - genuine_request P(yes) <= 0.20 -> held for human review (never silently
    dropped; Marcin approves every customer email anyway)

Everything else — low confidence, API error, timeout, or no API key
configured — means triage is SKIPPED and the pipeline runs exactly as before.
The geocode gate stays the hard gate; Jev is a fast pre-filter, not a decider.
This is deliberate: fail closed on danger, fail open to humans on ambiguity.

Privacy: the only thing sent to TypeSafe's API is the property address string
(plus the tier label). No name, email, or phone. TypeSafe states it does not
train on customer requests. The address is still scrubbed after delivery per
the pilot privacy rule; triage is part of building the snapshot.

Cost: $0.042 per million input tokens, output free. A triage call is a few
hundred tokens — on the order of a tenth of a cent.

Auth: tries, in order —
  1. explicit api_key argument,
  2. TYPESAFE_API_KEY from the environment.
Unset/unavailable -> skip quietly (reason: "no API key configured").
"""
import json
import os
import time
import urllib.request
import urllib.error

API_URL = "https://api.typesafe.ai/v1/systemone"
# Pinned: TypeSafe advises pinning the version once confidence thresholds are
# tuned against it. Re-tune ACT_* thresholds if this moves.
JEV_MODEL = "jev-1.13.0"

# Act on a scope verdict only when the model is this concentrated.
ACT_CONFIDENCE = 0.80
# Hold for human review only when P(genuine) is this low. Noul near 0.5 means
# "yes and no similarly probable", not "medium" — so only the deep-low tail
# counts as junk.
JUNK_THRESHOLD = 0.20

TIMEOUT_S = 10

def _resolve_auth(explicit: str = None):
    """Return (mode, key). mode is 'env' or None.

    Env-key-only auth: this public copy carries no vault or
    credential-store fallback.
    """
    if explicit and explicit.strip():
        return "env", explicit.strip()
    env_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if env_key:
        return "env", env_key
    return None, None


def _questions():
    return {
        "address_scope": {
            "type": "choice",
            "instructions": (
                "What does this property address text refer to? Judge the "
                "address as written, including any state abbreviation or "
                "state name in it."
            ),
            "criteria": {
                "connecticut_address": (
                    "A real-looking street address in Connecticut, USA "
                    "(may say CT or Connecticut, or be a CT town with no "
                    "state given)."
                ),
                "out_of_state": (
                    "A real-looking street address in a US state other than "
                    "Connecticut."
                ),
                "unparseable": (
                    "Not a usable street address: gibberish, a joke entry, "
                    "a place name with no street, or missing the parts an "
                    "address needs."
                ),
            },
        },
        "genuine_request": {
            "type": "noul",
            "instructions": (
                "The property address looks like a real address a person "
                "typed in order to get a well snapshot, not keyboard-mashing, "
                "a joke entry like '123 Fake St', or a test/probe string."
            ),
        },
    }


def _call_jev(state: dict, auth) -> dict:
    """auth is (mode, key) from _resolve_auth. Returns (answers, latency_ms)."""
    _mode, key = auth
    body = json.dumps(
        {"model": JEV_MODEL, "state": state, "questions": _questions()}
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(API_URL, data=body, headers=headers,
                                 method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        # 401 = bad key, 422 = malformed question, 429/529 = back off.
        # None of these should ever break fulfillment.
        raise RuntimeError(f"jev api http {e.code}: {e.read(120)!r}")
    except Exception as e:  # timeout, DNS, connection reset...
        raise RuntimeError(f"jev api unreachable: {type(e).__name__}: {e}")
    latency_ms = int((time.monotonic() - t0) * 1000)
    return _validate_answers(payload), latency_ms


def _validate_answers(payload) -> dict:
    """One documented failure boundary for provider output (R19):
    valid JSON with the wrong SHAPE used to escape as AttributeError
    (payload/answers/sub-objects as lists) and halt the runner.
    Anything malformed raises RuntimeError here, which triage()
    already degrades to 'skipped'."""
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"jev api returned non-object JSON ({type(payload).__name__})")
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        raise RuntimeError("jev api response 'answers' is not an object")
    for key in ("address_scope", "genuine_request"):
        if key in answers and not isinstance(answers[key], dict):
            raise RuntimeError(
                f"jev api response '{key}' is not an object")
    scope = answers.get("address_scope") or {}
    conf = scope.get("confidence")
    if conf is not None and (isinstance(conf, bool)
                             or not isinstance(conf, (int, float))
                             or not 0.0 <= conf <= 1.0):
        raise RuntimeError(
            f"jev api confidence out of range: {conf!r}")
    genuine = answers.get("genuine_request") or {}
    noul = genuine.get("noul")
    if noul is not None and (isinstance(noul, bool)
                             or not isinstance(noul, (int, float))
                             or not 0.0 <= noul <= 1.0):
        raise RuntimeError(f"jev api noul out of range: {noul!r}")
    return answers


def triage(raw_order: dict, order_dir: str, api_key: str = None) -> dict:
    """Run the Jev triage. Never raises: failures degrade to 'skipped'."""
    address = (raw_order.get("property_address") or "").strip()
    result = {
        "model": JEV_MODEL,
        "decision": "skipped",
        "reason": "",
        "address_scope": None,
        "address_scope_confidence": None,
        "genuine_p": None,
        "latency_ms": None,
    }
    key = _resolve_auth(api_key)
    if key[0] is None:
        result["reason"] = "no API key configured"
        _write(result, order_dir)
        return result
    if not address:
        result["reason"] = "empty address"
        _write(result, order_dir)
        return result

    try:
        answers, latency_ms = _call_jev(
            {"property_address": address,
             "tier": (raw_order.get("tier") or "free")}, key)
    except RuntimeError as e:
        result["reason"] = str(e)
        _write(result, order_dir)
        return result

    result["latency_ms"] = latency_ms
    # Defense in depth (R19): even if a future _call_jev replacement
    # skips validation, non-dict sub-objects degrade to empty rather
    # than raising out of triage.
    scope = answers.get("address_scope")
    if not isinstance(scope, dict):
        scope = {}
    genuine = answers.get("genuine_request")
    if not isinstance(genuine, dict):
        genuine = {}
    winner = scope.get("choice")
    conf = scope.get("confidence")
    p_genuine = genuine.get("noul")
    result["address_scope"] = winner
    result["address_scope_confidence"] = conf
    result["genuine_p"] = p_genuine
    result["probabilities"] = scope.get("probabilities")

    # Junk check first: only the deep-low tail counts.
    if isinstance(p_genuine, (int, float)) and p_genuine <= JUNK_THRESHOLD:
        result["decision"] = "held_for_review"
        result["reason"] = f"P(genuine)={p_genuine:.2f} <= {JUNK_THRESHOLD}"
    elif winner in ("out_of_state", "unparseable") and isinstance(
        conf, (int, float)
    ) and conf >= ACT_CONFIDENCE:
        result["decision"] = "out_of_scope" if winner == "out_of_state" else "unparseable"
        result["reason"] = f"{winner} at confidence {conf:.2f}"
    else:
        result["decision"] = "proceed"
        result["reason"] = "low confidence or CT address; geocode gate decides"
    _write(result, order_dir)
    return result


def _write(result: dict, order_dir: str):
    try:
        with open(os.path.join(order_dir, "triage.json"), "w") as f:
            json.dump(result, f, indent=2)
    except OSError:
        pass
