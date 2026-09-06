"""panes.py: tmux pane inventory + claude-pid-to-pane mapping via a
parent-pid walk, for adopting external (non-launcher) Claude Code
sessions into Preview/terminal/keys."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

import panes


class FakeRun:
    def __init__(self, stdout="", returncode=0, raises=None):
        self.stdout = stdout
        self.returncode = returncode
        self.raises = raises
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if self.raises:
            raise self.raises

        class R:
            pass
        r = R()
        r.returncode = self.returncode
        r.stdout = self.stdout
        r.stderr = ""
        return r


LIST_PANES_OUT = (
    "rc-portugal\t%3\t1001\t0\n"
    "mysession\t%7\t2001\t0\n"
)


class ListPanesTest(unittest.TestCase):
    def setUp(self):
        panes._cache = {"panes": [], "at": 0.0}

    def test_parses_tab_separated_rows(self):
        fake = FakeRun(stdout=LIST_PANES_OUT)
        rows = panes.list_panes(run=fake, now_fn=lambda: 1000.0)
        self.assertEqual(rows, [
            {"session_name": "rc-portugal", "pane_id": "%3", "pane_pid": 1001, "window_index": "0"},
            {"session_name": "mysession", "pane_id": "%7", "pane_pid": 2001, "window_index": "0"},
        ])
        self.assertEqual(fake.calls, [
            ["tmux", "list-panes", "-a", "-F",
             "#{session_name}\t#{pane_id}\t#{pane_pid}\t#{window_index}"],
        ])

    def test_skips_malformed_lines(self):
        fake = FakeRun(stdout="rc-portugal\t%3\tnotapid\t0\nonly\tthreefields\there\n")
        rows = panes.list_panes(run=fake, now_fn=lambda: 1000.0)
        self.assertEqual(rows, [])

    def test_nonzero_exit_returns_stale_cache(self):
        panes._cache = {"panes": [{"session_name": "old", "pane_id": "%1",
                                    "pane_pid": 1, "window_index": "0"}], "at": 990.0}
        fake = FakeRun(returncode=1)
        rows = panes.list_panes(run=fake, now_fn=lambda: 991.0)
        self.assertEqual(rows[0]["session_name"], "old")

    def test_missing_tmux_binary_returns_empty_or_stale(self):
        fake = FakeRun(raises=OSError("no tmux"))
        rows = panes.list_panes(run=fake, now_fn=lambda: 1000.0)
        self.assertEqual(rows, [])

    def test_caches_for_15_seconds(self):
        fake = FakeRun(stdout=LIST_PANES_OUT)
        clock = {"t": 1000.0}
        panes.list_panes(run=fake, now_fn=lambda: clock["t"])
        clock["t"] = 1010.0
        panes.list_panes(run=fake, now_fn=lambda: clock["t"])
        self.assertEqual(len(fake.calls), 1)
        clock["t"] = 1016.0
        panes.list_panes(run=fake, now_fn=lambda: clock["t"])
        self.assertEqual(len(fake.calls), 2)


class PaneForPidTest(unittest.TestCase):
    def setUp(self):
        panes._cache = {"panes": [], "at": 0.0}

    def test_direct_match_on_pane_pid(self):
        rows = [{"session_name": "mysession", "pane_id": "%7", "pane_pid": 2001, "window_index": "0"}]
        result = panes.pane_for_pid(2001, panes=rows)
        self.assertEqual(result["session_name"], "mysession")

    def test_walks_parent_chain_to_find_pane_pid(self):
        rows = [{"session_name": "mysession", "pane_id": "%7", "pane_pid": 2001, "window_index": "0"}]
        # claude pid 2003's parent is 2001 (the pane's shell) — one hop.
        ppids = {2003: 2001}
        result = panes.pane_for_pid(2003, panes=rows, read_ppid=lambda pid, run=None: ppids.get(pid))
        self.assertEqual(result["pane_id"], "%7")

    def test_no_match_returns_none(self):
        rows = [{"session_name": "mysession", "pane_id": "%7", "pane_pid": 2001, "window_index": "0"}]
        result = panes.pane_for_pid(9999, panes=rows, read_ppid=lambda pid, run=None: None)
        self.assertIsNone(result)

    def test_stops_at_max_walk_instead_of_looping_forever(self):
        rows = []
        calls = {"n": 0}

        def read_ppid(pid, run=None):
            calls["n"] += 1
            return pid + 1  # never matches, never terminates on its own

        result = panes.pane_for_pid(1, panes=rows, read_ppid=read_ppid)
        self.assertIsNone(result)
        self.assertLessEqual(calls["n"], panes.MAX_WALK)

    def test_default_read_ppid_parses_proc_status(self):
        # Exercise the real default (Linux /proc path) against this test
        # process's own pid, which always has a readable PPid.
        ppid = panes._default_read_ppid(os.getpid())
        self.assertIsInstance(ppid, int)

    def test_default_read_ppid_falls_back_to_ps_when_no_proc(self):
        fake = FakeRun(stdout="4242\n")
        # Force the /proc path to fail by asking about a pid that can't
        # possibly exist, so only the `ps` fallback can produce a result.
        result = panes._default_read_ppid(2**30, run=fake)
        self.assertEqual(result, 4242)
        self.assertEqual(fake.calls, [["ps", "-o", "ppid=", "-p", str(2**30)]])
