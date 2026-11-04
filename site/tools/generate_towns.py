#!/usr/bin/env python3
"""Generate the AI-citable town pages for the I Need a Well pilot site.

One template, per-town data below. No per-town statistics are invented:
town-specific numbers appear only where a workspace source backs them
(East Windsor dry run); everything else is labeled statewide CT data, and
pages say so explicitly.
"""
import os

SITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(SITE, "towns")
os.makedirs(OUT, exist_ok=True)

# district = well-permit / environmental-health authority for the town.
# NCDHD membership verified via ncdhd.org (member-towns page); the other four
# appear on NCDHD's own area-health-departments listing as separate entities.
TOWNS = [
    dict(slug="east-windsor", name="East Windsor", county="Hartford County",
         district="the North Central District Health Department (NCDHD)",
         blurb=("Home turf for this pilot — run out of Broad Brook (06016). The area's "
                 "public well records are the ones we've worked with most."),
         local_facts=[
             ("Dry-run findings, Broad Brook area",
              "25 wells within 5 km of our test point, 14 with usable records — "
              "typical depths 140–425 ft at 8–30 gallons per minute (gpm).",
              "Source: project dry run #1, East Windsor/Broad Brook, 2026-09-18."),
             ("A recent East Windsor listing we screened",
              "7 nearby wells within about 2 miles: 223–663 ft deep, 0.5–40 gpm yields.",
              "Source: 193 Wells Rd permit-packet test run, 2026-09-18."),
         ]),
    dict(slug="suffield", name="Suffield", county="Hartford County",
         district="the North Central District Health Department (NCDHD)",
         blurb=("North of the river, lots of acreage on private wells. Well permits here "
                 "run through the district, not the town hall."), local_facts=[]),
    dict(slug="enfield", name="Enfield", county="Hartford County",
         district="the North Central District Health Department (NCDHD)",
         blurb=("NCDHD's home base. Plenty of Enfield parcels sit just outside the water "
                 "lines — the snapshot tells you what the well side looks like."),
         local_facts=[]),
    dict(slug="windsor", name="Windsor", county="Hartford County",
         district="the Windsor Health Department",
         blurb=("Older housing stock meets new construction on well water — the records "
                 "near your specific lot matter more than the town average."),
         local_facts=[]),
    dict(slug="windsor-locks", name="Windsor Locks", county="Hartford County",
         district="the North Central District Health Department (NCDHD)",
         blurb=("Small town, and well records thin out fast at the edges. Request for "
                 "your exact address and we'll pull what's actually nearby."),
         local_facts=[]),
    dict(slug="bloomfield", name="Bloomfield", county="Hartford County",
         district="the West Hartford–Bloomfield Health District",
         blurb=("West-side lots with real variation in depth and yield. Town-wide "
                 "averages would lie to you — the snapshot works lot by lot."),
         local_facts=[]),
    dict(slug="south-windsor", name="South Windsor", county="Hartford County",
         district="the South Windsor Health Department",
         blurb=("Subdivisions and farmland side by side. If the listing says private "
                 "well required, the snapshot is built for that parcel, not the town."),
         local_facts=[]),
    dict(slug="ellington", name="Ellington", county="Tolland County",
         district="the North Central District Health Department (NCDHD)",
         blurb=("Rural routes and long driveways — classic private-well country. "
                 "Permits and well records route through the district."),
         local_facts=[]),
    dict(slug="vernon", name="Vernon", county="Tolland County",
         district="the North Central District Health Department (NCDHD)",
         blurb=("Rockville's old mill housing through Vernon's newer subdivisions — "
                 "water comes from wells on plenty of these streets."),
         local_facts=[]),
    dict(slug="manchester", name="Manchester", county="Hartford County",
         district="the Manchester Health Department",
         blurb=("Mostly on public water, but the outskirts aren't. If your listing "
                 "says well, don't assume — get the snapshot."),
         local_facts=[]),
]

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Free Well Snapshot for {name}, CT | I Need a Well</title>
<meta name="description" content="Looking at a {name}, Connecticut property that needs a private well? Get a free well snapshot: nearby well records, an honest cost band, the dry-hole truth, and next steps — by email within 2 business days.">
<link rel="stylesheet" href="../styles.css">
<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "Service",
  "name": "I Need a Well — Free Well Snapshot ({name}, CT)",
  "serviceType": "Residential water well feasibility snapshot",
  "description": "Free pilot for Connecticut property buyers: verdict on well feasibility for a specific {name} address, nearby well depth/yield records from public CT data, planning cost band, dry-hole contingency, water-quality testing flags, setback top-lines, and next steps — delivered by email within 2 business days.",
  "provider": {{ "@type": "Organization", "name": "I Need a Well (pilot)",
    "address": {{ "@type": "PostalAddress", "addressLocality": "East Windsor", "addressRegion": "CT", "addressCountry": "US" }} }},
  "areaServed": {{ "@type": "City", "name": "{name}",
    "containedInPlace": {{ "@type": "State", "name": "Connecticut" }} }},
  "offers": {{ "@type": "Offer", "price": "0", "priceCurrency": "USD",
    "description": "Free pilot — first 25 snapshots" }}
}}
</script>
</head>
<body>
<nav class="nav">
  <div class="wrap">
    <a class="brand" href="../index.html">I Need a Well<span class="dot">.</span></a>
    <div class="nav-links">
      <a href="../index.html#what-you-get">What you get</a>
      <a href="../index.html#towns">Towns</a>
      <a href="../index.html#faq">FAQ</a>
    </div>
    <a class="btn small" href="../index.html#request">Get my free snapshot</a>
  </div>
