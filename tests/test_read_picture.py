"""Synthetic image-decoding contract for api106.read_picture (refs #109).

Every image is generated in memory. No real photos, no network.
"""

import io
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from parking_report import api106

ORIENTATION = 0x0112
GPS_IFD = 0x8825


def jpeg_bytes(image, exif=None):
    out = io.BytesIO()
    if exif is None:
        image.save(out, "JPEG", quality=95)
    else:
        image.save(out, "JPEG", quality=95, exif=exif)
    return out.getvalue()


def png_bytes(image):
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def gps_exif(lat, lon):
    exif = Image.Exif()
    gps = exif.get_ifd(GPS_IFD)
    gps[1], gps[3] = "N", "E"
    gps[2] = (float(int(lat)), float(round((lat % 1) * 60, 4)), 0.0)
    gps[4] = (float(int(lon)), float(round((lon % 1) * 60, 4)), 0.0)
    return exif


class ReadPictureTests(unittest.TestCase):
    def read(self, raw, name="photo.jpg"):
        with TemporaryDirectory() as directory:
            path = Path(directory) / name
            path.write_bytes(raw)
            return api106.read_picture(path)

    def decoded(self, out):
        image = Image.open(io.BytesIO(out))
        image.load()
        return image

    def test_exif_orientation_is_applied(self):
        exif = Image.Exif()
        exif[ORIENTATION] = 6  # rotate 90 degrees clockwise on display
        digest, gps, out = self.read(
            jpeg_bytes(Image.new("RGB", (40, 20), "red"), exif)
        )
        self.assertEqual(self.decoded(out).size, (20, 40))
        self.assertIsNone(gps)
        self.assertEqual(len(digest), 64)

    def test_output_is_rgb_jpeg_without_input_exif(self):
        exif = Image.Exif()
        exif[0x010E] = "ZZ synthetic description"
        source = Image.new("RGBA", (30, 30), (0, 128, 0, 100))
        _, _, out = self.read(png_bytes(source), "photo.png")
        self.assertTrue(out.startswith(b"\xff\xd8"))
        image = self.decoded(out)
        self.assertEqual((image.format, image.mode), ("JPEG", "RGB"))
        _, _, out = self.read(jpeg_bytes(Image.new("RGB", (30, 30)), exif))
        self.assertNotIn(0x010E, self.decoded(out).getexif())

    def test_large_input_is_bounded_to_2560(self):
        _, _, out = self.read(
            png_bytes(Image.new("RGB", (3000, 1500), "white")), "photo.png"
        )
        self.assertEqual(self.decoded(out).size, (2560, 1280))

    def test_gps_inside_and_outside_the_supported_area(self):
        image = Image.new("RGB", (10, 10))
        _, gps, _ = self.read(jpeg_bytes(image, gps_exif(31.8, 35.2)))
        self.assertAlmostEqual(gps[0], 31.8, places=3)
        self.assertAlmostEqual(gps[1], 35.2, places=3)
        with self.assertRaises(api106.InputError):
            self.read(jpeg_bytes(image, gps_exif(32.5, 34.9)))

    def test_truncated_jpeg_is_rejected(self):
        raw = jpeg_bytes(Image.effect_noise((256, 256), 64).convert("RGB"))
        with self.assertRaises(api106.InputError):
            self.read(raw[: len(raw) // 2])

    def test_invalid_empty_and_unsupported_inputs_are_rejected(self):
        gif = io.BytesIO()
        Image.new("P", (5, 5)).save(gif, "GIF")
        for raw in (
            b"",
            b"ZZ not an image",
            b"\xff\xd8\xff" + b"\x00" * 32,
            gif.getvalue(),
        ):
            with self.subTest(prefix=raw[:8]), self.assertRaises(api106.InputError):
                self.read(raw)

    def test_decompression_bomb_is_rejected_before_decode(self):
        bomb = png_bytes(Image.new("1", (7000, 6000)))  # 42M pixels > MAX_IMAGE_PIXELS
        self.assertLess(len(bomb), api106.MAX_IMAGE)
        with self.assertRaises(api106.InputError):
            self.read(bomb, "photo.png")


ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"


class SupportedPythonTests(unittest.TestCase):
    """Python 3.10 is dropped so no supported runtime resolves 12.x (#109, #122)."""

    def source_file(self, name):
        path = ROOT / name
        if not path.exists():  # the Docker test stage copies tests/ only
            self.skipTest(f"{name} is not in this layout")
        return path.read_text(encoding="utf-8")

    def api_entries(self):
        block = re.search(
            r"^api = \[(.*?)^\]",
            self.source_file("pyproject.toml"),
            re.DOTALL | re.MULTILINE,
        )
        self.assertIsNotNone(block)
        return re.findall(r'^\s*"([^"]+)"', block.group(1), re.MULTILINE)

    def test_requires_python_is_311_or_later(self):
        text = self.source_file("pyproject.toml")
        self.assertIn('requires-python = ">=3.11"', text)
        self.assertIn('python_version = "3.11"', text)  # mypy target

    def test_pillow_has_one_patched_range_without_a_fallback(self):
        pillow = [e for e in self.api_entries() if e.lower().startswith("pillow")]
        self.assertEqual(pillow, ["Pillow>=13,<14"])

    def test_ci_matrix_starts_at_311(self):
        text = self.source_file(".github/workflows/tests.yml")
        self.assertIn("python-version: ['3.11', '3.12', '3.13']", text)
        self.assertNotIn("3.10", text)

    def test_docker_bases_are_supported_pythons(self):
        for name in ("Dockerfile", "Dockerfile.render"):
            bases = re.findall(
                r"^FROM python:3\.(\d+)", self.source_file(name), re.MULTILINE
            )
            self.assertTrue(bases, name)
            self.assertTrue(all(int(minor) >= 11 for minor in bases), name)

    def test_installed_pillow_is_patched(self):
        import PIL

        self.assertGreaterEqual(int(PIL.__version__.split(".")[0]), 13)


if __name__ == "__main__":
    unittest.main()
