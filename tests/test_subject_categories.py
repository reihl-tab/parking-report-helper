"""Synthetic GetSubjectCategories transport, parsing and fingerprint tests."""

import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from parking_report.api106 import APIError, InputError, Municipality, NoRedirect
from parking_report.subjects import (
    catalog_fingerprint,
    flatten_category_tree,
    load_catalog,
)

CREDS = {
    "subscription_key": "synthetic-key",
    "login": {"UserName": "test", "Password": "test"},
}
TOKEN = "synthetic/token?secret=1"


def tree():
    """Synthetic three-level tree, including a repeated subject code."""
    return [
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "parentId": None,
            "categoryName": "ניקיון",
            "categoryCode": "10",
            "level": 1,
            "parentCategoryCode": None,
            "subjectCode": None,
            "icon": "",
            "categoryArabicName": "",
            "order": 1,
            "children": [
                {
                    "categoryName": "גזם / גרוטאות",
                    "categoryCode": "11",
                    "level": 2,
                    "subjectCode": None,
                    "children": [
                        {
                            "categoryName": "אשפה - גרוטאות",
                            "categoryCode": "12",
                            "level": 3,
                            "subjectCode": "1015",
                            "children": [],
                        }
                    ],
                }
            ],
        },
        {
            "categoryName": "חניה אסורה",
            "categoryCode": "20",
            "level": 1,
            "subjectCode": None,
            "children": [
                {
                    "categoryName": "חניה על מדרכה",
                    "categoryCode": "21",
                    "level": 2,
                    "subjectCode": "1145",
                    "children": [],
                },
                {
                    "categoryName": "חניה על מדרכה",
                    "categoryCode": "22",
                    "level": 2,
                    "subjectCode": "1145",
                    "children": [],
                },
            ],
        },
    ]


_EMPTY = object()


class Opener:
    def __init__(self, payload=_EMPTY, error=None):
        self.payload = [] if payload is _EMPTY else payload
        self.error = error
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error:
            raise self.error
        return io.BytesIO(json.dumps(self.payload).encode())


class SubjectCategoryTransportTests(unittest.TestCase):
    def call(self, opener, token=TOKEN):
        with patch(
            "parking_report.api106.urllib.request.build_opener", return_value=opener
        ) as factory:
            result = Municipality(CREDS).list_subject_categories(token)
        self.assertIsInstance(factory.call_args.args[0], NoRedirect)
        return result

    def test_exact_get_shape_and_token_in_query(self):
        opener = Opener(tree())
        self.call(opener)
        request, _timeout = opener.requests[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertIn("GetSubjectCategories?token=", request.full_url)
        self.assertIn("synthetic%2Ftoken%3Fsecret%3D1", request.full_url)
        self.assertNotIn(TOKEN, request.full_url)
        self.assertEqual(
            request.headers.get("Ocp-apim-subscription-key"), "synthetic-key"
        )
        self.assertEqual(request.headers.get("User-agent"), "App 106")
        self.assertEqual(request.headers.get("Content-type"), "text/plain")

    def test_success_returns_raw_nodes(self):
        result = self.call(Opener(tree()))
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["categoryName"], "ניקיון")

    def test_http_error_leaks_no_token_or_url(self):
        opener = Opener(error=urllib.error.HTTPError("u", 401, "x", None, None))
        with self.assertRaises(APIError) as ctx:
            self.call(opener)
        self.assertNotIn("synthetic", str(ctx.exception))
        self.assertNotIn("token", str(ctx.exception).lower())
        self.assertIsNone(ctx.exception.__cause__)
        self.assertTrue(ctx.exception.__suppress_context__)

    def test_non_list_response_rejected(self):
        for payload in ({"Success": True}, "text", 42, None):
            with self.subTest(payload=payload):
                with self.assertRaises(APIError):
                    self.call(Opener(payload))

    def test_oversized_response_rejected(self):
        opener = Opener([{"categoryName": "א"}] * 1001)
        with self.assertRaises(APIError):
            self.call(opener)

    def test_non_dict_nodes_rejected(self):
        with self.assertRaises(APIError):
            self.call(Opener([["not", "a", "dict"]]))

    def test_over_16mb_response_rejected(self):
        class BigOpener(Opener):
            def open(self, request, timeout):
                self.requests.append((request, timeout))
                return self.stream

        class SizedStream(io.BytesIO):
            def read(self, size=-1):
                sizes.append(size)
                return super().read(size)

        sizes = []
        opener = BigOpener()
        # A valid JSON prefix: without the length check this would parse as [].
        opener.stream = SizedStream(b"[]" + b" " * (16 * 1024 * 1024))
        with self.assertRaises(APIError):
            self.call(opener)
        self.assertEqual(sizes, [16 * 1024 * 1024 + 1])

    def test_invalid_token_rejected(self):
        for token in ("", None, 42, "has space", "x" * 4097, "tab\ttoken"):
            with self.subTest(token=token):
                with self.assertRaises(InputError):
                    Municipality(CREDS).list_subject_categories(token)


