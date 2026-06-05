"""Unit tests for the pure workspace-to-editor matching policy (matching.py).

The probe boundary (open command connection + eval) is faked with a dict lookup,
so these tests exercise the selection rules in isolation — no UE editor, no sockets.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matching import decide_node, parse_project_path, stem_eq, uproject_stem


def fake_probe(mapping):
    """Probe over {node_id: uproject_path | None}; missing id -> None (inconclusive)."""
    return lambda node_id: mapping.get(node_id)


class UprojectStemTests(unittest.TestCase):
    def test_extracts_stem_from_full_path(self):
        self.assertEqual(uproject_stem("D:/repository/uereb/uereb.uproject"), "uereb")

    def test_stem_is_filename_not_parent_dir(self):
        # The stem must come from the .uproject filename, not the containing folder.
        self.assertEqual(uproject_stem("D:/games/MyFolder/CoolGame.uproject"), "CoolGame")


class StemEqTests(unittest.TestCase):
    def test_case_insensitive_match(self):
        self.assertTrue(stem_eq("uereb", "UEREB"))

    def test_distinct_stems_do_not_match(self):
        self.assertFalse(stem_eq("uereb", "playground"))

    def test_none_never_matches(self):
        self.assertFalse(stem_eq(None, "uereb"))
        self.assertFalse(stem_eq("uereb", None))
        self.assertFalse(stem_eq(None, None))


class ParseProjectPathTests(unittest.TestCase):
    def test_unwraps_repr_quoted_string(self):
        # EvaluateStatement returns the repr of the expression (a quoted string).
        self.assertEqual(
            parse_project_path("'D:/repository/uereb/uereb.uproject'"),
            "D:/repository/uereb/uereb.uproject",
        )

    def test_accepts_bare_string(self):
        self.assertEqual(parse_project_path("D:/x/y.uproject"), "D:/x/y.uproject")

    def test_empty_result_is_inconclusive(self):
        self.assertIsNone(parse_project_path(""))
        self.assertIsNone(parse_project_path(None))
        self.assertIsNone(parse_project_path("''"))  # repr of an empty project path


class DecideNodeTests(unittest.TestCase):
    def test_single_match_selects_that_node(self):
        probe = fake_probe({"a": "D:/x/playground.uproject", "b": "D:/y/uereb.uproject"})
        self.assertEqual(decide_node(["a", "b"], "uereb", probe), ("b", "match"))

    def test_no_matching_project_is_no_match(self):
        probe = fake_probe({"a": "D:/x/playground.uproject"})
        self.assertEqual(decide_node(["a"], "uereb", probe), (None, "no_match"))

    def test_inconclusive_probe_is_not_a_mismatch(self):
        # R2: the correct editor mid-boot (probe -> None) must yield "inconclusive"
        # (retryable), never "no_match" (which would refuse the right editor).
        probe = fake_probe({"a": None})
        self.assertEqual(decide_node(["a"], "uereb", probe), (None, "inconclusive"))

    def test_match_wins_over_concurrent_inconclusive(self):
        probe = fake_probe({"a": None, "b": "D:/y/uereb.uproject"})
        self.assertEqual(decide_node(["a", "b"], "uereb", probe), ("b", "match"))

    def test_two_editors_same_project_is_ambiguous(self):
        probe = fake_probe({"a": "D:/one/uereb.uproject", "b": "D:/two/uereb.uproject"})
        self.assertEqual(decide_node(["a", "b"], "uereb", probe), (None, "ambiguous"))

    def test_empty_node_list_is_no_match(self):
        self.assertEqual(decide_node([], "uereb", fake_probe({})), (None, "no_match"))

    def test_raising_probe_is_inconclusive_not_fatal(self):
        # A probe that raises (e.g. command connect-back timeout) must degrade to
        # inconclusive, never propagate and discard the whole match attempt.
        def boom(_node_id):
            raise RuntimeError("connect-back failed")
        self.assertEqual(decide_node(["a"], "uereb", boom), (None, "inconclusive"))

    def test_match_survives_a_sibling_probe_that_raises(self):
        def probe(node_id):
            if node_id == "a":
                raise RuntimeError("flaky foreign editor")
            return "D:/y/uereb.uproject"
        self.assertEqual(decide_node(["a", "b"], "uereb", probe), ("b", "match"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
