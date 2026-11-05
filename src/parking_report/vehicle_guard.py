"""Private photo/vehicle HMAC identity. No provisioning, logging or live effects."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any

from .stats import plate_digits

IDENTITY_DOMAIN = "parking-report/photo-vehicle/v1"
VERIFIER_DOMAIN = "parking-report/photo-vehicle-key-verifier/v1"


class GuardKeyError(ValueError):
    """Fixed refusal without echoing private values."""


class GuardKey:
    """Runtime-only secret; never serialized, printed or implicitly replaced."""

    def __init__(self, key_id: str, seed: bytes) -> None:
        if (
            not isinstance(key_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", key_id) is None
            or not isinstance(seed, bytes)
            or len(seed) < 16
        ):
            raise GuardKeyError("Private guard key configuration is invalid")
        self.key_id = key_id
        self.__seed = seed

    def _sign(self, value: dict[str, str]) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return hmac.new(self.__seed, encoded, hashlib.sha256).hexdigest()

    def identity(self, photo_sha256: str, plate: str) -> str:
        """Strict standard plate normalization, preserving leading zeros."""
        if (
            not isinstance(photo_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", photo_sha256) is None
            or not isinstance(plate, str)
            or re.fullmatch(
                r"(?:[0-9]{7,8}|[0-9]{2}-[0-9]{3}-[0-9]{2}|[0-9]{3}-[0-9]{2}-[0-9]{3})",
                plate,
            )
            is None
        ):
            raise GuardKeyError("Private guard identity inputs are invalid")
        normalized = plate_digits(plate)
        if normalized is None:
            raise GuardKeyError("Private guard identity inputs are invalid")
        return self._sign(
            dict(
                domain=IDENTITY_DOMAIN,
                key_id=self.key_id,
                photo_sha256=photo_sha256,
                plate=normalized,
            )
        )

    def metadata(self) -> dict[str, str]:
        """Private-ledger metadata only; no raw seed or plate values."""
        return {
            "key_id": self.key_id,
            "verifier": self._sign(
                dict(domain=VERIFIER_DOMAIN, key_id=self.key_id, check="verify-key")
            ),
        }

    def verify(self, metadata: Any) -> None:
        """Fail closed, never initialize or silently re-key an existing index."""
        if (
            not isinstance(metadata, dict)
            or set(metadata) != {"key_id", "verifier"}
            or metadata.get("key_id") != self.key_id
            or not isinstance(metadata.get("verifier"), str)
            or re.fullmatch(r"[0-9a-f]{64}", metadata["verifier"]) is None
        ):
            raise GuardKeyError("Private guard key verification failed")
        if not hmac.compare_digest(metadata["verifier"], self.metadata()["verifier"]):
            raise GuardKeyError("Private guard key verification failed")
