# I Need a Well — MCP server

A Model Context Protocol (MCP) server that gives any AI assistant one
tool: screen a Connecticut street address for residential well
prospects, using public data only.

Built 2026-09-30. **Local build — not deployed, not listed anywhere.**

## What it does

The server wraps the well-screening engine that **ships in this
repo** (`well-data-skill/bin/wellscreen.py`, beside this folder, run
unmodified as a subprocess) and exposes its JSON screen as an MCP
tool. No path configuration is needed on a fresh clone; the server
refuses to start — naming every location it tried — if the engine
file is missing. The engine
geocodes with the US Census geocoder, pulls Connecticut well
completion records (CT open data, cached locally by the engine,
refreshed every 30 days), and counts USGS monitoring well sites.
Every source is free and keyless. Nothing is written anywhere: no
database, no intake form, no email. This is the free screen only —
there is no paid tier, no dossier, and no outreach in this server.

## The tool

### `screen_address(address: string)`

- **Input:** one US street address, 3–200 characters, e.g.
  `"193 Wells Rd, East Windsor, CT 06016"`.
- **Output (success):** the engine's screen JSON — matched address,
  coordinates, unique nearby well properties (duplicate state filings
  merged; the raw filing count is reported alongside), average casing
  depth and yield with every average stating how many properties it
  is based on, a USGS well-site count, and the coverage note that CT
  open data covers reports filed since 2021-01-01.
- **Connecticut only, by design.** The address's state is verified
  from the Census match's structured state field. Errors come back
  as MCP tool errors, in plain words, exactly as the engine reports
  them — they are never converted into a fake success:
  - `not_in_connecticut` — the address matched outside Connecticut;
    no screen was run.
  - `no_geocode_match` — the Census geocoder found no match; check
    the address, or use the nearest town name.
  - `data_source_unavailable` — a public data source did not answer
    after retries; wait and try again.
- **Village names work.** CT villages whose mailing name isn't the
  town the geocoder files them under (Moodus → East Haddam,
  Hazardville → Enfield, Broad Brook → East Windsor, …) are matched
  via the parent town, and the result **says so** in a
  `geocode_fallback` / `locality_note` field. Nothing is silently
  renamed.
- A null average means no nearby property reported that value. It is
  never estimated, and missing data is never phrased as reassurance.

## Run it locally

```bash
cd mcp-server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# stdio (for a local MCP client config):
.venv/bin/python server.py

# streamable HTTP on http://127.0.0.1:8765/mcp :
.venv/bin/python server.py --transport http --port 8765
```

Environment knobs (all optional): `WELLSCREEN_PATH` (engine location
**override** — only needed if the engine is not at the in-repo
default `well-data-skill/bin/wellscreen.py`),
`WELLSCREEN_TIMEOUT_S` (per-call ceiling, default 240 — the engine
retries slow government APIs internally, so screens can legitimately
take a while when a source is sluggish).

**Local-network note:** MCP stdio clients launch servers with a
small default environment. If your machine reaches the internet
through a proxy, make sure your client config passes `HTTPS_PROXY`
(and friends) through to the server, or the engine's public-data
calls will fail with `data_source_unavailable`. On a normally
networked host (including the planned deployment) this doesn't apply.

## Safety shape (public-server pattern)

- **Read-only annotations:** `readOnlyHint`, non-destructive,
  idempotent, open-world — declared on the tool for hosts that use
  them (e.g. confirmation prompts).
- **Strict input:** the address is length-capped (3–200 chars) in the
  tool schema; invalid calls are rejected before the engine runs.
- **Rate limit:** FastMCP token-bucket middleware — sustained
  1 request/second per client, burst of 10.
- **Response cap:** FastMCP response-limiting middleware at 256 KB
  (a backstop; the engine itself caps output at 50 nearby properties).
- **Timeouts:** every engine call has a hard ceiling (above); a hung
  upstream surfaces as `data_source_unavailable`, not a stuck request.
- **No login** — the data is public and the tool is read-only, so v1
  has no OAuth and no keys.

## What gets logged

Server logs record that a call happened, its outcome (success or the
error kind), and how long it took. **Full addresses are never written
to the log** — only the address's length, as a sanity signal. Tool
results themselves contain the Census-matched address, which is the
answer the caller asked for; the server keeps no copy of it.

## Honest limits

- Connecticut only. Other states return `not_in_connecticut`.
- Public records only, and CT's open well data starts 2021 — older
  wells aren't in it, and the screen says thin evidence is thin.
- This screens prospects from records. It does not pick a drill spot;
  a licensed driller does that on site.
- Not deployed yet: no public URL, no hosting, no registry listing.

## Files

- `server.py` — the FastMCP server (one tool, middleware, engine runner)
- `test_client.py` — protocol-level test client (stdio + HTTP batteries)
- `requirements.txt` — pinned dependencies (FastMCP 4.0.10 / MCP SDK 2.2.0)