</nav>

<header class="town-hero">
  <div class="wrap">
    <p class="kicker">Free pilot · Connecticut only</p>
    <h1>Free well snapshot for {name}, CT.</h1>
    <p>Looking at a {name} property where the listing says &ldquo;private well required&rdquo;?
    Send the address and get a straight answer by email within 2 business days: what nearby
    wells actually hit, what a well will probably cost, and the dry-hole truth nobody puts
    in the listing. {blurb}</p>
  </div>
</header>

<section class="block">
  <div class="wrap">
    <p class="eyebrow">What the snapshot covers for {name}</p>
    <h2 class="sec">Built for your lot, not the town average.</h2>
    <div class="district-note">
      <b>Permits:</b> new private wells in {name} are permitted through {district}.
      A well permit typically runs about $200 via the health district &mdash; verify with the
      district when the time comes.
    </div>
{local_facts}
    <div class="factbox">
      <p><b>What we can say statewide (not {name}-specific):</b> Connecticut&rsquo;s open
      well-completion dataset holds 8,205 reports &mdash; online submissions since January 2021.
      Older records are fragmented across towns, health districts, and unscanned files. Planning
      cost rates run $25&ndash;$65/ft (2026 national guide ranges, planning context only), and
      every snapshot carries a +$10,000&ndash;$25,000 dry-hole contingency, because the standard
      driller contract bills the attempt whether the hole comes up wet or dry.</p>
      <span class="src">Sources: CT DCP open well data; 2026 published national guide ranges.</span>
    </div>
    <div class="factbox">
      <p><b>Water quality:</b> Connecticut requires arsenic and uranium testing for newly
      constructed private and semipublic wells (CT DPH, effective October 1, 2022). The snapshot
      flags what your area&rsquo;s geology suggests testing for &mdash; a lab test on your own
      well still has the final word.</p>
    </div>
    <div class="factbox">
      <p><b>Setbacks (CT top-lines):</b> the legal distance a well must sit from septic
      and water &mdash; 75 ft well-to-septic at under 10 gallons per minute (gpm) (the band
      follows the well's required withdrawal rate, not its tested yield),
      25 ft from a watercourse. Your snapshot lists the top-lines to
      confirm for your lot with the health district.</p>
    </div>
    <p style="color:var(--ink-soft)">We don&rsquo;t publish town-wide averages &mdash; depth and
    yield vary lot by lot even within {name}. Request a snapshot for your exact address and we
    pull the records around it.</p>
  </div>
</section>

<section class="block tint">
  <div class="wrap">
    <div class="cta-band">
      <h2>Get your {name} snapshot.</h2>
      <p>Free pilot &mdash; first 25 snapshots. Email, property address. In your inbox within 2 business days. We contact nobody.</p>
      <a class="btn" href="../index.html#request">Request my free snapshot →</a>
    </div>
    <div class="siblings">
      <span style="align-self:center;font-weight:700;color:var(--ink-soft)">Other towns:</span>
{siblings}
    </div>
  </div>
</section>

<footer>
  <div class="wrap">
    <div>
      <h4>I Need a Well<span style="color:var(--amber)">.</span></h4>
      <p>A free pilot from East Windsor, Connecticut.</p>
      <p class="fine">Planning estimates only &mdash; not engineering, legal, or financial advice. Confirm everything with licensed drillers and your local health district.</p>
    </div>
    <div>
      <h4>Machine-readable</h4>
      <p><a href="../llms.txt">llms.txt</a> &mdash; what this service does and how any AI assistant can use or cite it.</p>
      <p class="fine">© 2026 I Need a Well (pilot). Connecticut only.</p>
    </div>
  </div>
</footer>
</body>
</html>
"""

def facts_html(town):
    out = []
    for title, body, src in town["local_facts"]:
        out.append(
            '    <div class="factbox">\n'
            f'      <p><b>{title}:</b> {body}</p>\n'
            f'      <span class="src">{src}</span>\n'
            '    </div>'
        )
    return "\n".join(out)

def siblings_html(town):
    return "\n".join(
        f'      <a href="{t["slug"]}.html">{t["name"]}</a>'
        for t in TOWNS if t["slug"] != town["slug"]
    )

for town in TOWNS:
    html = TEMPLATE.format(
        name=town["name"],
        blurb=town["blurb"],
        district=town["district"],
        local_facts=facts_html(town),
        siblings=siblings_html(town),
    )
    path = os.path.join(OUT, f'{town["slug"]}.html')
    with open(path, "w") as f:
        f.write(html)
    print("wrote", path)
