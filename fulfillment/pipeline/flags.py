"""Product flags — single source of truth for parked/live surfaces.

This module imports nothing from the pipeline package, so every stage
may import it without a cycle (screen.py already imports report.py, so
a flag owned by screen.py could not be read by report.py).

Read flags as module attributes (``FLAGS.DOSSIER_INTENT_ENABLED``),
never ``from pipeline.flags import DOSSIER_INTENT_ENABLED`` — the
from-import binds the value at import time and defeats both the gate
and test monkeypatching.
"""

# $39 dossier-intent question (Marcin, 2026-10-24: PARKED). When False,
# no customer surface asks it: the snapshot's "Want the full dossier?"
# card (screen.py), favor #2 in the delivery email (report.py), and the
# plain-text pointer to it (run_screen.py) all stay unrendered. The
# card component, dossier generation, and intent machinery remain in
# the tree, inert — flip this to True to restore the question exactly.
DOSSIER_INTENT_ENABLED = False
