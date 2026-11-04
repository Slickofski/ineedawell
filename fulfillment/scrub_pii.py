#!/usr/bin/env python3
"""RETIRED 2026-10-21 — do not use. This script refuses to run.

Why: the original fulfillment-tree scrub was non-atomic (it stamped
pii_scrubbed_at in the same write it was supposed to be verifying, then
rounded coordinates in a second, unchecked write), never inspected its
own PATCH results, and touched zero local files — while the site's
privacy promise ("use it to build your snapshot, then it's deleted")
covers every retained copy. The 2026-10-21 adversarial review flagged
it as finding #4 of the privacy/scrub cluster.

The canonical scrub now lives in the worker repo and the database:
  - Repo: scrub_pii.py + worker/operations.py — plan-by-default,
    execution requires explicit per-order approval and an
    external-copies-cleared attestation; covers the order's
    UUID-contained local attempt trees (symlink-checked).
  - DB: public.well_scrub(uuid, boolean) (migration 001) clears
    name/phone/address/notes/report_path and rounds coordinates in
    ONE atomic update, gated on confirmed delivery; and
    public.well_retire_undelivered (migration 002) handles orders
    that were never delivered.
Both were verified live 2026-10-21 (anon has no direct table access;
the bounded submit_well_request RPC is the only public door and the
pilot intake is closed).

If you reached for this file: use the repo path above. Do not resurrect
this script — a half-scrub is worse than none, because it stamps
success. (Marcin approved this retirement 2026-10-21.)
"""

import sys


def main(argv):
    print(__doc__.strip())
    print("\nREFUSING TO RUN — use the canonical scrub (worker repo).")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
