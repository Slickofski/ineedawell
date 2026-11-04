# I Need a Well

**The listing says "private well required." Now what?**

I Need a Well answers the first question a Connecticut land buyer hits:
*can I actually put a well on this lot, and what will it cost me?*

Give it a Connecticut property address. It digs through the state's public
records: nearby well completion reports, water-quality data, soil and
setback rules, and hands back a plain-language snapshot: a verdict, a
cost band with checkable math, the dry-hole truth, water-quality flags,
the setback top-lines, and what to do next. Free, no accounts, no keys.

It is not a permit, a quote, or a substitute for your health district or a
licensed driller. It's the homework, done in about 20 seconds, so you can
talk to those people knowing what you're looking at.

## Run it

You need Python 3.12 (developed and tested on 3.12.3) and an internet
connection. No accounts, no API keys: every data source is public and
keyless (OpenStreetMap Nominatim for geocoding, the CT Department of
Consumer Protection well-completion dataset on data.ct.gov, and the
federal Water Quality Portal).

```bash
cd fulfillment
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 run_screen.py --order '{"name":"Demo","email":"demo@example.com","property_address":"193 Wells Rd, East Windsor, CT 06016","tier":"free"}'
```

That address is a real public land listing used as the project sample.
A successful run creates an order folder under `fulfillment/orders/`
containing, among other files:

- `screen.html`: the snapshot, the way a buyer reads it
- `screen.json`: the same verdict, machine-readable
- `delivery_email.html` / a plain-text draft: the delivery email

**Supplying coordinates:** if you already know a property's coordinates
(listings often publish them), include `"lat"` and `"lon"` in the order
JSON and the pipeline uses them instead of geocoding: useful for rural
addresses geocoders place ambiguously. The output records that the
coordinates were supplied, not found.

**Failure behavior:** if an address can't be located unambiguously, the
run does not crash and does not guess. It writes a polite draft asking
for a corrected address, records the reason in `fulfillment_failed.json`,
and still exits 0: check for that file (or the `FAILED (graceful)` line)
to know a run didn't produce a snapshot.

## Run the tests

```bash
cd fulfillment
python3 -m unittest discover
```

The full suite (220 tests), the fulfillment pipeline tests plus the Jev
triage tests, runs under that one command, uses mocked/synthetic data,
and needs no network and no credentials.

## What's in the box

- `fulfillment/`: the pipeline: intake → geocode → nearby wells →
  water quality → setbacks (Connecticut rules engine, `pipeline/rules_ct.json`)
  → verdict → snapshot + delivery drafts. Also the report and permit-packet
  generators used by the paid tiers (parked: see below).
- `site/`: the static landing page and request form. Its form posts to a
  Supabase backend; the URL and public anon key sit in a marked config
  block at the top of `site/app.js`. As shipped, that block carries
  **placeholder values**: the page and form render as a demo, but nothing
  is submitted anywhere. Point the config at your own backend before
  deploying it anywhere.
- `mcp-server/`: an MCP server exposing the well-data engine so AI
  assistants can call it. Its engine ships inside this repo
  (`well-data-skill/`); set `WELLSCREEN_PATH` only if you keep a copy
  elsewhere.
- `samples/`: generated snapshots for sample Connecticut properties
  (customer details replaced with sample values), so you can see every
  verdict shape without running anything.

## What's optional, and what stays off

- **Jev triage (TypeSafe)**: an address-scope check that runs before
  geocoding if `TYPESAFE_API_KEY` is set. Without a key it skips itself
  and the run proceeds. You don't need it.
- **Supabase**: only the website form and the hosted worker use it. The
  command-line pipeline above never touches it.
- **SMTP / payments**: belong to paid tiers that are currently parked.
  The free snapshot sends nothing and charges nothing; delivery emails
  are generated as drafts for a human to approve and send.
- **Before you send anyone anything:** edit `fulfillment/config/business.json`:
  it ships with placeholder business identity fields.

## Scope and honesty notes

- Connecticut only, for now: the rules engine, fee schedules, and well
  records are CT. The geocoder will happily locate other states; the
  verdict machinery will not pretend to know their rules.
- Well records come from reports filed online since 2021: the public
  dataset's own boundary, stated in the output. Older wells exist that
  no dataset here can see.
- Running the pipeline sends the property address to public APIs
  (geocoding, state and federal data services): that's how it works;
  no address is sold or shared anywhere else by this code.

## Support and maintenance

Connecticut-only, best-effort, no SLA. Issues are triaged weekly.
**Never post a private property address in a public issue**: describe
the problem with a sample or synthetic address instead. Upstream data
sources (USGS, the Water Quality Portal, CT Socrata, and the CT rule
and fee sources) are checked monthly; a degraded source is supposed to
fail loudly, never produce a silently weaker verdict.

## License

MIT: see [LICENSE](LICENSE). Copyright (c) 2026 I Need a Well contributors.
