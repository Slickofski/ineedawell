"""Stage 6 (free tier) — Free Well Snapshot: the 60-second read.

Answers "should I worry" from public data: verdict, key numbers, cost band,
dry-hole truth, water-quality flags, setback top-lines, and what happens
next. No driller shortlist, no packet — those are the paid tiers.

Output: orders/<id>/screen.html + orders/<id>/screen.json
"""
import json
import os
from datetime import date
from urllib.parse import quote_plus

from pipeline import report as R
from pipeline import flags as FLAGS
from pipeline import permit_roadmap as PR
from pipeline import rules as RULES
from pipeline import nearby_wells as NW


def _duration_txt(hours) -> str:
    if hours is None:
        return "duration not recorded"
    if hours < 1:
        return f"{hours * 60:g} minutes ({hours:g} hours)"
    return f"{hours:g} hours"


def _storage_sentence(wells) -> str | None:
    """Borehole-storage sentence for the snapshot (2026-10-24,
    borehole-storage fix Part 1; wording from the approved audit
    drafts). The bore itself is a reserve: depth minus the resting
    level, at 1.47 gal/ft for a standard 6-inch bore. Appears only
    when at least one neighbor well carries a sane depth + resting-
    level pair — unknown storage is never stated. Deep and thin
    medians get honestly different sentences."""
    st = NW.storage_summary(wells.get("wells") or [])
    if not st["wells_with_column"]:
        return None
    med = st["median_storage_gal"]
    col = st["median_column_ft"]
    if st["thin"]:
        return (f"Borehole storage: these wells stand shallow "
                f"\u2014 the typical neighbor well holds only about "
                f"{med:g} gallons standing in the bore itself, so a "
                f"long heavy draw can outrun the reserve even when "
                f"the recorded yield looks strong.")
    return (f"Borehole storage: wells here also stand deep "
            f"\u2014 the typical neighbor well holds about {med:g} "
            f"gallons of water standing in the bore itself (about "
            f"{col:g} ft of water column), a reserve that can carry "
            f"a house through showers and laundry while the well "
            f"refills.")


def _storage_json(wells) -> dict:
    """Labeled borehole_storage block for screen.json (2026-10-24),
    beside well_strength. Medians are null — never 0 — when no sane
    depth + resting-level pair exists."""
    st = NW.storage_summary(wells.get("wells") or [])
    return {
        "wells_with_column": st["wells_with_column"],
        "excluded_impossible": st["excluded_impossible"],
        "median_column_ft": st["median_column_ft"],
        "median_storage_gal_6in_bore": st["median_storage_gal"],
    }


def _strength_note(wells) -> str | None:
    """Compact well-test line for the snapshot (2026-10-20; rewritten
    for Codex R03, 2026-10-21).

    Publishes only what the filings record — resting (static) level,
    level during the test, method, duration — and states plainly that
    sustained yield is unestablished: a single endpoint reading, with
    no stabilization or recovery measurement, cannot show sustained
    production. The earlier endpoint-to-sustained-production inference
    was removed, not reworded. Impossible levels are flagged as data
    errors, never narrated. Returns None when the filings carry no test
    levels at all, and the snapshot simply omits the line.

    Borehole storage (2026-10-24 fix Part 1): the standing-reserve
    sentence from _storage_sentence is appended whenever a sane
    depth + resting-level pair exists — and stands alone as the line
    when the filings carry storage data but no test levels.
    """
    s = NW.strength_summary(wells.get("wells") or [])
    trio, invalid = s["trio"], s["invalid"]
    parts = []
    if len(trio) == 1:
        w = trio[0]
        method = NW.METHOD_PLAIN.get(
            w.get("yield_test_type"),
            (w.get("yield_test_type") or "method not recorded").lower())
        parts.append(
            f"Recorded test at {w.get('address') or 'a neighbor well'}: "
            f"resting level {w['static_level_ft']:g} ft, level during "
            f"the test {w['pumping_level_ft']:g} ft, tested with "
            f"{method} over {_duration_txt(w.get('yield_test_hours'))}, "
            f"recorded yield {w['yield_gpm']:g} gpm.")
    elif len(trio) > 1:
        statics = [w["static_level_ft"] for w in trio]
        pumpings = [w["pumping_level_ft"] for w in trio]
        methods = ", ".join(sorted({
            NW.METHOD_PLAIN.get(w.get("yield_test_type"),
                                (w.get("yield_test_type")
                                 or "method not recorded").lower())
            for w in trio}))
        hours = s["hours"]
        dur = (f"{min(hours):g}\u2013{max(hours):g} hours"
               if hours else "durations not recorded")
        parts.append(
            f"{len(trio)} neighbor tests on file record resting levels "
            f"of {min(statics):g}\u2013{max(statics):g} ft and levels "
            f"during the test of {min(pumpings):g}\u2013{max(pumpings):g} "
            f"ft; methods: {methods}; test durations: {dur}.")
    if invalid:
        parts.append(
            f"{len(invalid)} filed test(s) report impossible water "
            "levels (a during-test level shallower than the resting "
            "level, or below the well bottom) and are excluded as data "
            "errors.")
    storage = _storage_sentence(wells)
    if not parts:
        # No test levels on file at all: the storage fact, when the
        # filings carry it, still belongs on the snapshot line.
        return storage
    if storage:
        parts.append(storage)
    parts.append(
        "Each filing records a single endpoint level, so sustained "
        "yield is unestablished from these records.")
    return " ".join(parts)


