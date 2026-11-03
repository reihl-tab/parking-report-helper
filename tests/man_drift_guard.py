"""Skip-or-fail guard for the man-page drift tests (#256).

The drift tests compare the committed man/ tree with the generator, so a
checkout without man/ skips them. The Docker test stage copies man/ (#205)
and sets PARKING_REQUIRE_MAN_DRIFT=1: there a missing man/ fails those tests
instead, so the build cannot pass with the drift checks silently skipped.
The flag is off unless set to exactly "1"; messages never contain paths.
"""

from __future__ import annotations

import os
import unittest
from collections.abc import Mapping
from pathlib import Path

REQUIRE_ENV = "PARKING_REQUIRE_MAN_DRIFT"
SKIP_REASON = "man/ is not present in this checkout"
FAIL_MESSAGE = (
    f"man/ is not present, but {REQUIRE_ENV}=1 requires the man drift tests to run"
)


def drift_required(env: Mapping[str, str] | None = None) -> bool:
    """True when the man drift tests must run rather than skip."""
    return (os.environ if env is None else env).get(REQUIRE_ENV, "") == "1"


def require_man_pages(
    test: unittest.TestCase, man1: Path, env: Mapping[str, str] | None = None
) -> None:
    """Call from setUp: return if man1 exists, else fail (flag set) or skip."""
    if man1.is_dir():
        return
    if drift_required(env):
        test.fail(FAIL_MESSAGE)
    test.skipTest(SKIP_REASON)
