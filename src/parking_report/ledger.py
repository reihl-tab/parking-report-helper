"""Private Git-backed 106 ledger. Fail closed on stale writers or uncertain writes."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from filelock import FileLock

if TYPE_CHECKING:
    from .vehicle_guard import GuardKey

_HASH = r"[0-9a-f]{64}"


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
        # check=False: the return code is inspected below and mapped to
        # LedgerError. A git that cannot start (OSError) or a SubprocessError
        # is the same failure, never a raw exception; its text is withheld.
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.root,
                capture_output=True,
                text=True,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise LedgerError(
                "Private ledger Git operation failed; reconcile before retrying"
            ) from None
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
            if not isinstance(self.data.get("shared_photos", {}), dict):
                raise LedgerError("Corrupt private shared-photo index")
            if not isinstance(self.data.get("photo_vehicle", {}), dict):
                raise LedgerError("Corrupt private photo-vehicle guard index")
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

    def reserve(
        self,
        report_id: str,
        digest: str,
        photo_hash: str,
        *,
        followup: dict[str, str] | None = None,
        shared_photo: dict[str, Any] | None = None,
        extension: dict[str, str] | None = None,
        guard: GuardKey | None = None,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}", report_id):
            raise LedgerError("Stable intake ID must be an opaque folder name")
        if any(not re.fullmatch(_HASH, value) for value in (digest, photo_hash)):
            raise LedgerError("Invalid payload/photo digest")
        followup, group, extension, relation = self._relations(
            followup, shared_photo, extension, guard
        )
        reports = self.data["reports"]
        key = hashlib.sha256(report_id.encode()).hexdigest()
        existing = reports.get(key)
        if existing:
            if self._changed(existing, digest, photo_hash, followup, group, relation):
                raise LedgerError(
                    "Existing intake ID has changed inputs; review without creating a new case"
                )
            return existing
        self._bind(key, photo_hash, followup, group, extension, relation, guard)
        entry: dict[str, Any] = {
            "digest": digest,
            "photo_sha256": photo_hash,
            "state": {"version": 1, "status": "prepared"},
        }
        if followup is not None:
            entry["followup"] = followup
        if group is not None:
            entry["shared_photo"] = group["group"]
        if relation is not None:
            entry["extension"] = relation
        reports[key] = entry
        if followup is None and relation is None:
            # A second group member keeps the first member as the photo's origin.
            self.data["photos"].setdefault(photo_hash, key)
        self.persist()
        return entry

    def _relations(
        self,
        followup: dict[str, str] | None,
        shared_photo: dict[str, Any] | None,
        extension: dict[str, str] | None,
        guard: GuardKey | None,
    ) -> tuple[
        dict[str, str] | None,
        dict[str, Any] | None,
        dict[str, str] | None,
        dict[str, str] | None,
    ]:
        """Validate the optional relations and derive their ledger records."""
        if followup is not None:
            from .followup import validate_followup

            followup = validate_followup(followup)
        relation = None
        if extension is not None:
            from .shared_photo_extension import (
                extension_relation,
                validate_extension,
            )

            if followup is not None or shared_photo is not None:
                raise LedgerError(
                    "An extension cannot combine a follow-up or shared photo"
                )
            extension = validate_extension(extension)
            relation = extension_relation(extension)
        elif guard is not None:
            raise LedgerError("A private guard key requires an extension manifest")
        return followup, self._shared_group(followup, shared_photo), extension, relation

    @staticmethod
    def _changed(
        existing: dict[str, Any],
        digest: str,
        photo_hash: str,
        followup: dict[str, str] | None,
        group: dict[str, Any] | None,
        relation: dict[str, str] | None,
    ) -> bool:
        return (
            existing["digest"] != digest
            or existing["photo_sha256"] != photo_hash
            or existing.get("followup") != followup
            or existing.get("shared_photo") != (group["group"] if group else None)
            or existing.get("extension") != relation
        )

    def _bind(
        self,
        key: str,
        photo_hash: str,
        followup: dict[str, str] | None,
        group: dict[str, Any] | None,
        extension: dict[str, str] | None,
        relation: dict[str, str] | None,
        guard: GuardKey | None,
    ) -> None:
        """Parent/group/extension binding checks for a new reservation."""
        other = self.data["photos"].get(photo_hash)
        if followup is not None:
            self._check_parent(key, photo_hash, followup)
        elif group is not None:
            self._join_group(key, photo_hash, group)
        elif relation is not None:
            self._extend(key, photo_hash, extension, relation, guard)
        elif other and other != key:
            raise LedgerError("Original photo already belongs to another intake ID")

    @staticmethod
    def _shared_group(
        followup: dict[str, str] | None, shared_photo: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Plate-free ledger record for a shared-photo manifest, if any."""
        if shared_photo is None:
            return None
        if followup is not None:
            raise LedgerError("A follow-up cannot also be a shared-photo member")
        from .shared_photo import group_fingerprint, validate_shared_photo

        manifest = validate_shared_photo(shared_photo)
        return {
            "group": group_fingerprint(manifest),
            "members": [
                hashlib.sha256(i.encode()).hexdigest() for i in manifest["intake_ids"]
            ],
            "photo_sha256": manifest["photo_sha256"],
        }

    def _join_group(self, key: str, photo_hash: str, group: dict[str, Any]) -> None:
        """Lock the group on its first reservation; later members must match it."""
        if key not in group["members"] or group["photo_sha256"] != photo_hash:
            raise LedgerError(
                "Shared-photo manifest does not name this intake and photo"
            )
        recorded = self.data.get("shared_photos", {}).get(photo_hash)
        origin = self.data["photos"].get(photo_hash)
        locked = {"group": group["group"], "members": group["members"]}
        if recorded is None:
            if origin is not None:
                raise LedgerError("Original photo already belongs to another intake ID")
            self.data.setdefault("shared_photos", {})[photo_hash] = locked
        elif recorded != locked or origin not in group["members"]:
            raise LedgerError("Shared-photo group is locked; membership cannot change")

    def _check_parent(
        self, key: str, photo_hash: str, followup: dict[str, str]
    ) -> None:
        reports = self.data["reports"]
        parent_key = hashlib.sha256(followup["parent_id"].encode()).hexdigest()
        parent = reports.get(parent_key)
        if parent_key == key or not parent:
            raise LedgerError("Follow-up parent is missing or self-referential")
        expected = (followup["parent_digest"], photo_hash, "complete")
        actual = (
            parent.get("digest"),
            parent.get("photo_sha256"),
            parent.get("state", {}).get("status"),
        )
        if actual != expected:
            raise LedgerError("Follow-up needs the completed matching parent")
        receipt_number = parent.get("state", {}).get("case", {}).get("CaseNumberInCRM")
        if receipt_number and receipt_number != followup["parent_case_number"]:
            raise LedgerError("Follow-up case reference conflicts with parent receipt")
        origin = self.data["photos"].get(photo_hash)
        if not origin or origin not in reports:
            raise LedgerError("Follow-up photo index is missing")
        if any(
            e.get("followup", {}).get("parent_id") == followup["parent_id"]
            for e in reports.values()
        ):
            raise LedgerError("Parent already has a follow-up; reconcile it first")

    def provision_guard_key(self, guard: GuardKey) -> None:
        """Reviewed one-time provisioning of the private guard index (#293).

        Initializes the optional index with exactly one key ID and its
        verification value. Never called on a submission path; never
        initializes opportunistically and never re-keys: a different key ID
        or a mismatched verification value is refused. Rotation is a
        separate reviewed migration, outside this feature.
        """
        metadata = guard.metadata()
        index = self.data.get("photo_vehicle")
        if index is None:
            self.data["photo_vehicle"] = {
                "key": metadata,
                "guards": {},
                "extensions": {},
            }
            self.persist()
            return
        if not isinstance(index, dict) or index.get("key") != metadata:
            raise LedgerError("A different private guard key is already provisioned")
        if not isinstance(index.get("guards"), dict) or not isinstance(
            index.get("extensions"), dict
        ):
            raise LedgerError("Corrupt private photo-vehicle guard index")

    def _guard_index(self, guard: GuardKey) -> dict[str, Any]:
        """The provisioned private index, verified in constant time, or fail."""
        from .vehicle_guard import GuardKeyError

        index = self.data.get("photo_vehicle")
        if not isinstance(index, dict) or not index:
            raise LedgerError("Private photo-vehicle guard index is not provisioned")
        try:
            guard.verify(index.get("key"))
        except GuardKeyError:
            raise LedgerError("Private guard key verification failed") from None
        guards = index.get("guards")
        extensions = index.get("extensions")
        if not isinstance(guards, dict) or not isinstance(extensions, dict):
            raise LedgerError("Corrupt private photo-vehicle guard index")
        for mapping in (guards, extensions):
            if any(
                re.fullmatch(_HASH, k) is None or re.fullmatch(_HASH, v) is None
                for k, v in mapping.items()
            ):
                raise LedgerError("Corrupt private photo-vehicle guard index")
        return index

    def _extend(
        self,
        key: str,
        photo_hash: str,
        manifest: dict[str, str] | None,
        relation: dict[str, str],
        guard: GuardKey | None,
    ) -> None:
        """Bind one new intake to a completed ordinary parent (#293).

        The parent entry and the original photo index stay byte-for-byte
        unchanged; the child is exempt from the photo index's single-intake
        refusal for this exact photo. One extension per original photo, and
        one intake per (photo, plate) guard key under the provisioned key.
        """
        if guard is None or manifest is None:
            raise LedgerError("Extension reservation needs the private guard key")
        reports = self.data["reports"]
        parent_key = relation["parent"]
        parent = reports.get(parent_key)
        if parent_key == key or not parent:
            raise LedgerError("Extension parent is missing or self-referential")
        expected = (manifest["parent_digest"], photo_hash, "complete")
        actual = (
            parent.get("digest"),
            parent.get("photo_sha256"),
            parent.get("state", {}).get("status"),
        )
        if actual != expected:
            raise LedgerError("Extension needs the completed matching parent")
        if any(k in parent for k in ("followup", "shared_photo", "extension")):
            raise LedgerError("Extension parent is not an ordinary intake")
        if self.data["photos"].get(photo_hash) != parent_key:
            raise LedgerError("Extension parent does not own the original photo")
        if any(
            e.get("followup", {}).get("parent_id") == manifest["parent_id"]
            for e in reports.values()
        ):
            raise LedgerError("Extension parent already has a follow-up")
        if photo_hash in self.data.get("shared_photos", {}):
            raise LedgerError("Extension parent belongs to a shared-photo group")
        index = self._guard_index(guard)
        from .vehicle_guard import GuardKeyError

        try:
            derived = guard.identity(photo_hash, manifest["plate"])
        except GuardKeyError:
            raise LedgerError("Private guard identity inputs are invalid") from None
        guards = index["guards"]
        extensions = index["extensions"]
        holder = guards.get(derived)
        if holder is not None and holder != key:
            raise LedgerError("This plate is already guarded for the original photo")
        if holder == key or extensions.get(photo_hash) == key:
            raise LedgerError("Private guard index is inconsistent; reconcile first")
        if extensions.get(photo_hash) is not None:
            raise LedgerError("The original photo already has one extension")
        guards[derived] = key
        extensions[photo_hash] = key

    def checkpoint(self, report_id: str, state: dict[str, Any]) -> None:
        key = hashlib.sha256(report_id.encode()).hexdigest()
        entry = self.data["reports"].get(key)
        if not entry:
            raise LedgerError("Unreserved intake ID")
        entry["state"] = dict(state)
        self.persist()
