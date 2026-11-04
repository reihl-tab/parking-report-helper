"""Complexity ratchet for #66 (src/) and #288 (scripts/): nothing new above
radon rank C.

Cyclomatic complexity is computed with the standard library so this test
needs no extra dependency in CI. The counter follows radon's ``cc`` rules
(radon 6.0.1); it was checked to give the same score as ``radon cc`` for
every block in src/, tests/ and scripts/ when it was added, and again for
every block in scripts/ when scripts/ joined the ratchet (#288).

Rules, for src/ (GRANDFATHERED) and scripts/ (SCRIPTS_GRANDFATHERED) alike:
- any function, method or class scoring above C (more than 20) must be
  grandfathered, and
- a grandfathered block may not score higher than its recorded number.
Lower a recorded number (or remove the entry) when a block improves.
Nested functions are not scored on their own, as in ``radon cc``.
"""

import ast
import unittest
from pathlib import Path

import parking_report

# The installed package, so this also runs in the Docker test image, which
# has the package installed but no src/ tree.
SRC = Path(parking_report.__file__).resolve().parent
LIMIT = 20  # radon rank C is 11-20; D starts at 21

# (file relative to src/parking_report, block name): highest allowed score.
GRANDFATHERED = {
    ("api106.py", "Addresses"): 22,
    ("api106.py", "Addresses.resolve"): 31,
    ("api106.py", "Municipality.list_cases"): 29,
    ("cli.py", "main"): 23,
}

# The repository's scripts/ directory, beside tests/. The Docker test image
# copies scripts/ next to tests/ too; where it is absent the scripts ratchet
# skips rather than fails.
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

# (file relative to scripts/, block name): highest allowed score. Empty since
# #288 split gen_manpages.render_page (45) and ci_test_report.summary (22).
SCRIPTS_GRANDFATHERED: dict[tuple[str, str], int] = {}

_FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)


def _decisions(node):
    """Decision points in node, not entering nested functions or classes."""
    if isinstance(node, (*_FUNC, ast.ClassDef)):
        return 0
    if isinstance(node, ast.Assert):
        return 1  # radon counts an assert once and does not look inside it
    count = 0
    if isinstance(node, ast.Try):
        count += len(node.handlers) + bool(node.orelse)
    elif isinstance(node, ast.BoolOp):
        count += len(node.values) - 1
    elif isinstance(node, (ast.If, ast.IfExp)):
        count += 1
    elif isinstance(node, ast.Match):
        wildcard = any(
            getattr(case.pattern, "pattern", False) is None for case in node.cases
        )
        count += max(0, len(node.cases) - wildcard)
    elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
        count += 1 + bool(node.orelse)
    elif isinstance(node, ast.comprehension):
        count += 1 + len(node.ifs)
    return count + sum(_decisions(child) for child in ast.iter_child_nodes(node))


def _defs(node):
    """Function and class definitions reachable without entering one."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (*_FUNC, ast.ClassDef)):
            yield child
        else:
            yield from _defs(child)


def _function_cc(node):
    return 1 + sum(_decisions(child) for child in node.body)


def blocks(tree):
    """Yield (name, score) for each block, as ``radon cc`` lists them."""
    for node in _defs(tree):
        if isinstance(node, _FUNC):
            yield node.name, _function_cc(node)
            continue
        total = 1
        methods = []
        for child in node.body:
            if isinstance(child, ast.ClassDef):
                continue
            if isinstance(child, _FUNC):
                found = [child]
            else:
                total += _decisions(child)
                found = [d for d in _defs(child) if isinstance(d, _FUNC)]
            for method in found:
                score = _function_cc(method)
                total += score
                methods.append((f"{node.name}.{method.name}", score))
        if methods:  # radon scores a class by its average method, plus one
            total = int(total / len(methods)) + (len(methods) > 1)
        yield node.name, total
        yield from methods


def tree_scores(root):
    """{(file relative to root, block name): score} for every .py under root."""
    scores = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name, score in blocks(tree):
            scores[(path.relative_to(root).as_posix(), name)] = score
    return scores


def src_scores():
    return tree_scores(SRC)


def over_limit(scores, grandfathered):
    """Blocks above LIMIT that are not grandfathered."""
    return {
        key: score
        for key, score in scores.items()
        if score > LIMIT and key not in grandfathered
    }


def worse_than_recorded(scores, grandfathered):
    """Grandfathered blocks now scoring above their recorded number."""
    return {
        key: (scores[key], allowed)
        for key, allowed in grandfathered.items()
        if key in scores and scores[key] > allowed
    }


SAMPLE = """
def a(x):
    assert x and y
    f = lambda q: q if q else 1
    try:
        pass
    except A:
        pass
    else:
        pass
    for i in x:
        pass
    else:
        pass
    match x:
        case 1:
            pass
        case _:
            pass
    def inner():
        if x:
            return 1
    return [i for i in x if i] or None
class C:
    x = 1 if a else 2
    def m1(self):
        return a or b
    def m2(self):
        while x:
            pass
"""


class TestComplexityCounter(unittest.TestCase):
    def test_matches_radon_on_sample(self):
        # Expected values are radon 6.0.1 `cc` output for SAMPLE.
        self.assertEqual(
            list(blocks(ast.parse(SAMPLE))),
            [("a", 11), ("C", 4), ("C.m1", 2), ("C.m2", 2)],
        )


class TestComplexityRatchet(unittest.TestCase):
    def setUp(self):
        self.scores = src_scores()

    def test_nothing_new_above_c(self):
        over = over_limit(self.scores, GRANDFATHERED)
        self.assertEqual(over, {}, "new block above radon rank C; split it")

    def test_grandfathered_blocks_do_not_get_worse(self):
        worse = worse_than_recorded(self.scores, GRANDFATHERED)
        self.assertEqual(worse, {}, "grandfathered block got more complex")

    def test_grandfathered_entries_still_exist(self):
        missing = sorted(set(GRANDFATHERED) - set(self.scores))
        self.assertEqual(missing, [], "remove stale GRANDFATHERED entries")


class TestScriptsComplexityRatchet(unittest.TestCase):
    """The same rules for scripts/ (#288)."""

    def setUp(self):
        if not SCRIPTS.is_dir():
            self.skipTest("scripts/ is not present in this layout")
        self.scores = tree_scores(SCRIPTS)

    def test_scripts_are_scored(self):
        self.assertIn(("gen_manpages.py", "render_page"), self.scores)

    def test_nothing_new_above_c(self):
        over = over_limit(self.scores, SCRIPTS_GRANDFATHERED)
        self.assertEqual(over, {}, "new scripts/ block above radon rank C; split it")

    def test_grandfathered_blocks_do_not_get_worse(self):
        worse = worse_than_recorded(self.scores, SCRIPTS_GRANDFATHERED)
        self.assertEqual(worse, {}, "grandfathered scripts/ block got more complex")

    def test_grandfathered_entries_still_exist(self):
        missing = sorted(set(SCRIPTS_GRANDFATHERED) - set(self.scores))
        self.assertEqual(missing, [], "remove stale SCRIPTS_GRANDFATHERED entries")


if __name__ == "__main__":
    unittest.main()
