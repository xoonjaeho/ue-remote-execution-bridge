"""Orchestration tests for `server._open_remote` — the discovery/settle/cache/probe
wiring that pure-policy `matching.py` tests don't cover.

A fake RemoteExecution replaces the UDP/TCP transport, so discovery -> probe -> decide
-> cache -> connect runs without a UE editor. Importing `server` no longer starts the
heartbeat thread (`_start_heartbeat` is __main__-only), so nothing mutates the cache
underneath these tests.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server  # noqa: E402


class FakeRemote:
    """Minimal stand-in for execute.RemoteExecution, driven by a node->project map.

    `projects[node_id] is None` simulates an editor whose identity eval fails
    (mid-boot / Python not ready) -> the probe is inconclusive.
    """
    def __init__(self, nodes, projects, journal):
        self._nodes = list(nodes)
        self._projects = dict(projects)
        self._j = journal
        self._connected = None

    def start(self):
        self._j["starts"] += 1

    @property
    def remote_nodes(self):
        return [{"node_id": n} for n in self._nodes]

    def open_command_connection(self, node_id):
        self._connected = node_id
        self._j["opens"].append(node_id)

    def close_command_connection(self):
        self._connected = None

    def run_command(self, code, unattended=True, exec_mode=None, raise_on_failure=False):
        self._j["evals"].append(self._connected)
        path = self._projects.get(self._connected)
        if path is None:
            return {"success": False, "result": None}
        return {"success": True, "result": repr(path)}

    def stop(self):
        self._j["stops"] += 1


class OpenRemoteTests(unittest.TestCase):
    def setUp(self):
        self._orig_RE = server.RemoteExecution
        server._DISCOVERY_SETTLE = 0.0  # no real sleep in tests
        server._ALLOW_ANY = False
        server._PROJECT_STEM = "uereb"
        server._conn_decision = None
        self.journal = {"starts": 0, "stops": 0, "opens": [], "evals": []}

    def tearDown(self):
        server.RemoteExecution = self._orig_RE

    def _install(self, *fakes):
        """Each _open_remote() call constructs one RemoteExecution -> hand it the next fake."""
        queue = list(fakes)
        server.RemoteExecution = lambda cfg=None: queue.pop(0)

    def _fake(self, nodes, projects):
        return FakeRemote(nodes, projects, self.journal)

    def test_match_connects_to_the_matching_editor(self):
        self._install(self._fake(["A", "B"],
                                  {"A": "X/playground.uproject", "B": "Y/uereb.uproject"}))
        result = server._open_remote(5.0)
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[1], "B")
        self.assertIn("B", self.journal["evals"])  # actually probed B's identity

    def test_no_editor_returns_none(self):
        self._install(self._fake([], {}))
        self.assertIsNone(server._open_remote(0.3))

    def test_wrong_project_is_refused(self):
        self._install(self._fake(["A"], {"A": "X/playground.uproject"}))
        result = server._open_remote(5.0)
        self.assertIsInstance(result, str)
        self.assertIn("uereb", result)

    def test_no_match_is_not_cached_then_recovers_when_match_appears(self):
        # #1 regression: a non-match must NOT freeze a cached refusal. The next call
        # (matching editor now present) must re-probe and match.
        self._install(
            self._fake(["B"], {"B": "X/playground.uproject"}),
            self._fake(["A", "B"], {"A": "Y/uereb.uproject", "B": "X/playground.uproject"}),
        )
        self.assertIsInstance(server._open_remote(5.0), str)        # call 1: refused
        recovered = server._open_remote(5.0)                        # call 2: matches A
        self.assertIsInstance(recovered, tuple)
        self.assertEqual(recovered[1], "A")

    def test_no_match_reprobes_on_repeat_same_set(self):
        # no_match must NOT be cached: a repeat call with the SAME still-non-matching set
        # must re-probe (so a later-arriving match is never frozen out by a stale refusal).
        self._install(
            self._fake(["B"], {"B": "X/playground.uproject"}),
            self._fake(["B"], {"B": "X/playground.uproject"}),
        )
        server._open_remote(5.0)
        evals_after_first = list(self.journal["evals"])
        server._open_remote(5.0)  # same set, still non-matching
        self.assertGreater(len(self.journal["evals"]), len(evals_after_first))

    def test_cache_hit_skips_reprobe(self):
        projects = {"A": "X/playground.uproject", "B": "Y/uereb.uproject"}
        self._install(self._fake(["A", "B"], projects), self._fake(["A", "B"], projects))
        server._open_remote(5.0)                       # populates cache (match B)
        evals_after_first = list(self.journal["evals"])
        result = server._open_remote(5.0)              # same node set -> cache hit
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[1], "B")
        self.assertEqual(self.journal["evals"], evals_after_first)  # no re-probe

    def test_unreadable_identity_is_inconclusive_retry(self):
        self._install(self._fake(["A"], {"A": None}))  # eval fails -> inconclusive
        result = server._open_remote(5.0)
        self.assertIsInstance(result, str)
        self.assertIn("readable", result.lower())

    def test_two_same_project_editors_are_ambiguous(self):
        self._install(self._fake(["A", "B"],
                                  {"A": "One/uereb.uproject", "B": "Two/uereb.uproject"}))
        result = server._open_remote(5.0)
        self.assertIsInstance(result, str)
        self.assertIn("Multiple", result)

    def test_unresolved_workspace_refuses(self):
        server._PROJECT_STEM = None
        self._install(self._fake(["A"], {"A": "X/playground.uproject"}))
        result = server._open_remote(5.0)
        self.assertIsInstance(result, str)
        self.assertIn("UE_PROJECT_ROOT", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
