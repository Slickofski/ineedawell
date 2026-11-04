"""Stage 7 (report tier) — Customer-facing Property Well Report.

Builds a polished, self-contained HTML report from the stage 1-6 data
artifacts (intake, geocode, nearby wells, water quality, setbacks, driller
shortlist). No outreach, no packet, no sending — the report tier is pure
information.

Output: orders/<id>/report.html
"""
import html
import json
import os
from datetime import date

from pipeline import rules as RULES
from pipeline import flags as FLAGS
from pipeline import nearby_wells as NW
from pipeline import water_quality as WQ

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_business():
    path = os.path.join(BASE, "config", "business.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _biz(business, key, fallback=""):
    v = business.get(key, fallback)
    return v if v else fallback


def esc(v):
    return html.escape("" if v is None else str(v))


def money(v):
    return f"${v:,.0f}"


CSS = """
:root{
  --ink:#16283a; --muted:#5b6b7f; --line:#e2e8f0;
  --teal:#0f766e; --teal-dk:#115e59; --teal-bg:#ecfdf5;
  --amber:#b45309; --amber-bg:#fffbeb;
  --slate:#0f2233; --card:#ffffff; --page:#f1f5f9;
}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  line-height:1.55;-webkit-font-smoothing:antialiased}
.wrap{max-width:860px;margin:0 auto;padding:28px 20px 60px}
.hero{background:linear-gradient(135deg,#0f2233 0%,#16455b 60%,#0f766e 100%);
  color:#fff;border-radius:18px;padding:34px 32px;margin-bottom:22px;
  box-shadow:0 12px 32px rgba(15,34,51,.25)}
.hero .kicker{font-size:12px;letter-spacing:.14em;text-transform:uppercase;
  opacity:.75;margin-bottom:8px}
.hero h1{margin:0 0 6px;font-size:30px;line-height:1.2;letter-spacing:-.01em}
.hero .addr{font-size:16px;opacity:.92}
.hero .meta{margin-top:16px;font-size:13px;opacity:.75;display:flex;gap:18px;flex-wrap:wrap}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
  padding:24px 26px;margin-bottom:18px;box-shadow:0 2px 10px rgba(15,34,51,.05)}
.card h2{margin:0 0 4px;font-size:18px;letter-spacing:-.01em}
.card .sub{color:var(--muted);font-size:13.5px;margin:0 0 16px}
.verdict{border-left:6px solid var(--teal);background:var(--teal-bg)}
.verdict.caution{border-left-color:var(--amber);background:var(--amber-bg)}
.verdict .word{font-size:24px;font-weight:800;margin:0 0 6px;letter-spacing:-.01em}
.verdict p{margin:6px 0}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:12px;margin-top:16px}
.stat{background:rgba(255,255,255,.75);border:1px solid var(--line);border-radius:10px;
  padding:12px 14px}
.stat .n{font-size:22px;font-weight:800;color:var(--teal-dk)}
.verdict.caution .stat .n{color:var(--amber)}
.stat .l{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.wellbar{margin:10px 0}
.wellbar .top{display:flex;justify-content:space-between;font-size:13.5px;margin-bottom:4px}
.wellbar .top .a{font-weight:600}
.wellbar .top .b{color:var(--muted);white-space:nowrap;margin-left:12px}
.track{height:14px;background:#eef2f7;border-radius:7px;overflow:hidden}
.fill{height:100%;background:linear-gradient(90deg,#14b8a6,#0f766e);border-radius:7px}
.scale{display:flex;justify-content:space-between;font-size:11px;color:var(--muted);margin-top:4px}
.scenarios{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.scenario{border:1px solid var(--line);border-radius:12px;padding:16px}
.scenario .t{font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;
  color:var(--muted);margin-bottom:6px}
.scenario .c{font-size:26px;font-weight:800;letter-spacing:-.01em}
.scenario .m{font-size:12.5px;color:var(--muted);margin-top:6px}
.scenario.hot{border-color:#f59e0b;background:var(--amber-bg)}
ul.flags{margin:0;padding:0;list-style:none}
ul.flags li{padding:10px 0 10px 30px;position:relative;border-bottom:1px dashed var(--line)}
ul.flags li:last-child{border-bottom:none}
ul.flags li::before{content:"!";position:absolute;left:0;top:10px;width:20px;height:20px;
  border-radius:50%;background:var(--amber-bg);color:var(--amber);font-weight:800;
  font-size:13px;text-align:center;line-height:20px;border:1px solid #fcd34d}
.flag{padding:12px 0;border-bottom:1px dashed var(--line)}
.flag:last-child{border-bottom:none}
.flag .ftitle{font-weight:800;font-size:15.5px;margin-bottom:3px}
.flag .fsummary{font-size:14px;color:var(--ink)}
.flag details.fdetail{margin-top:6px;font-size:12.5px;color:var(--muted)}
.flag details.fdetail summary{cursor:pointer;font-weight:600;color:var(--teal-dk)}
.setback{display:flex;gap:12px;padding:10px 0;border-bottom:1px dashed var(--line);font-size:14px}
.setback:last-child{border-bottom:none}
.badge{flex:0 0 auto;font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;
  border-radius:20px;padding:3px 10px;height:fit-content;margin-top:2px}
.badge.ok{background:var(--teal-bg);color:var(--teal-dk);border:1px solid #99f6e4}
.badge.warn{background:var(--amber-bg);color:var(--amber);border:1px solid #fcd34d}
.drillers{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
.driller{border:1px solid var(--line);border-radius:12px;padding:16px}
.driller .co{font-weight:800;font-size:15.5px;margin-bottom:2px}
.driller .tw{color:var(--muted);font-size:13px;margin-bottom:10px}
.driller .ct{font-size:13.5px}
.driller a{color:var(--teal-dk);text-decoration:none;font-weight:600}
.steps{counter-reset:step;margin:0;padding:0;list-style:none}
.steps li{counter-increment:step;padding:10px 0 10px 44px;position:relative;
  border-bottom:1px dashed var(--line)}
.steps li:last-child{border-bottom:none}
.steps li::before{content:counter(step);position:absolute;left:0;top:10px;width:28px;height:28px;
  border-radius:50%;background:var(--slate);color:#fff;font-weight:800;font-size:14px;
  text-align:center;line-height:28px}
.fine{font-size:12.5px;color:var(--muted)}
.fine h3{font-size:13px;color:var(--ink);margin:18px 0 6px}
.footer{text-align:center;color:var(--muted);font-size:12.5px;margin-top:26px}
.footer .bn{font-weight:700;color:var(--ink)}
@media print{
  body{background:#fff}
  .wrap{padding:0;max-width:none}
  .hero{box-shadow:none;-webkit-print-color-adjust:exact;print-color-adjust:exact}
  .card{box-shadow:none;break-inside:avoid}
}
</style>
"""


TRACT_MIN_ACRES = 5.0


def _public_water_verdict(wells, parcel_acres=None):
    """Abandonment signal (2026-10-16, rules package step 4): when the
    record near a parcel holds abandonments instead of new wells,
    report the counts and flag the PHC 19-13-B51m permit bar. The
    counts do NOT establish why the wells were sealed (R21): public
    water is one possible explanation among several, so the copy
    says exactly that and routes the reader to check service
    availability. Returns a verdict tuple or None. Abandonment
    counts are context, never well evidence."""
    ab_r = wells.get("abandonment_records_in_radius") or 0
    ab_t = wells.get("town_abandonment_count") or 0
    wsw_t = wells.get("town_water_supply_count")
    if not (ab_r >= 3 or (ab_t >= 20 and wsw_t == 0)):
        return None
    town = wells.get("stats_town") or wells.get("town") or "this town"
    counts = (f"{ab_r} of the filings near this parcel are well "
              f"abandonments, and town-wide in {town} the record shows "
              f"{ab_t} abandonments against {wsw_t or 0} water-supply wells")
    bar = ("The filings don't say WHY those wells were sealed: "
           "replacement wells, demolitions and public-water hookups "
           "all produce abandonment records. A public main arriving "
           "is one possible explanation, so check whether public "
           "water serves this lot (water company, health district) "
           "before budgeting for a well at all. It matters beyond "
           "convenience: under CT rules (Public Health Code "
           "\u00a719-13-B51m), a well permit can be barred when a "
           "parcel boundary sits within 200 ft of an approved public "
           "water supply.")
    if parcel_acres is not None and parcel_acres >= TRACT_MIN_ACRES:
        return ("caution", "Wells here were sealed: check for a street main before budgeting",
                f"No water-supply wells in the online record (filings since "
                f"2021) for this {parcel_acres:g}-acre tract, but the "
                f"record is not empty: {counts}. {bar} At tract scale that "
                "can change the whole approval picture: public water at "
                "the street may serve the lots, or bar wells lot by lot.")
    return ("caution", "Wells here were sealed: check for a street main before budgeting",
            f"No water-supply wells in the online record (filings since "
            f"2021) near this parcel, but the record is not empty: "
            f"{counts}. {bar}")


def _yield_evidence(wells):
    """Yield evidence counted from the per-well records themselves
    (donor pipeline pattern, Codex R01 fix 2026-10-21).

    Depth and yield are separate evidence: a record with a depth but no
    yield says nothing about production, and a zero yield is a real
    recorded value, not a missing one. Falls back to the aggregate keys
    when no per-well list is present.
    """
    rows = wells.get("wells") or []
    if rows:
        total = wells.get("total_water_supply_wells", len(rows))
        values = [w["yield_gpm"] for w in rows
                  if w.get("yield_gpm") is not None]
        depths = [w["depth_ft"] for w in rows
                  if w.get("depth_ft") is not None]
        return {"total": total, "values": values, "n": len(values),
                "zeros": sum(1 for v in values if v == 0),
                "depth_n": len(depths)}
    y = wells.get("yield_gpm") or {}
    n = wells.get("yield_count")
    if n is None:
        n = 1 if y.get("median") is not None else 0
    values = ([y["min"], y["median"], y["max"]]
              if y.get("median") is not None else [])
    return {"total": wells.get("total_water_supply_wells") or 0,
            "values": values, "n": n,
            "zeros": wells.get("zero_yield_count") or 0,
            "depth_n": wells.get("depth_count") or 0}


def _verdict(wells, parcel_acres=None):
    # R01 (2026-10-21): every branch below is gated on YIELD evidence —
    # the count of records that actually report a yield — never on the
    # depth count. The old code keyed on wells_with_depth_yield (then a
    # depth-only count), so three drilled depths with one yield read as
    # three producing wells, and zero yields read as production.
    ev = _yield_evidence(wells)
    total, n_y, zeros = ev["total"], ev["n"], ev["zeros"]
    y = wells.get("yield_gpm") or {}
    d = wells.get("depth_ft") or {}
    med_y = y.get("median")
    # PR #5 Finding 1 (2026-10-22): the 'good' branches below are
    # gated on yield evidence, which can exist with NO depth
    # statistics at all — the depth parenthetical renders only when
    # depth min/max actually exist, never unconditionally.
    depth_note = ""
    if d.get("min") is not None and d.get("max") is not None:
        depth_note = (f" (depths {d['min']:g} to {d['max']:g} ft "
                      "where recorded)")
    caveat = ("The record only covers wells filed online since 2021, so "
              "treat it as evidence, not a guarantee.")
    counts = (f"{n_y} of {total} nearby record(s) report a yield; "
              f"{total - n_y} have no yield on file.")
    # Borehole storage (2026-10-24, borehole-storage fix Part 1): the
    # standing reserve joins the verdict NOTES in both directions.
    # Only KNOWN storage speaks: with no sane depth + resting-level
    # pair on file, both appends are empty and the notes read exactly
    # as before.
    _st = NW.storage_summary(wells.get("wells") or [])
    # Borehole-storage RE-GATE (2026-10-24, design approved by Marcin
    # as proposed): known storage also gates the verdict CLASS, both
    # directions. Storage is KNOWN for gating only when sane
    # depth+resting-level pairs cover at least half the
    # neighborhood's wells; below that share, storage is unknown for
    # gating and no class moves on it. Thin cap: a would-be "good"
    # drops to caution when the median column is thin (< 50 ft).
    # Deep lift: the complete-evidence caution rises to "good" only
    # with median yield >= 2 gpm AND median storage >= 300 gal. The
    # zero-yield branches return before either gate is reached and
    # are never gated.
    _rows_n = len(wells.get("wells") or [])
    _storage_known = (_rows_n > 0
                      and _st["wells_with_column"] * 2 >= _rows_n)
    _cap_thin = _storage_known and bool(_st["thin"])
    _lift_deep = (_storage_known and med_y is not None and med_y >= 2
                  and _st["median_storage_gal"] is not None
                  and _st["median_storage_gal"] >= 300)
    if _st["wells_with_column"]:
        _gal = _st["median_storage_gal"]
        if _st["thin"]:
            _good_storage = (
                " One caution inside the good news: these wells stand "
                f"shallow (typical standing reserve only about {_gal:g} "
                "gal in the bore), so a long heavy draw can outrun the "
                "well even at these yields: ask drillers about it.")
            _caution_storage = ""
        else:
            _good_storage = (
                " The neighbors' wells also stand deep (typical "
                f"standing reserve about {_gal:g} gal in the bore), so "
                "the reserve behind those yields is real, not just a "
                "strong test day.")
            _caution_storage = (
                " One fact the yield number hides: these wells stand "
                f"deep: a typical standing reserve of about {_gal:g} "
                "gallons in the bore, which can carry a household "
                "through peak use while the well refills. Still a site "
                "question; a driller should confirm it on site.")
    else:
        _good_storage = ""
        _caution_storage = ""
    # Tract framing (2026-10-14, Remy, at Marcin's decision "keep tracts,
    # rewrite the packets for developers"): at >= TRACT_MIN_ACRES the buyer
    # is a developer, not a single-family shopper. The well evidence stays
    # the same, but the verdict must lead with the subdivision/approval
    # question and treat yield as a per-lot question — never "a workable
    # aquifer for a single-family home" on a 15-35 acre parcel.
    if parcel_acres is not None and parcel_acres >= TRACT_MIN_ACRES:
        ac = f"{parcel_acres:g}"
        if zeros:
            return ("caution", "Zero-yield records: approvals and water both open",
                    f"{counts} {zeros} of the yield-bearing record(s) "
                    f"recorded zero yield. On a {ac}-acre tract that is a "
                    "per-lot risk, not a footnote, and before any of it, "
                    "the subdivision question decides what can be built "
                    "here at all. Get the approval picture and driller "
                    "opinions before you commit.")
        if n_y >= 3 and n_y == total and med_y is not None and med_y >= 10:
            if _cap_thin:
                # Re-gate thin cap, tract form: strong yields cannot
                # bless a tract whose per-lot reserve is thin.
                # Approvals still gate the parcel; the cap changes
                # only the water class.
                return ("caution",
                        "Strong yields, thin reserve: approvals "
                        "decide this tract",
                        f"All {n_y} nearby records report yields of "
                        f"{y.get('min'):g} to {y.get('max'):g} gallons per minute"
                        f"{depth_note}: encouraging per-well "
                        "production, but the wells stand shallow: the "
                        "typical standing reserve is only "
                        f"about {_st['median_storage_gal']:g} gal of water in "
                        "the bore, so the reserve behind each lot's "
                        "yield is thin even where the filed number is "
                        f"strong. And this is a {ac}-acre tract, not a "
                        "single homesite: whether water works here gets "
                        "answered lot by lot, and only after the "
                        "subdivision question that actually gates this "
                        "parcel: what can legally be built, how many "
                        "lots, and whether each lot can carry its own "
                        "well and septic. Approvals still decide this "
                        "tract, and the thin per-lot reserve needs "
                        "a driller's answer too. " + caveat)
            return ("good", "Good water evidence: approvals decide this tract",
                    f"All {n_y} nearby records report yields of "
                    f"{y.get('min'):g} to {y.get('max'):g} gallons per minute"
                    f"{depth_note}. That is encouraging per-well evidence"
                    f", but this is a {ac}-acre tract, not a single "
                    "homesite. Whether water works here gets answered lot "
                    "by lot, and only after the question that actually "
                    "gates this parcel: what can legally be built, how "
                    "many lots, and whether each lot can carry its own "
                    "well and septic. Treat the aquifer as one box "
                    "checked; subdivision approval, layout, and per-lot "
                    "septic feasibility decide the deal. " + caveat
                    + _good_storage)
        if (n_y >= 3 and n_y == total and med_y is not None
                and med_y < 10 and _lift_deep):
            # Re-gate deep lift, tract form: complete yield evidence
            # below the good gate on yield alone, raised by a deep
            # standing reserve. Approvals still gate the parcel; the
            # lift changes only the water class.
            return ("good",
                    "Workable water, deep reserve: approvals decide "
                    "this tract",
                    f"{counts} Recorded yields run "
                    f"{min(ev['values']):g} to {max(ev['values']):g} gallons per minute (gpm)"
                    ": low per-well production on the filed "
                    "numbers. But the wells stand deep: the typical "
                    "well holds "
                    f"about {_st['median_storage_gal']:g} gallons of water "
                    "standing in the bore, a reserve that can carry a "
                    "lot through peak use while the well refills at "
                    "the filed rate. The reserve covers peaks; it does "
                    "not raise what a well produces, and sustained "
                    "yield is unestablished from these records: "
                    "each lot still needs a driller's confirmation. "
                    "And before any of it, the subdivision question "
                    f"decides what can be built on this {ac}-acre "
                    "tract at all: approvals, layout, and per-lot "
                    "septic feasibility gate the parcel. " + caveat)
        if total >= 1:
            return ("caution", "Thin water record: approvals come first on a tract",
                    f"{counts} That is thin yield evidence for a "
                    f"{ac}-acre tract: per-lot depth and yield stay "
                    "uncertain, and before any of that, the subdivision "
                    "question decides what can be built here at all. Budget "
                    "for the deep end, and get the approval picture and "
                    "driller opinions before you commit.")
        pw = _public_water_verdict(wells, parcel_acres)
        if pw:
            return pw
        return ("caution", "Nothing on record: approvals first, water unproven",
                "No nearby water-supply wells in the online record (filings "
                f"since 2021) for this {ac}-acre tract. Neighbors may have "
                "older wells on paper at the health district. At tract "
                "scale the gating questions are approvals and layout first, "
                "per-lot water second: budget for the deep end and talk to "
                "the health district and drillers early.")
    if zeros:
        return ("caution", "Zero-yield records: ask drillers and the health district why",
                f"{counts} {zeros} of the yield-bearing record(s) recorded "
                "zero yield: a drilled well that filed no production. "
                "That is a red flag to investigate with drillers and the "
                "health district, not a detail. " + caveat)
    if total == 0:
        pw = _public_water_verdict(wells, parcel_acres)
        if pw:
            return pw
        return ("caution", "No local record to lean on: budget for the deep end",
                "No nearby water-supply wells in the online record (filings "
                "since 2021). Neighbors may have older wells on paper at "
                "the health district. Until then, this parcel is a "
                "research project: budget for the deep end and talk to "
                "drillers early.")
    if n_y == 0:
        return ("caution", "No yield on record: nothing here proves water",
                f"{counts} Depths on file are not production evidence: "
                "with no recorded yield, nothing here shows what a well "
                "at this parcel would deliver. Budget for the deep end "
                "and get driller opinions before you commit.")
    if n_y < 3 or n_y < total:
        if n_y == 1:
            scope = (f"The single recorded yield is "
                     f"{ev['values'][0]:g} gallons per minute (gpm): one record, so no "
                     "range can be drawn from it.")
        else:
            scope = (f"Recorded yields run "
                     f"{min(ev['values']):g} to {max(ev['values']):g} gallons per minute (gpm) "
                     f"across those {n_y} records only.")
        return ("caution", "Not enough records to call it: budget for the deep end",
                f"{counts} {scope} A thin record means wider uncertainty "
                "on depth and yield. Budget for the deep end and get "
                "driller opinions before you commit.")
    # Only complete yield evidence reaches here: >= 3 yield-bearing
    # records, every record reports a yield, and no zero yields.
    if med_y is not None and med_y >= 10:
        if _cap_thin:
            # Re-gate thin cap: yield alone can no longer earn
            # "good" when the standing reserve behind it is thin.
            return ("caution", "Strong flow, thin reserve: ask a driller before you commit",
                    f"All {n_y} nearby records report yields of "
                    f"{y.get('min'):g} to {y.get('max'):g} gallons per minute"
                    f"{depth_note}: strong filed production. But "
                    "these wells stand shallow: the typical standing "
                    f"reserve is only about {_st['median_storage_gal']:g} gal "
                    "of water in the bore, so one long heavy draw can "
                    "outrun the reserve even at these yields. Whether "
                    "that serves a home here turns on the reserve, not "
                    "the test-day number: ask drillers about it "
                    "before you commit. " + caveat)
        return ("good", "Good water outlook",
                f"All {n_y} nearby records report yields of "
                f"{y.get('min'):g} to {y.get('max'):g} gallons per minute"
                f"{depth_note}. That is a workable aquifer for a single-family "
                "home, but " + caveat[0].lower() + caveat[1:]
                + _good_storage)
    if _lift_deep:
        # Re-gate deep lift: complete evidence, low filed yield, but
        # a deep standing reserve that can carry peak use while the
        # well refills. The reserve covers peaks \u2014 it does not
        # raise sustained production, which stays unestablished.
        return ("good",
                "Workable for a house: low flow, deep reserve",
                f"{counts} The typical neighbor well "
                f"holds about {_st['median_storage_gal']:g} gallons of water "
                "standing in the bore: a reserve that can carry a "
                "household through peak use while the well refills. "
                "Recorded yields run "
                f"{min(ev['values']):g} to {max(ev['values']):g} gallons "
                "per minute (gpm): low on the filed numbers, so on "
                "yield alone this neighborhood would stay a caution. "
                "The reserve covers peaks; it does not "
                "raise what the well produces, and sustained yield is "
                "unestablished from these records: a driller "
                "should confirm it on site. " + caveat)
    return ("caution", "Water on record, amount unproven: get driller opinions before you commit",
            f"{counts} Recorded yields run "
            f"{min(ev['values']):g} to {max(ev['values']):g} gallons per minute (gpm). Whether "
            "that serves a home here is a site question: budget for "
            "the deep end and get driller opinions before you commit. "
            + caveat + _caution_storage)


def _verdict_full(wells, parcel_acres=None):
    """_verdict + the gate metadata renderers need (CX pass,
    2026-10-29): whether this verdict is the borehole-storage DEEP
    LIFT (complete evidence, median yield 2\u2013<10 gpm, median storage
    >= 300 gal, class good) and the median storage behind it. On a
    lifted verdict the snapshot/report stats rows lead with the
    standing reserve instead of the median yield the verdict exists
    to overrule (audit Finding 4). The conditions mirror the lift
    branches inside _verdict exactly; class must be "good", which
    only the lift can produce at this yield band."""
    cls, word, note = _verdict(wells, parcel_acres)
    st = NW.storage_summary(wells.get("wells") or [])
    rows_n = len(wells.get("wells") or [])
    known = rows_n > 0 and st["wells_with_column"] * 2 >= rows_n
    med_y = (wells.get("yield_gpm") or {}).get("median")
    lift = bool(known and med_y is not None and 2 <= med_y < 10
                and st["median_storage_gal"] is not None
                and st["median_storage_gal"] >= 300 and cls == "good")
    return cls, word, note, {"storage_lift": lift,
                             "median_storage_gal": st["median_storage_gal"]}


def _stats_html(n_wells, wells, meta, include_range=False):
    """Verdict-card stats row, shared by the report and the snapshot
    (CX pass, 2026-10-29). On a storage-lifted verdict the standing
    reserve LEADS the row and the median yield is demoted to a later
    stat, not deleted (audit Finding 4: the row used to headline the
    very yield number the verdict exists to overrule)."""
    d = wells.get("depth_ft") or {}
    med_y = (wells.get("yield_gpm") or {}).get("median")
    parts = []
    if meta.get("storage_lift") and meta.get("median_storage_gal") is not None:
        parts.append(
            f'<div class="stat"><div class="n">about {meta["median_storage_gal"]:g} gal</div>'
            '<div class="l">standing reserve in the bore</div></div>')
    parts.append(
        f'<div class="stat"><div class="n">{n_wells}</div>'
        '<div class="l">nearby wells on record</div></div>')
    parts.append(
        f'<div class="stat"><div class="n">{d.get("median"):g} ft</div>'
        '<div class="l">median depth</div></div>' if d.get("median") else "")
    parts.append(
        f'<div class="stat"><div class="n">{med_y:g} gpm</div>'
        '<div class="l">median yield</div></div>' if med_y is not None else "")
    if include_range:
        parts.append(
            f'<div class="stat"><div class="n">{d.get("min"):g} to {d.get("max"):g} ft</div>'
            '<div class="l">local depth range</div></div>' if d.get("min") else "")
    return "\n".join(parts)


def _depth_bars(wells):
    # Ported from the donor pipeline (Codex R01 fix, 2026-10-21): when
    # every depth is unknown there is no scale — never draw bars against
    # an invented 1-ft maximum; render the unknown honestly instead.
    items = wells.get("wells", [])
    if not items:
        return '<p class="fine">No depth records in the online dataset for this radius.</p>'
    depths = [w.get("depth_ft") for w in items if w.get("depth_ft")]
    mx = max(depths) if depths else None
    out = []
    for w in items:
        dep = w.get("depth_ft")
        pct = (dep / mx * 100) if (dep and mx) else 0
        yld = w.get("yield_gpm")
        ytxt = f"{yld:g} gpm" if yld is not None else "yield n/a"
        dtxt = f"{dep:g} ft" if dep is not None else "depth n/a"
        out.append(
            f'<div class="wellbar"><div class="top"><span class="a">{esc(w.get("address"))}'
            f'</span><span class="b">{dtxt} &middot; {ytxt}</span></div>'
            f'<div class="track"><div class="fill" style="width:{pct:.1f}%"></div></div></div>'
        )
    if mx:
        out.append(
            f'<div class="scale"><span>0 ft</span><span>deepest nearby: {mx:g} ft</span></div>'
        )
    else:
        out.append('<p class="fine">No usable depth values in these records, so no depth scale is shown.</p>')
    return "\n".join(out)


def cost_band(wells):
    """Structured planning cost band from the local depth range.

    Single source of truth for the numbers behind _cost_section and the
    machine-readable snapshot JSON. Returns None when depth data is thin.
    """
    d = wells.get("depth_ft") or {}
    if not (d.get("min") and d.get("median") and d.get("max")):
        return None
    lo, med, hi = d["min"], d["median"], d["max"]
    # Deep-end anchor: tiny local samples (2-4 wells) miss the deep tail, so
    # the worst case prices max(local max, town p90) instead of local max
    # alone. town_depth_ft comes from the same CT dataset (driller-reported
    # town, matched case-insensitively).
    deep, deep_basis = hi, "deep end of the local range"
    t = wells.get("town_depth_ft") or {}
    if t.get("p90") and t["p90"] > hi:
        deep = t["p90"]
        # The p90 is the STATS town's number (R16) — label it with the
        # town the records actually came from, not the property's town.
        town = wells.get("stats_town") or wells.get("town") or "town"
        deep_basis = (f"{town} p90 depth ({t['p90']:g} ft across "
                      f"{t.get('n', '?')} town records): the local sample "
                      f"was too small to see the deep tail")
    # Rates, contingency, and permit figures are rules-as-data
    # (cost-source pass, 2026-11-04): the rules table carries each
    # number's citation, scope ("drilling and casing" — NOT the
    # complete-system scope the published $25-$65 range is quoted
    # under), confidence, and verified date. A missing entry raises
    # inside rules.cost_entry / rules.fee_amount — the band never
    # falls back to a literal.
    low_e = RULES.cost_entry("cooperates")
    plan_e = RULES.cost_entry("plan_on")
    high_e = RULES.cost_entry("fights_you")
    cont_e = RULES.cost_entry("contingency")
    lo_rate = low_e.get("rate_usd_per_ft")
    med_rate = plan_e.get("rate_usd_per_ft")
    hi_rate = high_e.get("rate_usd_per_ft")
    if None in (lo_rate, med_rate, hi_rate):
        raise RuntimeError(
            "rules_ct.json cost rate entry missing rate_usd_per_ft")
    cont = cont_e.get("amount_range_usd")
    if not cont or len(cont) != 2:
        raise RuntimeError(
            "rules_ct.json cost contingency entry missing amount_range_usd")
    permit_fee = RULES.fee_amount("ct_fee_well_permit_ncdhd")
    bloom_fee = RULES.fee_amount("ct_fee_well_only_whbhd")
    return {
        "scope": plan_e["scope"],
        "sources": [low_e["citation"], plan_e["citation"],
                    high_e["citation"]],
        "method": ("depth_ft x per-foot planning rate for drilling and "
                   "casing; rates are planning points from published "
                   "national cost guides (Angi, CostHelper, National "
                   "Ground Water Association Well Owners Guide, "
                   "DrillerDB): planning context only, driller "
                   "quotes are the real numbers"),
        "scenarios": [
            {"label": "If the geology cooperates", "depth_ft": lo,
             "rate_usd_per_ft": lo_rate,
             "estimate_usd": round(lo * lo_rate),
             "basis": "shallow end of the local range",
             "source": low_e["citation"],
             "confidence": low_e["confidence"]},
            {"label": "Plan on", "depth_ft": med,
             "rate_usd_per_ft": med_rate,
             "estimate_usd": round(med * med_rate),
             "basis": "middle of the local range",
             "source": plan_e["citation"],
             "confidence": plan_e["confidence"]},
            {"label": "If it fights you", "depth_ft": deep,
             "rate_usd_per_ft": hi_rate,
             "estimate_usd": round(deep * hi_rate),
             "open_ended": True,
             "basis": f"{deep_basis}, before the dry-hole downside",
             "source": high_e["citation"],
             "confidence": high_e["confidence"]},
        ],
        "excludes": ["pump", "pressure tank", "electrical", "trenching",
                     (f"well permit (set by your health district: "
                      f"${permit_fee} in the North Central District, "
                      f"which covers East Windsor, Enfield, Ellington, "
                      f"Suffield, Vernon, and Windsor Locks; "
                      f"${bloom_fee} in Bloomfield; check your "
                      f"district's current fee schedule)")],
        "dry_hole_contingency_usd": list(cont),
        "dry_hole_note": ("You pay for the attempt: drillers bill per "
                          "foot whether or not the hole produces, so a "
                          "failed hole at the depths common around here "
                          "can run about $9,000 to $18,000 at the "
                          "plan-on rate, before you decide whether to "
                          "deepen it, hydrofracture it, or drill again "
                          "somewhere else. Carry a "
                          f"${cont[0]:,} to ${cont[1]:,} contingency "
                          "for that: our planning allowance, built "
                          "from the rates above, not a published figure."),
    }


def _cost_section(wells):
    band = cost_band(wells)
    if band is None:
        return ("<p>Not enough local depth records to build a planning band: "
                "get three driller quotes and ask each what depth they would "
                "permit and drill to on this lot.</p>")
    sc = band["scenarios"]
    cards = "\n".join([
        f'<div class="scenario"><div class="t">{esc(sc[0]["label"])}</div>'
        f'<div class="c">{money(sc[0]["estimate_usd"])}</div>'
        f'<div class="m">{sc[0]["depth_ft"]:g} ft &times; about ${sc[0]["rate_usd_per_ft"]}/ft: {esc(sc[0]["basis"])}</div></div>',
        f'<div class="scenario"><div class="t">{esc(sc[1]["label"])}</div>'
        f'<div class="c">{money(sc[1]["estimate_usd"])}</div>'
        f'<div class="m">{sc[1]["depth_ft"]:g} ft &times; about ${sc[1]["rate_usd_per_ft"]}/ft: {esc(sc[1]["basis"])}</div></div>',
        f'<div class="scenario hot"><div class="t">{esc(sc[2]["label"])}</div>'
        f'<div class="c">{money(sc[2]["estimate_usd"])}+</div>'
        f'<div class="m">{sc[2]["depth_ft"]:g} ft &times; about ${sc[2]["rate_usd_per_ft"]}/ft: {esc(sc[2]["basis"])}</div></div>',
    ])
    permit_fee = RULES.fee_amount("ct_fee_well_permit_ncdhd")
    bloom_fee = RULES.fee_amount("ct_fee_well_only_whbhd")
    cont_lo, cont_hi = band["dry_hole_contingency_usd"]
    rates_txt = (f"${sc[0]['rate_usd_per_ft']}, "
                 f"${sc[1]['rate_usd_per_ft']}, and "
                 f"${sc[2]['rate_usd_per_ft']}")
    return f"""
<div class="scenarios">{cards}</div>
<p class="fine" style="margin-top:14px"><b>How we got these numbers:</b> the
per-foot rates are planning points picked from published national cost guides,
not quotes. Angi (updated September 18, 2026) puts residential well drilling
at $25 to $65 per foot, and $50 to $75 per foot in rocky ground. CostHelper
puts drilling with casing at $15 to $30 per foot, rising to $30 to $50 or more
in difficult ground. The National Ground Water Association's Well Owners Guide
reports drilling-only averages of about $24 per foot for a 4-inch well and
about $30 per foot for a 6-inch well, with casing billed on top of that. We
plan at {rates_txt} per foot for drilling and casing. The pump, pressure tank,
electrical work, and trenching are not in these numbers; drillers quote those
separately. Your driller's written quote is the real number.</p>
<p class="fine">The hole is the middle of the bill: add pump, pressure tank,
electrical, and trenching (quoted separately), plus the well permit (set by
your health district: ${permit_fee} in the North Central District, which
covers East Windsor, Enfield, Ellington, Suffield, Vernon, and Windsor Locks;
${bloom_fee} in Bloomfield; check your district's current fee schedule).
<b>Dry-hole truth:</b> one risk no guide can price for your lot: you pay for
the attempt. Drillers bill per foot whether or not the hole produces, so a
failed hole at the depths common around here can run about $9,000 to $18,000
at the plan-on rate, before you decide whether to deepen it, hydrofracture
it, or drill again somewhere else. We carry a <b>+${cont_lo:,} to ${cont_hi:,}
contingency</b> for that. It is our planning allowance, built from the rates
above, not a published figure. Your three driller quotes are the real
numbers; this band is what you walk in with.</p>
"""


def setback_number_phrase(it) -> str:
    """The number beside a setback rule, honestly labeled (R18):
    guidance is guidance — never a 'minimum' — and every other
    number keeps its condition attached at the detail line below."""
    ft = esc(it.get("minimum_ft"))
    if (it.get("confidence") or "").upper() == "GUIDANCE":
        return (f"{ft} ft: district guidance "
                f"('if possible'), not a state minimum")
    return f"{ft} ft minimum"


def setback_detail(it) -> str:
    """Condition, scaling and provenance for one setback item —
    the fields the HTML helpers used to drop (R18)."""
    bits = []
    if it.get("condition"):
        bits.append(esc(it["condition"]))
    if it.get("scales"):
        bits.append(esc(it["scales"]))
    src = []
    if it.get("source"):
        src.append(f"Source: {esc(it['source'])}")
    if it.get("confidence"):
        # Plain words, never the bare table code (CX residual fix,
        # 2026-10-29): rules.confidence_plain is the one mapping
        # every customer surface shares.
        src.append(f"Confidence: {esc(RULES.confidence_plain(it['confidence']))}")
    if src:
        bits.append(" ".join(src))
    return " ".join(bits)


def _setback_rows(setbacks):
    out = []
    for it in setbacks.get("items", []):
        status = (it.get("status") or "").lower()
        badge = ("warn", "to confirm") if "verif" in status or "field" in status else ("ok", esc(it.get("status")))
        out.append(
            f'<div class="setback"><span class="badge {badge[0]}">{badge[1]}</span>'
            f'<div><b>{esc(it.get("rule"))}</b>: {setback_number_phrase(it)}'
            f'<div class="fine">{setback_detail(it)}</div>'
            f'<div class="fine">{esc(it.get("finding") or "")}</div></div></div>'
        )
    return "\n".join(out) or '<p class="fine">No setback data.</p>'


def _driller_cards(ranked):
    ds = (ranked or {}).get("drillers", [])
    if not ds:
        return '<p class="fine">No driller shortlist for this area yet.</p>'
    cards = []
    for dr in ds:
        phone = esc(dr.get("phone") or "")
        web = esc(dr.get("website") or "")
        contact = []
        if phone:
            contact.append(f'<a href="tel:{phone}">{phone}</a>')
        if web:
            contact.append(f'<a href="{web}">website</a>')
        cards.append(
            f'<div class="driller"><div class="co">{esc(dr.get("company"))}</div>'
            f'<div class="tw">{esc(dr.get("town"))} &middot; {dr.get("distance_miles")} mi</div>'
            f'<div class="ct">{" &middot; ".join(contact) if contact else "contact via directory"}</div></div>'
        )
    return "\n".join(cards)


def build_report(order, geo, wells, wq, setbacks, ranked, order_dir) -> str:
    business = load_business()
    c = order["customer"]
    today = date.today().isoformat()
    pname = _biz(business, "product_name", "Property Well Report")

    vcls, vword, vnote, vmeta = _verdict_full(wells, order.get("parcel_acres"))
    # B-13: flags are _flag() dicts — format them to finished copy via
    # the shared formatter, never interpolate the raw dict.
    wq_flags = "\n".join(f"<li>{esc(WQ.format_flag(f))}</li>"
                         for f in wq.get("flags", []))
    d = wells.get("depth_ft") or {}
    # R01: the "on record" stat is the total record count; the old code
    # showed the depth-yield count under that label. Median checks use
    # `is not None` so a recorded 0 gpm median is shown, not hidden.
    n_wells = (wells.get("total_water_supply_wells")
               or wells.get("wells_with_depth_yield") or 0)

    stats = _stats_html(n_wells, wells, vmeta, include_range=True)

    footer_bits = [
        _biz(business, "legal_name"), _biz(business, "address"),
        _biz(business, "email"), _biz(business, "phone"),
    ]
    footer_line = " &middot; ".join(esc(b) for b in footer_bits if b and not b.startswith("YOUR"))

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(pname)}: {esc(order["property_address"])}</title>
<style>{CSS}
</head>
<body>
<div class="wrap">

