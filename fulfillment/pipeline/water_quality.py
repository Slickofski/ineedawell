"""Stage 4 — Water-quality risk flags from the Water Quality Portal.

Queries the EPA/USGS Water Quality Portal Result search for arsenic and
uranium result rows within 25 miles of the parcel, and adds bedrock-unit
risk context from the CT DPH/USGS private-well study:
  - 3.9% of sampled CT private wells exceeded the arsenic MCL
  - 4.7% exceeded the uranium MCL (~7% exceeded one or the other)

Public data supports risk *flags*, never a prediction for a specific future
well. Connecticut requires arsenic and uranium testing for newly constructed
private and semipublic wells (CT DPH, effective Oct 1, 2022) — the product
tells the owner to confirm the exact panel with a certified lab and ask about
radon. Every output ends with: lab testing is the final word.
"""
import csv
import io
import json

import requests

WQP_URL = "https://www.waterqualitydata.us/data/Result/search"
USER_AGENT = "WellConciergePipeline/1.0 (water quality screening; local test)"
RADIUS_MILES = 25

FINAL_WORD = "Risk flags are not a prediction. Laboratory testing of the finished well is the final word."


_EXPECTED_HEADERS = {"MonitoringLocationIdentifier", "CharacteristicName",
                     "ResultMeasureValue"}


def _count(characteristic: str, lat: float, lon: float) -> dict:
    """Count WQP result rows for one characteristic, validating the
    CONTENT, not just the HTTP status (Codex R20; validator ported
    from the original worker pipeline). An HTTP-200 HTML error page
    used to become two 'sample rows'.

    Returns {"result_rows": int|None, "status": ...} where status is
    available / valid_empty / invalid / unavailable. Invalid and
    unavailable counts are None — unknown, never a fabricated zero."""
    try:
        r = requests.get(
            WQP_URL,
            params={
                "lat": lat,
                "long": lon,
                "within": RADIUS_MILES,
                "characteristicName": characteristic,
                "mimeType": "csv",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=120,
        )
        r.raise_for_status()
    except requests.RequestException:
        return {"result_rows": None, "status": "unavailable"}
    reader = csv.DictReader(io.StringIO(r.text))
    if not _EXPECTED_HEADERS.issubset(reader.fieldnames or []):
        return {"result_rows": None, "status": "invalid"}
    rows = list(reader)
    if any(None in row
           or (row.get("CharacteristicName") or "").casefold()
           != characteristic.casefold() for row in rows):
        return {"result_rows": None, "status": "invalid"}
    return {"result_rows": len(rows),
            "status": "valid_empty" if not rows else "available"}


def _flag(title: str, summary: str, detail: str) -> dict:
    """One water-quality flag as three layers:
    - title: scannable headline (the "so what")
    - summary: the one useful sentence a buyer reads
    - detail: the technical backing, shown behind a <details> toggle, not inline
    """
    return {"title": title, "summary": summary, "detail": detail}


def format_flag(flag) -> str:
    """One flag as finished customer text — the single wording source
    for every surface that prints flags as copy (B-13).

    A dict flag (see _flag) renders as its first two layers joined the
    way the snapshot card and the delivery email present them: the
    title (scannable headline) and the summary (the one useful
    sentence). The detail layer is deliberately NOT in this text —
    technical backing stays behind the snapshot's <details> toggle and
    in screen.json's structured flags, never inline (Marcin's
    two-layer copy rule). Legacy plain-string flags pass through
    unchanged.
    """
    if isinstance(flag, str):
        return flag
    if isinstance(flag, dict):
        title = (flag.get("title") or "").strip()
        summary = (flag.get("summary") or "").strip()
        if title and summary:
            return f"{title}: {summary}"
        return title or summary
    if flag is None:
        return ""
    return str(flag)


def water_quality(order: dict, geo: dict, order_dir: str) -> dict:
    lat, lon = geo["lat"], geo["lon"]
    arsenic = _count("Arsenic", lat, lon)
    uranium = _count("Uranium", lat, lon)

    flags = []
    # One collapsed blurb, not a science digest: the state panel covers the
    # real risks, radon is the only optional call, cost comes from the lab.
    detail_bits = []
    if (arsenic["result_rows"] or 0) > 0:
        detail_bits.append(
            f"Water Quality Portal: {arsenic['result_rows']} uninterpreted "
            "regional arsenic sample rows within 25 miles — not a parcel "
            "finding. CT DPH/USGS private-well study: 3.9% of sampled CT "
            "wells exceeded the arsenic MCL (the state's safety limit)."
        )
    if (uranium["result_rows"] or 0) > 0:
        detail_bits.append(
            f"Water Quality Portal: {uranium['result_rows']} uninterpreted "
            "regional uranium sample rows within 25 miles — not a parcel "
            "finding. CT DPH/USGS private-well study: 4.7% exceeded the "
            "uranium MCL (the state's safety limit)."
        )
    for name, block in (("arsenic", arsenic), ("uranium", uranium)):
        if block["status"] in ("invalid", "unavailable"):
            detail_bits.append(
                f"Water Quality Portal {name} count unavailable "
                f"(response {block['status']}) — the regional sample "
                f"count is unavailable (not zero); the study rates "
                f"above still apply.")
    detail_bits.append("No queryable public result dataset for radon in water found.")
    flags.append(_flag(
        "Water quality — the state panel covers it",
        "Connecticut requires arsenic and uranium testing on every new well — "
        "the two contaminants that actually show up in CT bedrock — so the "
        "lab runs the state panel as routine. Radon is optional; ask the lab "
        "whether to add it, and get the panel price when you book.",
        " ".join(detail_bits)
    ))

    result = {
        "radius_miles": RADIUS_MILES,
        "source": "Water Quality Portal (EPA/USGS) Result search, CSV",
        "arsenic": arsenic,
        "uranium": uranium,
        "flags": flags,
        "note": "Counts are regional sample rows, not parcel predictions. "
                "Connecticut requires arsenic and uranium testing for newly "
                "constructed private and semipublic wells (CT DPH, effective "
                "Oct 1, 2022) — confirm the exact panel with a certified lab.",
        "final_word": FINAL_WORD,
    }
    with open(f"{order_dir}/water_quality.json", "w") as f:
        json.dump(result, f, indent=2)
    return result