class FlattenCategoryTreeTests(unittest.TestCase):
    def test_flatten_preserves_subjects_and_duplicate_codes(self):
        rows = flatten_category_tree(tree())
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            rows[0],
            {
                "code": "1015",
                "name": "אשפה - גרוטאות",
                "path": ["ניקיון", "גזם / גרוטאות", "אשפה - גרוטאות"],
            },
        )
        duplicate_paths = [row["path"] for row in rows if row["code"] == "1145"]
        self.assertEqual(len(duplicate_paths), 2)

    def test_grouping_nodes_produce_no_rows(self):
        self.assertEqual(flatten_category_tree([{"categoryName": "אחר"}]), [])

    def test_malformed_nodes_fail_closed(self):
        bad = [
            [{"subjectCode": "1"}],
            [{"categoryName": ""}],
            [{"categoryName": "  ", "subjectCode": "1"}],
            [{"categoryName": "א", "subjectCode": 1145}],
            [{"categoryName": "א", "subjectCode": "11x"}],
            [{"categoryName": "א", "children": "nope"}],
            [{"categoryName": "א", "children": 0}],
            [{"categoryName": "א", "children": False}],
            ["not a dict"],
            "not a list",
            [{"categoryName": "א", "children": [{"subjectCode": "1"}]}],
        ]
        for payload in bad:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    flatten_category_tree(payload)

    def test_none_children_accepted(self):
        rows = flatten_category_tree(
            [{"categoryName": "א", "subjectCode": "1", "children": None}]
        )
        self.assertEqual(len(rows), 1)

    def test_deep_tree_fails_closed_as_value_error(self):
        node = {"categoryName": "עלה", "subjectCode": "1"}
        for _ in range(11):
            node = {"categoryName": "רמה", "children": [node]}
        with self.assertRaises(ValueError):
            flatten_category_tree([node])

    def test_row_cap_fails_closed(self):
        nodes = [
            {"categoryName": "א", "subjectCode": str(100000 + i)} for i in range(10001)
        ]
        with self.assertRaises(ValueError):
            flatten_category_tree(nodes)


class CatalogFingerprintTests(unittest.TestCase):
    def test_fingerprint_is_order_independent_and_stable(self):
        rows = flatten_category_tree(tree())
        self.assertEqual(
            catalog_fingerprint(rows), catalog_fingerprint(list(reversed(rows)))
        )
        self.assertEqual(
            catalog_fingerprint(rows),
            "8caee39d5cd26fd03d564b28a9bffc8ef4c30034df0f8f7a764927eb1f7de593",
        )

    def test_fingerprint_detects_name_change(self):
        changed = tree()
        changed[0]["children"][0]["children"][0]["categoryName"] = "אשפה - גרוטאות "
        self.assertNotEqual(
            catalog_fingerprint(flatten_category_tree(tree())),
            catalog_fingerprint(flatten_category_tree(changed)),
        )

    def test_captured_catalog_has_known_shape_and_fingerprint(self):
        rows = [row for group in load_catalog().values() for row in group]
        self.assertEqual(len(rows), 86)
        self.assertEqual(
            catalog_fingerprint(rows),
            "77613b820e657c43c7df6558a5f1d24c3a4bcd1ea7d691bb4bb55db2607c66ab",
        )


class SubjectCategoryMockE2ETests(unittest.TestCase):
    def test_client_tree_matches_captured_row_shape(self):
        """Live flattened rows compare directly with captured catalog rows."""
        opener = Opener(tree())
        with patch(
            "parking_report.api106.urllib.request.build_opener", return_value=opener
        ):
            raw = Municipality(CREDS).list_subject_categories(TOKEN)
        rows = flatten_category_tree(raw)
        self.assertEqual(
            catalog_fingerprint(rows), catalog_fingerprint(list(reversed(rows)))
        )
        for row in rows:
            self.assertEqual(set(row), {"code", "name", "path"})
            self.assertTrue(row["code"].isdigit())
            self.assertEqual(row["path"][-1], row["name"])


if __name__ == "__main__":
    unittest.main()
