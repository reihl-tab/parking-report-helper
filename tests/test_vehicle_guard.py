"""Synthetic guard vectors; no production seeds, plates or network effects."""

import hashlib
import hmac
import json
import unittest

from parking_report.vehicle_guard import GuardKey, GuardKeyError


class GuardKeyTests(unittest.TestCase):
    def setUp(self):
        self.seed = bytes(range(32))  # Synthetic, deterministic test secret only.
        self.key = GuardKey("test-v1", self.seed)
        self.photo = "a" * 64

    def test_domain_separated_vector_and_normalization(self):
        canonical = json.dumps(
            dict(
                domain="parking-report/photo-vehicle/v1",
                key_id="test-v1",
                photo_sha256=self.photo,
                plate="1234567",
            ),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        expected = hmac.new(self.seed, canonical, hashlib.sha256).hexdigest()
        self.assertEqual(self.key.identity(self.photo, "12-345-67"), expected)
        self.assertEqual(self.key.identity(self.photo, "1234567"), expected)
        self.assertNotEqual(self.key.identity(self.photo, "1234568"), expected)
        self.assertNotEqual(self.key.identity("b" * 64, "1234567"), expected)

    def test_leading_zero_is_preserved(self):
        self.assertNotEqual(
            self.key.identity(self.photo, "0123456"),
            self.key.identity(self.photo, "1234567"),
        )

    def test_missing_short_seed_and_unknown_key_fail_closed(self):
        for seed in (None, b"", b"short", "not-bytes"):
            with self.subTest(seed_type=type(seed).__name__):
                with self.assertRaises(GuardKeyError):
                    GuardKey("test-v1", seed)
        with self.assertRaises(GuardKeyError):
            self.key.verify({"key_id": "other", "verifier": "0" * 64})

    def test_wrong_seed_or_corrupt_metadata_cannot_rekey(self):
        metadata = self.key.metadata()
        self.key.verify(metadata)
        with self.assertRaises(GuardKeyError):
            GuardKey("test-v1", bytes(range(1, 33))).verify(metadata)
        for bad in (
            {},
            None,
            dict(metadata, unexpected=True),
            dict(metadata, verifier="invalid"),
        ):
            with self.assertRaises(GuardKeyError):
                self.key.verify(bad)

    def test_invalid_input_fixed_errors_no_seed_or_plate(self):
        for plate in ("", "123", "123456789", "1234x67", "12 345 67"):
            with self.assertRaises(GuardKeyError) as caught:
                self.key.identity(self.photo, plate)
            if plate:
                self.assertNotIn(plate, str(caught.exception))
        with self.assertRaises(GuardKeyError):
            self.key.identity("invalid", "1234567")
        self.assertNotIn(self.seed.hex(), repr(self.key))

    def test_metadata_contains_only_key_id_and_verifier(self):
        self.assertEqual(set(self.key.metadata()), {"key_id", "verifier"})
        verifier = json.dumps(
            dict(
                domain="parking-report/photo-vehicle-key-verifier/v1",
                key_id="test-v1",
                check="verify-key",
            ),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        self.assertEqual(
            self.key.metadata()["verifier"],
            hmac.new(self.seed, verifier, hashlib.sha256).hexdigest(),
        )
        self.assertNotEqual(
            self.key.metadata()["verifier"],
            hmac.new(self.seed, b"", hashlib.sha256).hexdigest(),
        )
        self.assertNotIn(
            "parking-report/photo-vehicle-key-verifier/v1",
            self.key.identity(self.photo, "1234567"),
        )