<div class="hero">
  <div class="kicker">{esc(pname)}</div>
  <h1>{esc(order["property_address"])}</h1>
  <div class="addr">{esc(_biz(business, "tagline", ""))}</div>
  <div class="meta"><span>Prepared for {esc(c["name"])}</span><span>{today}</span><span>Order {esc(order["order_id"])}</span></div>
</div>

<div class="card verdict {vcls}">
  <div class="word">{vword}</div>
  <p>{vnote}</p>
  <div class="stats">{stats}</div>
</div>

<div class="card">
  <h2>Nearby well evidence</h2>
  <p class="sub">Public completion reports near this parcel: {esc(wells.get("source", ""))}</p>
  {_depth_bars(wells)}
  <p class="fine" style="margin-top:12px"><b>Reading this:</b> budget against the
  geology, not the average. Wells were drilled by: {esc(", ".join(sorted({w.get("company") for w in wells.get("wells", []) if w.get("company")}))) or "n/a"}.</p>
</div>

<div class="card">
  <h2>What it will cost to plan for</h2>
  <p class="sub">Planning band from the local depth range: your driller quotes are the real numbers</p>
  {_cost_section(wells)}
</div>

<div class="card">
  <h2>Water-quality flags</h2>
  <p class="sub">{esc(wq.get("source", ""))}: {esc(WQ._clean_legacy(wq.get("note") or ""))}</p>
  <ul class="flags">{wq_flags}</ul>
  <p class="fine" style="margin-top:12px"><b>{esc(wq.get("final_word", ""))}</b></p>
