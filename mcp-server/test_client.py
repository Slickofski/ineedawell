#!/usr/bin/env python3
"""Protocol-level test client for the I Need a Well MCP server.

Talks to the server through a real MCP client (FastMCP Client) over
stdio or streamable HTTP — never by importing the tool function.
Records exactly what comes back through the protocol for each case
(is_error, structured content, text content) into a JSON results file.

Usage:
  python test_client.py --transport stdio
  python test_client.py --transport http [--port 8765]
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

HERE = Path(__file__).resolve().parent
SERVER = HERE / "server.py"

# VM quirk workaround: this machine's NO_PROXY contains bracketed IPv6
# literals (e.g. "[fd8b:...::1]") that the HTTP library FastMCP's
# client is built on (httpx2) crashes trying to parse. Keep the
# entries local testing needs (localhost / 127.0.0.1 / ::1), drop the
# bracketed literals. Affects only this test harness on this VM.
for var in ("NO_PROXY", "no_proxy"):
    entries = [e for e in os.environ.get(var, "").split(",") if e and not e.startswith("[")]
    if entries:
        os.environ[var] = ",".join(entries)

CASES = [
    {
        "id": "wells-rd-real-screen",
        "address": "193 Wells Rd, East Windsor, CT 06016",
        "expect": "success: a real screen (ct_wells_found present)",
    },
    {
        "id": "springfield-ma-wrong-state",
        "address": "100 Main St, Springfield, MA",
        "expect": "error: not_in_connecticut",
    },
    {
        "id": "moodus-village-fallback",
        "address": "1 Main St, Moodus, CT",
        "expect": "success via disclosed East Haddam fallback (geocode_fallback present)",
    },
    {
        "id": "gibberish-no-match",
        "address": "99999 Zqxjkv Nonexistent Blvd, Nowhere, CT",
        "expect": "error: no_geocode_match",
    },
]


def summarize(result):
    """Flatten a CallToolResult into what the protocol actually carried."""
    out = {"is_error": bool(getattr(result, "is_error", False))}
    sc = getattr(result, "structured_content", None)
    if sc is not None:
        out["structured_content"] = sc
    texts = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            texts.append(text)
    if texts:
        out["text_content"] = texts
    return out


async def run_cases(client):
    tools = await client.list_tools()
    record = {
        "tools": [
            {
                "name": t.name,
                "title": getattr(t, "title", None),
                "annotations": (
                    t.annotations.model_dump() if getattr(t, "annotations", None) else None
                ),
                "input_schema": t.inputSchema,
            }
            for t in tools
        ],
        "cases": [],
    }
    for case in CASES:
        started = time.monotonic()
        entry = {"id": case["id"], "address": case["address"], "expect": case["expect"]}
        try:
            result = await client.call_tool(
                "screen_address", {"address": case["address"]}, raise_on_error=False
            )
            entry["result"] = summarize(result)
        except Exception as e:  # client-side failure is recorded, not hidden
            entry["client_exception"] = "%s: %s" % (type(e).__name__, e)
        entry["elapsed_s"] = round(time.monotonic() - started, 1)
        record["cases"].append(entry)
        status = entry.get("result", {}).get("is_error")
        print("case %-28s is_error=%s (%.1fs)" % (case["id"], status, entry["elapsed_s"]))
    return record


async def run_stdio():
    # Pass this process's full environment through: MCP stdio
    # transports launch servers with a small default environment,
    # which on this VM drops the HTTPS_PROXY variables the engine
    # needs for outbound calls. A real operator's client config does
    # the same when their network needs a proxy.
    transport = StdioTransport(
        command=sys.executable, args=[str(SERVER)], env=dict(os.environ)
    )
    async with Client(transport) as client:
        return await run_cases(client)


async def run_http(port):
    proc = subprocess.Popen(
        [sys.executable, str(SERVER), "--transport", "http", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        url = "http://127.0.0.1:%d/mcp" % port
        deadline = time.monotonic() + 60
        while True:
            try:
                async with Client(url) as client:
                    return await run_cases(client)
            except Exception:
                if time.monotonic() > deadline:
                    raise
                await asyncio.sleep(1.0)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    record = {"transport": args.transport}
    if args.transport == "stdio":
        record.update(await run_stdio())
    else:
        record.update(await run_http(args.port))

    out_path = HERE / ("test-results-%s.json" % args.transport)
    out_path.write_text(json.dumps(record, indent=1, default=str))
    print("wrote", out_path)


if __name__ == "__main__":
    asyncio.run(main())
