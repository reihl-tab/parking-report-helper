"""Private Git-backed 106 ledger. Fail closed on stale writers or uncertain writes."""

from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Iterator

from filelock import FileLock


class LedgerError(RuntimeError):
    """No municipal effect is allowed after this error."""


class GitLedger:
    """Use a dedicated checked-out private ledger branch, never cache/artifact state."""

    def __init__(self, root: Path, branch: str = "106-ledger") -> None:
        self.root = root.resolve()
        self.branch = branch
        if branch != "106-ledger":
            raise LedgerError("Expected the dedicated 106-ledger branch")
        self.path = self.root / "ledger.json"
        self.data: dict[str, Any] = {}

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True
        )
        if result.returncode:
            raise LedgerError(
                "Private ledger Git operation failed; reconcile before retrying"
            )
        return result.stdout.strip()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def assert_current(self) -> None:
        if self.git("branch", "--show-current") != self.branch:
            raise LedgerError("Wrong private ledger branch")
        if self.git("status", "--porcelain"):
            raise LedgerError("Dirty ledger checkout; reconcile before retrying")
        remote = self.git("ls-remote", "--heads", "origin", "refs/heads/" + self.branch)
        if not remote or remote.split()[0] != self.head():
            raise LedgerError(
                "Stale or missing remote ledger; no automatic reset/retry"
            )

    @contextmanager
    def locked(self) -> Iterator[GitLedger]:
        """Hold a local exclusive lock through the whole run; remote CAS handles other hosts."""
        with FileLock(
            str(self.root.parent / ".106-ledger.lock"), timeout=0, mode=0o600
        ):
            self.assert_current()
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            if self.data.get("version") != 1 or not isinstance(
                self.data.get("reports"), dict
            ):
                raise LedgerError("Unsupported or corrupt private ledger")
            if not isinstance(self.data.get("photos"), dict):
                raise LedgerError("Corrupt private photo index")
            yield self

    def persist(self) -> None:
        """Push a non-forced fast-forward commit before permitting the next side effect."""
        self.assert_current()
        original = self.path.read_bytes()
        encoded = (
            json.dumps(self.data, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
        ).encode()
        if encoded == original:
            return
        self.path.write_bytes(encoded)
        self.path.chmod(0o600)
        self.git("add", "--", "ledger.json")
        self.git("commit", "-m", "Record private 106 ledger transition")
        self.git("push", "origin", "HEAD:refs/heads/" + self.branch)
        # Recheck remote acceptance even if a push result appeared successful.
        self.assert_current()

    def reserve(self, report_id: str, digest: str, photo_hash: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}", report_id):
            raise LedgerError("Stable intake ID must be an opaque folder name")
        if any(
            not re.fullmatch(r"[0-9a-f]{64}", value) for value in (digest, photo_hash)
        ):
            raise LedgerError("Invalid payload/photo digest")
        reports = self.data["reports"]
        key = hashlib.sha256(report_id.encode()).hexdigest()
        existing = reports.get(key)
        if existing:
            if existing["digest"] != digest or existing["photo_sha256"] != photo_hash:
                raise LedgerError(
                    "Existing intake ID has changed inputs; review without creating a new case"
                )
            return existing
        other = self.data["photos"].get(photo_hash)
        if other and other != key:
            raise LedgerError("Original photo already belongs to another intake ID")
        entry = {
            "digest": digest,
            "photo_sha256": photo_hash,
            "state": {"version": 1, "status": "prepared"},
        }
        reports[key] = entry
        self.data["photos"][photo_hash] = key
        self.persist()
        return entry

    def checkpoint(self, report_id: str, state: dict[str, Any]) -> None:
        key = hashlib.sha256(report_id.encode()).hexdigest()
        entry = self.data["reports"].get(key)
        if not entry:
            raise LedgerError("Unreserved intake ID")
        entry["state"] = dict(state)
        self.persist()
