#!/usr/bin/env python3
"""I Need a Well — MCP server (v1, local build).

Exposes ONE read-only tool, `screen_address`, over the Model Context
Protocol. The tool wraps the well-screening engine that ships in
this repo (well-data-skill/bin/wellscreen.py, beside this folder;
WELLSCREEN_PATH overrides its location): give it a Connecticut
street address, get back the engine's public-data screen as JSON —
nearby well records, depths, yields, coverage caveats.

Connecticut only, by design. The engine's structured errors pass
through to the caller unchanged in meaning and in plain words:
  - not_in_connecticut    the address matched outside Connecticut
  - no_geocode_match      the Census geocoder found no match
  - data_source_unavailable  a public data source did not answer

Nothing here writes anything: no database, no intake, no email.
The engine reads only free public data (US Census geocoder, CT open
data well records, USGS). See README.md for the full contract.

Run:
  python server.py                      # stdio (local MCP clients)
  python server.py --transport http     # streamable HTTP on 127.0.0.1:8765
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Annotated

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware
from mcp.types import ToolAnnotations
from pydantic import Field

# --- Configuration -----------------------------------------------------------

# Engine resolution (stranger-test B3, 2026-10-29): the screening
# engine ships IN the repo (well-data-skill/), so the server must
# find it on any machine — not only at the original author's
# absolute workspace path. Resolution order:
#   1. WELLSCREEN_PATH environment override (explicit; if set but
#      missing, startup fails naming it — an explicit knob is never
#      silently ignored).
#   2. The vendored in-repo copy, relative to this file:
#      <repo>/well-data-skill/bin/wellscreen.py.
#   3. The author's workspace layout, relative to this file:
#      <workspace>/skills/well-data/bin/wellscreen.py (this file at
#      <workspace>/goals/how-to-dig-a-well/mcp-server/server.py).
#   4. The legacy absolute default ~/workspace/skills/... as a
#      last resort.
# ENGINE_PATH is the first candidate that exists; if none does, the
# __main__ startup check fails naming every path in ENGINE_TRIED.
_HERE = Path(__file__).resolve().parent
_ENGINE_CANDIDATES = [
    _HERE.parent / "well-data-skill" / "bin" / "wellscreen.py",
]
if len(_HERE.parents) > 2:  # shallow copies (e.g. /tmp/x/server.py)
    _ENGINE_CANDIDATES.append(
        _HERE.parents[2] / "skills" / "well-data" / "bin" / "wellscreen.py"
    )
_ENGINE_CANDIDATES.append(
    Path.home() / "workspace" / "skills" / "well-data" / "bin" / "wellscreen.py"
)
_env_engine = os.environ.get("WELLSCREEN_PATH")
if _env_engine:
    ENGINE_PATH = Path(_env_engine)
    ENGINE_TRIED = [ENGINE_PATH]
else:
    ENGINE_TRIED = list(_ENGINE_CANDIDATES)
    ENGINE_PATH = next(
        (p for p in _ENGINE_CANDIDATES if p.exists()), _ENGINE_CANDIDATES[0]
    )
# The engine retries each upstream source internally (3 tries with
# backoff), so a single screen can legitimately take a while when a
# government API is slow. 240s is the hard ceiling; past that we tell
# the caller the source did not answer rather than hanging forever.
ENGINE_TIMEOUT_S = float(os.environ.get("WELLSCREEN_TIMEOUT_S", "240"))
RADIUS_MI = 2.0  # engine default; fixed for v1, not a caller knob

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("ineedawell-mcp")

# --- Server ------------------------------------------------------------------

mcp = FastMCP(
    "ineedawell-well-screen",
    version="0.1.0",
    instructions=(
        "Screens a Connecticut property address for residential well "
        "prospects using public data only (US Census geocoder, CT open "
        "data well records since 2021, USGS). Connecticut addresses "
        "only: an out-of-state address returns a not_in_connecticut "
        "error, never a screen. This is a screening aid built from "
        "public records — it does not pick a drill spot, and a null "
        "average means nobody reported that value, not that it is zero."
    ),
    middleware=[
        # Sustained 1 request/second per client, bursts of 10. A screen
        # makes real upstream calls; this keeps one caller from
        # monopolizing the server or hammering the public APIs.
        RateLimitingMiddleware(max_requests_per_second=1.0, burst_capacity=10),
        # Backstop only: the engine caps its own output (max 50 nearby
        # properties), so a normal screen is a few dozen KB. Anything
        # past 256 KB gets truncated rather than dumped on the caller.
        ResponseLimitingMiddleware(max_size=256_000),
    ],
)


# Concurrency bound (Codex R27): engines share one cache path and
# make real upstream calls; unbounded concurrent spawns multiply
# both. Excess callers wait for a slot rather than piling on.
MAX_CONCURRENT_ENGINES = 2
_engine_slots = asyncio.Semaphore(MAX_CONCURRENT_ENGINES)


async def _terminate_owned(proc) -> None:
    """Kill and reap a subprocess this server started. Shielded so
    the reap completes even while the surrounding task is being
    cancelled (R27)."""
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    await asyncio.shield(proc.wait())


async def _run_engine(address: str) -> tuple[int, dict]:
    """Run wellscreen.py as a subprocess; return (exit_code, payload).

    Raises ToolError if the engine cannot be run or does not answer
    within ENGINE_TIMEOUT_S. Never raises the engine's expected
    outcomes (bad address, wrong state) — those come back as
    (exit_code, payload) for the caller to pass through.

    Cancellation-safe (R27): if the tool task is cancelled while
    the engine runs, the owned process is killed and reaped BEFORE
    the CancelledError propagates — a cancelled caller must not
    leave an engine doing upstream calls and cache writes behind.
    """
    async with _engine_slots:
        return await _run_engine_once(address)


async def _run_engine_once(address: str) -> tuple[int, dict]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(ENGINE_PATH),
        address,
        "--radius-mi",
        str(RADIUS_MI),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=ENGINE_TIMEOUT_S)
    except asyncio.TimeoutError:
        await _terminate_owned(proc)
        raise ToolError(
            "data_source_unavailable: The screening engine did not finish "
            "within %d seconds — a public data source is likely not "
            "answering. Wait a moment and try again." % int(ENGINE_TIMEOUT_S)
        )
    except asyncio.CancelledError:
        await _terminate_owned(proc)
        raise
    try:
        payload = json.loads(out.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        log.error("engine exit=%s with unparseable stdout; stderr tail: %s",
                  proc.returncode, err.decode("utf-8", "replace")[-500:])
        raise ToolError(
            "engine_error: The screening engine failed unexpectedly "
            "(exit code %s) and returned no readable result." % proc.returncode
        )
    return proc.returncode, payload


@mcp.tool(
    annotations=ToolAnnotations(
        title="Screen a Connecticut address for well prospects",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
)
async def screen_address(
    address: Annotated[
        str,
        Field(
            min_length=3,
            max_length=200,
            description=(
                "A US street address, e.g. '193 Wells Rd, East Windsor, "
                "CT 06016'. Connecticut addresses only. Village mailing "
                "names (Moodus, Hazardville, Broad Brook) are accepted — "
                "if one is filed under its parent town, the result says so."
            ),
        ),
    ],
) -> dict:
    """Screen a Connecticut address for residential well prospects.

    Geocodes the address (US Census), then reports nearby Connecticut
    well completion records (unique properties, with duplicate state
    filings merged), average casing depth and yield — every average
    states how many properties it is based on — plus a USGS well-site
    count and the coverage caveat that CT open data starts 2021.

    Returns the engine's screen JSON on success. Errors are returned
    as MCP tool errors in plain words, exactly as the engine reports
    them: not_in_connecticut (address matched outside CT — no screen
    was run), no_geocode_match (no Census match — check the address),
    or data_source_unavailable (a public data source did not answer —
    retry shortly).
    """
    cleaned = " ".join(address.split())
    started = time.monotonic()
    # Deliberately logged WITHOUT the address itself: outcome and
    # timing only. Callers send property addresses; we don't keep them.
    log.info("screen_address called (address length %d)", len(cleaned))
    exit_code, payload = await _run_engine(cleaned)
    elapsed = time.monotonic() - started

    if exit_code == 0:
        log.info("screen_address ok in %.1fs", elapsed)
        return payload

    kind = payload.get("error", "engine_error")
    message = payload.get("message", "The screening engine reported an error.")
    log.info("screen_address outcome=%s in %.1fs", kind, elapsed)
    text = "%s: %s" % (kind, message)
    if payload.get("matched_address"):
        text += " (Census matched: %s)" % payload["matched_address"]
    raise ToolError(text)


# --- Entry point -------------------------------------------------------------

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="I Need a Well MCP server")
    ap.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    if not ENGINE_PATH.exists():
        log.error(
            "screening engine not found — tried: %s. The engine ships "
            "in the repo at well-data-skill/bin/wellscreen.py (beside "
            "the mcp-server/ folder); restore that copy, or set "
            "WELLSCREEN_PATH to the engine's location.",
            ", ".join(str(p) for p in ENGINE_TRIED),
        )
        sys.exit(1)

    if args.transport == "http":
        mcp.run(transport="streamable-http", host=args.host, port=args.port,
                show_banner=False)
    else:
        mcp.run(transport="stdio", show_banner=False)
