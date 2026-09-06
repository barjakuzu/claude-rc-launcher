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

    def tearDown(self):
        if self._orig_env is None:
            os.environ.pop("RC_CLAUDE_BIN", None)
        else:
            os.environ["RC_CLAUDE_BIN"] = self._orig_env
        importlib.reload(config)

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
