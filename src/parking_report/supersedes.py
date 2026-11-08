"""Validate a sanctioned corrected re-filing (#260); never infer owner permission.

A ``supersedes`` manifest names one still-open parent intake in the private
ledger whose case is being re-filed, corrected, on the same original photo
(for example a wrong street). It is an audit record bound into the preview
digest, never permission by itself: live use also needs
``--approved-supersedes-sha256`` and every normal gate.
"""

from __future__ import annotations

import re
from typing import Any

_FIELDS = {"parent_id", "parent_digest", "approval_reference"}

# Every ledger state a parent may be in while its case is still open. These are
# all of the saved states ``api106.submit`` knows; the ledger has no "closed"
# state, so any other or missing state fails closed.
SUPERSEDABLE_STATES = frozenset(
    {"prepared", "creating", "created", "attaching", "complete"}
)


def validate_supersedes(value: Any, intake_id: str | None = None) -> dict[str, str]:
    """Return exact validated metadata; reject partial, unknown or self links."""
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise ValueError("Supersedes manifest must contain the exact required fields")
    if not all(isinstance(v, str) and v and v == v.strip() for v in value.values()):
        raise ValueError("Invalid supersedes metadata")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}", value["parent_id"]):
        raise ValueError("Invalid supersedes parent ID")
    if not re.fullmatch(r"[0-9a-f]{64}", value["parent_digest"]):
        raise ValueError("Invalid supersedes parent digest")
    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_:/.-]{0,199}", value["approval_reference"]
    ):
        raise ValueError("Invalid approval evidence reference")
    if intake_id is not None and value["parent_id"] == intake_id:
        raise ValueError("An intake cannot supersede itself")
    return dict(value)
