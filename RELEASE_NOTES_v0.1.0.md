# I Need a Well - v0.1.0 (ALPHA)

First public release. **Alpha**: the snapshot pipeline is tested and in
pilot use, but expect rough edges, and expect the output format to keep
evolving.

## What this is

A free, open-source tool that answers the first question a Connecticut
land buyer hits - *can I actually put a well on this lot, and what will
it cost me?* Give it a Connecticut property address and it digs through
public records (nearby well completion reports, water-quality data, soil
and setback rules) and hands back a plain-language snapshot: a verdict,
a cost band with checkable math, the dry-hole truth, water-quality
flags, the setback top-lines, and what to do next.

It is not a permit, a quote, or a substitute for your health district or
a licensed driller.

## What's included

- `fulfillment/` - the snapshot pipeline (intake -> geocode -> nearby
  wells -> water quality -> setbacks -> verdict -> snapshot + delivery
  drafts), plus the report / permit-packet generators for the parked
  paid tiers.
- `site/` - the static landing page and request form. It ships with
  placeholder backend config (see the CONFIG block at the top of
  `site/app.js`): as cloned, the form is a demo that submits nowhere.
- `mcp-server/` - an MCP server exposing the well-data engine to AI
  assistants; the engine ships in-repo under `well-data-skill/`.
- `well-data-skill/` - the well-data screening engine (geocoding,
  CT well records, USGS sites, USDA soils).
- `samples/` - generated snapshots for sample properties, so you can
  see every verdict shape without running anything.

## Known limits

- **Connecticut only.** The rules engine, fee schedules, and well
  records are CT. Other states geocode fine; the verdict machinery
  will not pretend to know their rules.
- **Well records are reports filed online since 2021** - the public
  dataset's own boundary. Older wells exist that no dataset here can
  see.
- **Two ways in: the CLI and the MCP server.** The website form is a
  demo until you self-host it against your own backend.
- Delivery emails are generated as drafts for a human to approve and
  send; the free snapshot sends nothing itself.
- Address-scope triage (Jev/TypeSafe), SMTP, and payments are optional
  or parked - see the README's "What's optional" section.