</div>

<div class="card">
  <h2>Setback checklist</h2>
  <p class="sub">Setbacks are the legal distance a well must sit from septic and water: CT rules; your engineer confirms each one for your lot</p>
  {_setback_rows(setbacks)}
  <p class="fine" style="margin-top:12px">{esc(setbacks.get("confidence_note", ""))}</p>
  <p class="fine">{esc(RULES.doubling_caveat())}</p>
</div>

<div class="card">
  <h2>Who to call</h2>
  <p class="sub">Drillers active near this parcel, ranked by proximity: call two or three for competing quotes</p>
  <div class="drillers">{_driller_cards(ranked)}</div>
</div>

<div class="card">
  <h2>Your next steps</h2>
  <ol class="steps">
    <li><b>Decide with eyes open.</b> If the outlook above works for your budget including the contingency, this lot is drillable on paper.</li>
    <li><b>Get three quotes.</b> Ask each driller: what depth would you permit for, what's your per-foot rate, and what happens, exactly, if the hole is dry or low-yield?</li>
    <li><b>Plan the lab test now.</b> Connecticut requires arsenic and uranium testing for new wells: confirm the panel with a certified lab, and ask whether radon makes sense for your lot.</li>
    <li><b>File with the health district.</b> Your driller signs the state permit as applicant; the health director countersigns before it is valid.</li>
  </ol>
