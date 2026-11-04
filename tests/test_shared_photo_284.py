"""#284: a non-regular sibling report.json is refused before it is read."""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path

from parking_report.shared_photo import _read_regular, check_sibling

PLATES = ["1000001", "20000002"]


def _manifest() -> dict[str, object]:
    return {
        "intake_ids": ["car-a", "car-b"],
        "plates": list(PLATES),
        "photo_sha256": "a" * 64,
        "authority_reference": "approval-synthetic-284",
    }


class NonRegularSiblingTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.own = self.root / "car-a"
        self.own.mkdir()
        (self.root / "car-b").mkdir()
        self.target = self.root / "car-b" / "report.json"

    def _check_promptly(self) -> BaseException | None:
        """Run check_sibling in a thread; fail (not hang) if it blocks."""
        outcome: list[BaseException | None] = []

        def run() -> None:
            try:
                check_sibling(self.own, _manifest())
                outcome.append(None)
            except BaseException as exc:  # noqa: BLE001 - recorded for assertion
                outcome.append(exc)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(timeout=5)
        if worker.is_alive():
            self._unblock()
            worker.join(timeout=5)
            self.fail("check_sibling blocked on a non-regular sibling record")
        return outcome[0]

    def _unblock(self) -> None:
        if self.target.is_fifo():
            fd = os.open(self.target, os.O_WRONLY | os.O_NONBLOCK)
            os.close(fd)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs os.mkfifo")
    def test_fifo_sibling_is_refused_without_blocking(self) -> None:
        os.mkfifo(self.target)
        error = self._check_promptly()
        self.assertIsInstance(error, ValueError)
        self.assertIn("missing or unreadable", str(error))

    @unittest.skipUnless(os.path.exists("/dev/null"), "needs /dev/null")
    def test_device_sibling_is_refused_without_reading(self) -> None:
        self.target.symlink_to("/dev/null")
        error = self._check_promptly()
        self.assertIsInstance(error, ValueError)
        self.assertIn("missing or unreadable", str(error))

    @unittest.skipUnless(os.path.exists("/dev/null"), "needs /dev/null")
    def test_reader_rejects_a_device_before_reading(self) -> None:
        # /dev/null reads as empty; only the regular-file check can tell it apart.
        with self.assertRaises(OSError):
            _read_regular(Path("/dev/null"))

    def test_regular_sibling_with_identical_manifest_still_passes(self) -> None:
        import json

        self.target.write_text(json.dumps({"shared_photo": _manifest()}))
        self.assertIsNone(self._check_promptly())


if __name__ == "__main__":
    unittest.main()
