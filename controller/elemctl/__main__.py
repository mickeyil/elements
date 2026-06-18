"""Placeholder entry point during the v3 rewrite.

The v2 one-shot runner moved to elemctl.deprecated.run; the v3 entry
point is not wired yet. Point this at the v3 server once it exists.
"""

import sys

print(
    "elemctl: no v3 entry point yet (v2 runner is in elemctl/deprecated/).",
    file=sys.stderr,
)
sys.exit(1)
