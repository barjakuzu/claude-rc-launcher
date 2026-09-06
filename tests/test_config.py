"""config.CLAUDE_BIN must expand a leading ~ so RC_CLAUDE_BIN=~/... keeps
working (subprocess argv never does shell-style ~ expansion on its own)."""
import importlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


class ClaudeBinExpandsUserTest(unittest.TestCase):
    def setUp(self):
        self._orig_env = os.environ.get("RC_CLAUDE_BIN")
        # importlib.reload mutates the *same* config module object every
        # other module already holds a reference to (e.g. sessions.py's
        # `from config import CLAUDE_BIN`), so a test that reloads it must
        # put every attribute back exactly as it found it - not just
        # CLAUDE_BIN - or a later test importing `config` picks up
        # whatever env this test happened to leave behind.
        self._orig_config_attrs = vars(config).copy()

    def tearDown(self):
        if self._orig_env is None:
            os.environ.pop("RC_CLAUDE_BIN", None)
        else:
            os.environ["RC_CLAUDE_BIN"] = self._orig_env
        importlib.reload(config)
        # Belt-and-suspenders: confirm the reload actually restored the
        # module to its pre-test state (env-derived module-level
        # constants only - functions/classes are identity-stable across
        # reload of the same module and would spuriously mismatch here).
        for name, orig_value in self._orig_config_attrs.items():
            if name.startswith("__") or callable(orig_value):
                continue
            self.assertEqual(
                getattr(config, name), orig_value,
                f"config.{name} was not restored after reload")

    def test_tilde_path_is_expanded(self):
        os.environ["RC_CLAUDE_BIN"] = "~/bin/claude"
        importlib.reload(config)
        self.assertEqual(config.CLAUDE_BIN, os.path.expanduser("~/bin/claude"))
        self.assertFalse(config.CLAUDE_BIN.startswith("~"))

    def test_plain_command_name_is_unaffected(self):
        os.environ["RC_CLAUDE_BIN"] = "claude"
        importlib.reload(config)
        self.assertEqual(config.CLAUDE_BIN, "claude")


if __name__ == "__main__":
    unittest.main()