</div>

<div class="card">
  <h2>Methodology &amp; honesty section</h2>
  <div class="fine">
  <h3>Where every number came from</h3>
  <p>Coordinates: {esc(geo.get("source", ""))} ({geo.get("lat"):.5f}, {geo.get("lon"):.5f}).
  Well records: {esc(wells.get("source", ""))}: online submissions only, so
  older wells exist on paper at the health district that we cannot see.
  Water quality: {esc(wq.get("source", ""))}: regional sample rows, not a
  parcel prediction. Setbacks: CT rules from a single rules table keyed by
  town and district (PHC 19-13-B51, CT DPH Technical Standards, and district
  publications), each rule carrying its citation and confidence label: the health district has the
  final word on your lot.</p>
  <h3>What this report is not</h3>
  <p>{esc(_biz(business, "report_disclaimer"))} Not legal advice. Not an
  engineering stamp. Nobody was contacted on your behalf for this report.</p>
  </div>
</div>

<div class="footer">
  <div class="bn">{esc(_biz(business, "legal_name", "YOUR BUSINESS LEGAL NAME"))}</div>
  <div>{footer_line}</div>
  <div style="margin-top:6px">Coverage: {esc(_biz(business, "coverage", "Connecticut"))} &middot; Generated {today}</div>
</div>

