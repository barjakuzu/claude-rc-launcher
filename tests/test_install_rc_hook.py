import os
import subprocess
import tempfile
import unittest

INSTALL_SH = os.path.join(os.path.dirname(__file__), "..", "install.sh")


class InstallRcHookTest(unittest.TestCase):
    def setUp(self):
        with open(INSTALL_SH) as f:
            self.src = f.read()

    def test_installs_rc_hook_from_app_dir_not_script_dir(self):
        # Under `curl ... | bash`, SCRIPT_DIR resolves to the caller's cwd
        # (there is no on-disk install.sh to dirname), so hooks/rc-hook is
        # never found there. The clone always lands in APP_DIR.
        self.assertIn('cp "$APP_DIR/hooks/rc-hook" "$RC_HOME/bin/rc-hook"', self.src)
        self.assertNotIn('cp "$SCRIPT_DIR/hooks/rc-hook"', self.src)
        self.assertIn('chmod 700 "$RC_HOME/bin/rc-hook"', self.src)

    def test_missing_hook_source_is_non_fatal(self):
        self.assertIn('if [ -f "$APP_DIR/hooks/rc-hook" ]; then', self.src)
        self.assertIn("skipping hook spooler install", self.src)

    def test_curl_pipe_bash_style_install_does_not_abort_on_missing_hook(self):
        # Reproduce the exact failure mode: run just the rc-hook block with
        # `set -e`, SCRIPT_DIR pointing at an empty directory (as it would
        # when the script has no on-disk path, e.g. `curl | bash`), and
        # APP_DIR containing the real hooks payload. The block must not
        # abort and must still install the hook from APP_DIR.
        start = self.src.index("# ── rc-hook (Claude Code hook event spooler)")
        end = self.src.index("# ── PATH setup", start)
        block = self.src[start:end]
        self.assertTrue(block.strip())

        with tempfile.TemporaryDirectory() as tmp:
            script_dir = os.path.join(tmp, "cwd_no_hooks")
            app_dir = os.path.join(tmp, "app")
            rc_home = os.path.join(tmp, "rc_home")
            os.makedirs(script_dir)
            os.makedirs(os.path.join(app_dir, "hooks"))
            os.makedirs(rc_home)
            with open(os.path.join(app_dir, "hooks", "rc-hook"), "w") as f:
                f.write("#!/bin/sh\necho hook\n")

            script = (
                "set -e\n"
                "ok() { :; }\n"
                f'SCRIPT_DIR="{script_dir}"\n'
                f'APP_DIR="{app_dir}"\n'
                f'RC_HOME="{rc_home}"\n'
                + block
            )
            result = subprocess.run(
                ["bash", "-c", script], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(
                os.path.isfile(os.path.join(rc_home, "bin", "rc-hook")))

    def test_curl_pipe_bash_style_install_survives_no_hooks_dir_at_all(self):
        start = self.src.index("# ── rc-hook (Claude Code hook event spooler)")
        end = self.src.index("# ── PATH setup", start)
        block = self.src[start:end]

        with tempfile.TemporaryDirectory() as tmp:
            script_dir = os.path.join(tmp, "cwd_no_hooks")
            app_dir = os.path.join(tmp, "app_without_hooks")
            rc_home = os.path.join(tmp, "rc_home")
            os.makedirs(script_dir)
            os.makedirs(app_dir)
            os.makedirs(rc_home)

            script = (
                "set -e\n"
                "ok() { :; }\n"
                f'SCRIPT_DIR="{script_dir}"\n'
                f'APP_DIR="{app_dir}"\n'
                f'RC_HOME="{rc_home}"\n'
                + block
            )
            result = subprocess.run(
                ["bash", "-c", script], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(
                os.path.isfile(os.path.join(rc_home, "bin", "rc-hook")))

    def test_creates_events_dir_0700(self):
        self.assertIn('mkdir -p "$RC_HOME/events"', self.src)
        self.assertIn('chmod 700 "$RC_HOME/events"', self.src)

    def test_prints_snippet_path(self):
        self.assertIn("docs/hooks/settings.snippet.json", self.src)

    def test_provisions_rc_role_default_full_when_unset(self):
        self.assertIn("grep -q '^RC_ROLE=' \"$CONFIG_FILE\"", self.src)
        self.assertIn('echo "RC_ROLE=full" >> "$CONFIG_FILE"', self.src)

    def test_provisions_persistent_random_hash_salt_when_unset(self):
        self.assertIn("grep -q '^RC_HASH_SALT=' \"$CONFIG_FILE\"", self.src)
        self.assertIn("secrets.token_hex(16)", self.src)
        self.assertIn('RC_HASH_SALT=${RC_HASH_SALT}', self.src)


if __name__ == "__main__":
    unittest.main()