def _strength_json(wells) -> dict:
    s = NW.strength_summary(wells.get("wells") or [])
    return {
        "tests_with_full_details": len(s["trio"]),
        "tests_with_valid_details": len(s["trio"]),
        "invalid_tests_excluded": len(s["invalid"]),
        "sustained_yield": s["sustained_yield"],
        # Legacy endpoint-geometry counts, kept for key compatibility:
        # where the recorded level sat relative to the bottom. They are
        # not a sustained-production finding (R03).
        "tests_judged": len(s["judged"]),
        "worked_to_bottom": len(s["bottomed"]),
        "held_level": len(s["held"]),
        "note": _strength_note(wells),
    }


def _setback_mini(setbacks):
    rows = []
    for it in setbacks.get("items", []):
        rows.append(
            f'<div class="setback"><span class="badge warn">to confirm</span>'
            f'<div><b>{R.esc(it.get("rule"))}</b> &mdash; {R.setback_number_phrase(it)}'
            f'<div class="fine">{R.setback_detail(it)}</div>'
            f'<div class="fine">Your engineer confirms this distance on the site plan before filing.</div></div></div>'
        )
    return "\n".join(rows) or '<p class="fine">No setback data.</p>'


def _dossier_card(inbox) -> str:
    """The $39 dossier-intent card (parked 2026-10-24 — rendered only
    when FLAGS.DOSSIER_INTENT_ENABLED). Kept whole so the question can
    be switched back on exactly as it ran."""
    return f"""<div class="card upsell">
  <div class="word">Want the full dossier?</div>
  <p>This snapshot is free. We are testing a <b>$39 full report</b>: deeper
  well evidence, a ranked driller shortlist for this parcel, the full setback
  workup, and a print-ready summary for your file.</p>
  <p>If you would pay $39 for that on your next lot, just hit reply and type <b>YES</b>{
    (f' &mdash; or email <a href="mailto:{R.esc(inbox)}?subject=YES%20for%20the%20%2439%20report">{R.esc(inbox)}</a>'
     if inbox and not inbox.startswith("YOUR") else "")}.
  It costs you nothing to say so, and it decides what gets built next.</p>
  <p>Reply or not &mdash; thanks for reading either way. That&rsquo;s what keeps this free.</p>
</div>"""


TIMELINE = [
    ("Read the verdict above.", "That is the 60-second answer. Everything below is the evidence."),
    ("Put the cost band in your offer math.", "Include the dry-hole contingency before you fall in love with the lot."),
    ("Get 2\u20133 driller quotes before you commit.", "Ask each: what depth would you permit for here, and what exactly happens if the hole is dry?"),
    ("After offer accepted: health district.", "Site review, soil/perc tests, and permits run through the local health district — your driller signs the state permit as applicant."),
    ("Drill, then lab-test the water.", "Connecticut requires arsenic and uranium testing for new wells — confirm the panel with a certified lab, and ask whether radon makes sense for your lot."),
]