</div>
</body>
</html>
"""
    path = os.path.join(order_dir, "report.html")
    with open(path, "w") as f:
        f.write(html_doc)
    return path


def build_delivery_html(order, wells, wq) -> str:
    """Designed HTML delivery email (inline styles, Gmail-safe).

    Same numbers as the snapshot — _verdict() and cost_band() are the single
    source. Brand-first: header links the product name to the site.
    """
    business = load_business()
    site_url = _biz(business, "website", "https://ineedawell.com")
    site_host = site_url.replace("https://", "").replace("http://", "").rstrip("/")
    c = order.get("customer", {})
    first = ((c.get("name") or "").split() or ["there"])[0]

    vcls, vword, vnote = _verdict(wells, order.get("parcel_acres"))
    accent = "#b45309" if vcls == "caution" else "#0f766e"

    band = cost_band(wells)
    if band:
        scen_rows = "".join(
            f'<tr><td style="padding:8px 0;border-bottom:1px solid #eef2f7;'
            f'font-size:15px;color:#16283a">{esc(s["label"])}</td>'
            f'<td style="padding:8px 0;border-bottom:1px solid #eef2f7;'
            f'font-size:15px;font-weight:800;color:#16283a;text-align:right;'
            f'white-space:nowrap">${s["estimate_usd"]:,}</td></tr>'
            for s in band["scenarios"]
        )
        lo, hi = band["dry_hole_contingency_usd"]
        cost_html = (
            f'<table width="100%" cellpadding="0" cellspacing="0" style="margin:6px 0 4px">'
            f'{scen_rows}</table>'
            f'<p style="margin:8px 0 0;font-size:14px;color:#5b6b7f">'
            f'Dry-hole contingency to carry: <b style="color:#16283a">+${lo:,} to ${hi:,}</b> '
            f': you pay for the attempt even if it\'s dry.</p>'
            f'<p style="margin:6px 0 0;font-size:13px;color:#5b6b7f">'
            f'Planning band, not a quote: your driller quotes are the real numbers.</p>'
        )
    else:
        cost_html = ('<p style="margin:4px 0 0;font-size:15px;color:#16283a">'
                     'Not enough nearby depth records to price: '
                     'the snapshot explains why.</p>')

    def _flag_row(f):
        if isinstance(f, dict):
            f = WQ.clean_flag(f)
            return (
                f'<tr><td style="padding:8px 0;border-bottom:1px solid #eef2f7;'
                f'vertical-align:top"><span style="font-size:15px;font-weight:800;'
                f'color:#16283a">{esc(f.get("title", ""))}</span><br>'
                f'<span style="font-size:14px;color:#5b6b7f">{esc(f.get("summary", ""))}</span></td></tr>'
            )
        return (f'<tr><td style="padding:8px 0;border-bottom:1px solid #eef2f7;'
                f'font-size:14px;color:#5b6b7f">{esc(WQ.format_flag(f))}</td></tr>')
    wq_rows = "".join(_flag_row(f) for f in (wq.get("flags") or []))

    # $39 dossier-intent favor: parked 2026-10-24 (FLAGS module). While
    # parked the email asks the one feedback favor; flip the flag and
    # favor #2 (with the "Two favors" heading) returns exactly as before.
    if FLAGS.DOSSIER_INTENT_ENABLED:
        favors_heading = "Two favors"
        favor2_html = (
            '<br>\n<b style="color:#16283a">2.</b> An honest YES or NO: '
            'if you were looking at another lot next month, would you pay '
            '$39 for the full report: deeper well evidence, a '
            'ranked driller shortlist for the parcel, the full setback '
            'workup, and a print-ready summary? No sales pitch coming '
            'either way; it just tells me whether to build it.')
    else:
        favors_heading = "One favor"
        favor2_html = ""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#eef2f7;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#eef2f7;padding:24px 12px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border-radius:14px;overflow:hidden;">

<tr><td style="background:#0f2233;padding:28px 30px 24px;">
<p style="margin:0 0 10px;font-size:12px;letter-spacing:.14em;color:#7dd3c8;font-weight:700;">
<a href="{esc(site_url)}" style="color:#7dd3c8;text-decoration:none;">I NEED A WELL</a></p>
<h1 style="margin:0 0 6px;font-size:24px;color:#ffffff;font-weight:800;">Your free well snapshot</h1>
<p style="margin:0;font-size:15px;color:#cbd5e1;">{esc(order["property_address"])}</p>
</td></tr>

<tr><td style="padding:24px 30px 6px;">
<p style="margin:0 0 14px;font-size:15px;color:#16283a;line-height:1.55;">
Hi {esc(first)}, Marcin here, from <a href="{esc(site_url)}" style="color:#0f766e;font-weight:700;">I Need a Well</a>.
Here's the 60-second answer so you don't even have to open the attachment.</p>
</td></tr>

<tr><td style="padding:6px 30px;">
<table width="100%" cellpadding="0" cellspacing="0" style="border-left:5px solid {accent};background:#f8fafc;border-radius:0 10px 10px 0;">
<tr><td style="padding:16px 18px;">
<p style="margin:0 0 6px;font-size:19px;font-weight:800;color:#16283a;">{esc(vword)}</p>
<p style="margin:0;font-size:14.5px;color:#334155;line-height:1.55;">{esc(vnote)}</p>
</td></tr></table>
</td></tr>

<tr><td style="padding:18px 30px 6px;">
<p style="margin:0 0 4px;font-size:17px;font-weight:800;color:#16283a;">What to budget</p>
{cost_html}
</td></tr>

<tr><td style="padding:18px 30px 6px;">
<p style="margin:0 0 2px;font-size:17px;font-weight:800;color:#16283a;">Water: what to test</p>
<p style="margin:0 0 6px;font-size:13.5px;color:#5b6b7f;">Required on every new CT well: arsenic and uranium. Radon is your call.</p>
<table width="100%" cellpadding="0" cellspacing="0">{wq_rows}</table>
</td></tr>

<tr><td style="padding:18px 30px 6px;">
<p style="margin:0 0 4px;font-size:17px;font-weight:800;color:#16283a;">{favors_heading}</p>
<p style="margin:0 0 8px;font-size:14.5px;color:#334155;line-height:1.55;">
<b style="color:#16283a">1.</b> Reply and tell me what was most useful and what was missing. That decides what gets built next.{favor2_html}</p>
</td></tr>

<tr><td style="padding:10px 30px 26px;">
<p style="margin:0 0 12px;font-size:14.5px;color:#334155;">And whether you reply or not: thanks for reading. That's what keeps this free.</p>
<p style="margin:0;font-size:14.5px;color:#16283a;">Marcin<br>
<a href="{esc(site_url)}" style="color:#0f766e;font-weight:700;">I Need a Well</a>
<span style="color:#5b6b7f;">&middot; free pilot</span></p>
</td></tr>

</table>
<p style="margin:14px 0 0;font-size:12px;color:#94a3b8;">Free pilot &middot; Connecticut &middot; Nobody was contacted on your behalf.</p>
</td></tr></table>
</body></html>"""
