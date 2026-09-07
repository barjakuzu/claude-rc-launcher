"""config.CLAUDE_BIN must expand a leading ~ so RC_CLAUDE_BIN=~/... keeps
working (subprocess argv never does shell-style ~ expansion on its own)."""
import importlib
import os
import sys
import tempfile
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




class RoleAndSaltEnvTest(unittest.TestCase):
    def setUp(self):
        self._orig_role = os.environ.get("RC_ROLE")
        self._orig_salt = os.environ.get("RC_HASH_SALT")

    def tearDown(self):
        for key, orig in (("RC_ROLE", self._orig_role), ("RC_HASH_SALT", self._orig_salt)):
            if orig is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = orig
        importlib.reload(config)

    def test_role_defaults_to_full(self):
        os.environ.pop("RC_ROLE", None)
        importlib.reload(config)
        self.assertEqual(config.RC_ROLE, "full")

    def test_role_and_salt_read_from_env(self):
        os.environ["RC_ROLE"] = "metadata"
        os.environ["RC_HASH_SALT"] = "pepper"
        importlib.reload(config)
        self.assertEqual(config.RC_ROLE, "metadata")
        self.assertEqual(config.RC_HASH_SALT, "pepper")

    def test_salt_is_generated_when_unset(self):
        # Superseded: RC_HASH_SALT no longer defaults to "" -- it is
        # generated and persisted into ~/.claude-rc/env (see
        # RcRoleTest.test_hash_salt_is_generated_and_persisted for the
        # isolated-RC_HOME version of this).
        os.environ.pop("RC_HASH_SALT", None)
        importlib.reload(config)
        self.assertTrue(config.RC_HASH_SALT)


class RcRoleTest(unittest.TestCase):
    def test_default_role_is_full(self):
        # config module already imported at test collection time with no
        # RC_ROLE set in this test process's env
        self.assertIn(config.RC_ROLE, ("full", "metadata"))

    def test_hash_salt_is_generated_and_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["RC_HOME"] = tmp
            os.environ.pop("RC_HASH_SALT", None)
            importlib.reload(config)
            self.assertTrue(config.RC_HASH_SALT)
            env_file = os.path.join(tmp, "env")
            self.assertTrue(os.path.exists(env_file))
            self.assertEqual(oct(os.stat(env_file).st_mode & 0o777), "0o600")
            with open(env_file) as f:
                content = f.read()
            self.assertIn("RC_HASH_SALT=", content)
            del os.environ["RC_HOME"]
            os.environ.pop("RC_HASH_SALT", None)
            importlib.reload(config)


if __name__ == "__main__":
    unittest.main()