def build_screen(order, geo, wells, wq, setbacks, order_dir) -> str:
    business = R.load_business()
    c = order["customer"]
    today = date.today().isoformat()
    pname = R._biz(business, "product_name", "Well Snapshot")

    vcls, vword, vnote, vmeta = R._verdict_full(wells, order.get("parcel_acres"))
    # R01: "on record" is the total record count, and a recorded 0 gpm
    # median yield is shown, not hidden by a truthiness check.
    n_wells = (wells.get("total_water_supply_wells")
               or wells.get("wells_with_depth_yield") or 0)
    stats = R._stats_html(n_wells, wells, vmeta)
    # One pull per order (CX pass, 2026-10-29): the shared pull's date
    # rides the source line, identical on every surface of the order.
    pulled_txt = (f" (records pulled {R.esc(wells['pulled_at'])})"
                  if wells.get("pulled_at") else "")
    def _flag_card(f: dict) -> str:
        if isinstance(f, str):  # legacy string flags
            return f'<li>{R.esc(f)}</li>'
        return (
            '<div class="flag">'
            f'<div class="ftitle">{R.esc(f.get("title", ""))}</div>'
            f'<div class="fsummary">{R.esc(f.get("summary", ""))}</div>'
            f'<details class="fdetail"><summary>Where this comes from</summary>'
            f'{R.esc(f.get("detail", ""))}</details>'
            '</div>'
        )
    wq_flags = "\n".join(_flag_card(f) for f in wq.get("flags", []))
    steps = "\n".join(
        f"<li><b>{R.esc(t)}</b> {R.esc(s)}</li>" for t, s in TIMELINE
    )
    permit_steps = "\n".join(
        f"<li><b>{R.esc(s['step'])} &mdash; {R.esc(s['who'])}.</b> "
        f"{R.esc(s['detail'])}</li>"
        for s in PR.roadmap(wells.get("town"))
    )
    permit_authority = PR.permit_authority(wells.get("town"))
    _note = _strength_note(wells)
    strength_html = (f'<p style="margin-top:10px"><b>Strength check:</b> '
                     f'{R.esc(_note)}</p>') if _note else ""
    inbox = R._biz(business, "pilot_inbox", "")
    # $39 dossier-intent card: parked 2026-10-24 (FLAGS module) — the
    # component stays in the tree but renders nothing while parked.
    dossier_card = (_dossier_card(inbox)
                    if FLAGS.DOSSIER_INTENT_ENABLED else "")
    site_url = R._biz(business, "website", "https://ineedawell.com")
    site_host = site_url.replace("https://", "").replace("http://", "").rstrip("/")
    css = R.CSS.replace("</style>", "")
    maps_q = quote_plus(order["property_address"])

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Free Well Snapshot &mdash; {R.esc(order["property_address"])}</title>
<style>{css}
.upsell{{border:2px solid var(--teal);background:#f0fdfa}}
.upsell .word{{font-size:19px;font-weight:800;margin:0 0 8px}}
.hero-actions{{margin-top:18px;display:flex;gap:12px;flex-wrap:wrap}}
.hero-link{{display:inline-block;padding:11px 20px;border-radius:999px;
  border:2px solid rgba(255,255,255,.4);color:#fff;text-decoration:none;
  font-weight:700;font-size:.92rem;background:rgba(255,255,255,.08);
  cursor:pointer;font-family:inherit}}
.hero-link:hover{{background:rgba(255,255,255,.16)}}
</style>
</head>
<body>
<div class="wrap">

<div class="hero">
  <div class="kicker">Free well snapshot &middot; <a href="{R.esc(site_url)}" style="color:#fff">I Need a Well</a></div>
  <h1>{R.esc(order["property_address"])}</h1>
  <div class="addr">Should you worry about the water here? The 60-second answer, from public records.</div>
  <div class="meta"><span>Prepared for {(R.esc(c["name"]) if c.get("name") else "this property")}</span><span>{today}</span></div>
  <div class="hero-actions">
    <a class="hero-link" href="https://maps.apple.com/?q={maps_q}">View in Maps</a>
    <button class="hero-link" id="share-btn" hidden>Share this snapshot</button>
  </div>
</div>
<script>
(function() {{
  var b = document.getElementById('share-btn');
  if (navigator.share && location.protocol.indexOf('http') === 0 && b) {{
    b.hidden = false;
    b.addEventListener('click', function () {{
      navigator.share({{
        title: document.title,
        text: document.title,
        url: location.href
      }}).catch(function () {{}});
    }});
  }}
}})();
</script>

<div class="card verdict {vcls}">
  <div class="word">{vword}</div>
  <p>{vnote}</p>
  <div class="stats">{stats}</div>
</div>

<div class="card">
  <h2>What neighbors hit</h2>
  <p class="sub">Public well completion reports near this parcel</p>
  {R._depth_bars(wells)}
  {strength_html}
</div>

<div class="card">
  <h2>What to budget</h2>
  <p class="sub">Planning band from the local depth range &mdash; driller quotes are the real numbers</p>
  {R._cost_section(wells)}
</div>

<div class="card">
  <h2>Water: what to test</h2>
  <p class="sub">Required on every new CT well &mdash; arsenic and uranium. Radon is your call.</p>
  {wq_flags}
</div>

<div class="card">
  <h2>Setback top-lines</h2>
  <p class="sub">Setbacks are the legal distance a well must sit from septic and water &mdash; CT rules; your engineer confirms each one for your lot</p>
  {_setback_mini(setbacks)}
  <p class="fine" style="margin-top:10px">{R.esc(RULES.doubling_caveat())}</p>
</div>

<div class="card">
  <h2>The paperwork, step by step</h2>
  <p class="sub">Permits, order of operations, and who does what &mdash; the part nobody explains</p>
  <p class="fine">Well permits for this town run through {R.esc(permit_authority)}.
  Fees below are planning context, not quotes &mdash; verify with the district before acting.</p>
  <ol class="steps">{permit_steps}</ol>
</div>

<div class="card">
  <h2>What happens next</h2>
  <ol class="steps">{steps}</ol>
</div>

{dossier_card}

<div class="card">
  <h2>Where this came from</h2>
  <div class="fine">
  <p>Well records: {R.esc(wells.get("source", ""))}{pulled_txt} &mdash; online filings only
  (since 2021), so older wells exist on paper that we cannot see. Water quality:
  {R.esc(wq.get("source", ""))} &mdash; regional samples, not a parcel prediction.
  {R.esc(wq.get("final_word", ""))}</p>
  <p>{R.esc(R._biz(business, "report_disclaimer"))}</p>
  <p>This is a free pilot. Nobody was contacted on your behalf.</p>
  </div>
</div>

<div class="footer">
  <div class="bn">{R.esc(R._biz(business, "legal_name", "YOUR BUSINESS LEGAL NAME"))}</div>
  <div class="fine">Free pilot &middot; Connecticut &middot; Generated {today}</div>
</div>

</div>
</body>
</html>
"""
    path = os.path.join(order_dir, "screen.html")
    with open(path, "w") as f:
        f.write(doc)
    return path


def build_screen_json(order, geo, wells, wq, setbacks, order_dir) -> str:
    """Machine-readable mirror of the snapshot (screen.html).

    One deterministic JSON file per order so AI assistants and integrations
    can parse the verdict, cost band, flags, and setbacks without scraping
    prose. Same numbers as the HTML — cost_band() is the single source.
    Output: orders/<id>/screen.json
    """
    business = R.load_business()
    today = date.today().isoformat()
    vcls, vword, vnote = R._verdict(wells, order.get("parcel_acres"))
    doc = {
        "document": "well_snapshot",
        "format_version": "1",
        "generated": today,
        "order_id": order.get("order_id"),
        "tier": order.get("tier", "free"),
        "property_address": order.get("property_address"),
        "parcel_acres": order.get("parcel_acres"),
        "audience": ("developer_tract"
                     if (order.get("parcel_acres") or 0) >= R.TRACT_MIN_ACRES
                     else "single_family"),
        "coordinates": {
            "lat": geo.get("lat"),
            "lon": geo.get("lon"),
            "source": geo.get("source"),
            "display_name": geo.get("display_name"),
        },
        "verdict": {"class": vcls, "headline": vword, "detail": vnote},
        "nearby_wells": {
            "total_water_supply_wells": wells.get("total_water_supply_wells"),
            "wells_with_depth_yield": wells.get("wells_with_depth_yield"),
            "depth_count": wells.get("depth_count"),
            "yield_count": wells.get("yield_count"),
            "zero_yield_count": wells.get("zero_yield_count"),
            "missing_yield_count": wells.get("missing_yield_count"),
            "missing_depth_count": wells.get("missing_depth_count"),
            "records_in_radius": wells.get("records_in_radius"),
            "abandonment_records_in_radius": wells.get("abandonment_records_in_radius"),
            "town_water_supply_count": wells.get("town_water_supply_count"),
            "town_abandonment_count": wells.get("town_abandonment_count"),
            "radius_m": wells.get("radius_m"),
            "depth_ft": wells.get("depth_ft"),
            "yield_gpm": wells.get("yield_gpm"),
            # Deliberate, labeled addition (2026-10-20): the compact
            # well-strength summary behind the snapshot's Strength check
            # line — counts + the same sentence, never the raw internal
            # per-well drawdown fields.
            "well_strength": _strength_json(wells),
            # Labeled borehole-storage block (2026-10-24): the standing
            # reserve in the bore itself — medians over sane depth +
            # resting-level pairs, nulls when unknown.
            "borehole_storage": _storage_json(wells),
            "town": wells.get("town"),
            "stats_town": wells.get("stats_town"),
            "town_stats_fallback": wells.get("town_stats_fallback"),
            "town_depth_ft": wells.get("town_depth_ft"),
            "source": wells.get("source"),
            "pulled_at": wells.get("pulled_at"),
            "planning_note": wells.get("planning_note"),
            # Projected onto the published key set on purpose (2026-10-20):
            # nearby_wells records extra internal per-well fields (drawdown
            # capture) that must not leak into this published twin until a
            # deliberate format change adds them.
            "wells": [
                {k: w.get(k) for k in (
                    "address", "city", "depth_ft", "yield_gpm",
                    "static_level_ft", "company", "date")}
                for w in (wells.get("wells") or [])
            ],
        },
        "cost_band_usd": R.cost_band(wells),
        "water_quality": {
            "flags": wq.get("flags") or [],
            "source": wq.get("source"),
            "note": wq.get("note"),
            "final_word": wq.get("final_word"),
        },
        "setbacks": [
            {
                "rule": it.get("rule"),
                "minimum_ft": it.get("minimum_ft"),
                "condition": it.get("condition"),
                "scales_with": it.get("scales"),
                "status": it.get("status"),
                "source": it.get("source"),
            }
            for it in (setbacks.get("items") or [])
        ],
        "next_steps": [
            {"title": t, "detail": s} for t, s in TIMELINE
        ],
        "permit_authority": PR.permit_authority(wells.get("town")),
        "permit_roadmap": PR.roadmap(wells.get("town")),
        "provenance": {
            "well_records": wells.get("source"),
            "water_quality": wq.get("source"),
            "setback_rules": ("CT Public Health Code 19-13-B51 + CT DPH Technical "
                              "Standards + district publications, from the verified "
                              "rules table (Oct 2026) — field verification required"),
            "data_limitations": (
                "Well records: online filings only (since 2021); older wells exist "
                "on paper. Water quality: regional samples, not a parcel prediction. "
                "Setbacks: state rules verified Oct 2026; district add-ons "
                "confirmed only where the district publishes them."
            ),
        },
        "disclaimer": R._biz(business, "report_disclaimer"),
        "pilot_note": "Free pilot. Nobody was contacted on behalf of the requester.",
        "companion_html": "screen.html",
    }
    path = os.path.join(order_dir, "screen.json")
    with open(path, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    return path
