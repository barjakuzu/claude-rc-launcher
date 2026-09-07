import os
import unittest

INSTALL_SH = os.path.join(os.path.dirname(__file__), "..", "install.sh")


class InstallRcHookTest(unittest.TestCase):
    def setUp(self):
        with open(INSTALL_SH) as f:
            self.src = f.read()

    def test_installs_rc_hook_to_bin_dir_0700(self):
        self.assertIn('cp "$SCRIPT_DIR/hooks/rc-hook" "$RC_HOME/bin/rc-hook"', self.src)
        self.assertIn('chmod 700 "$RC_HOME/bin/rc-hook"', self.src)

    def test_creates_events_dir_0700(self):
        self.assertIn('mkdir -p "$RC_HOME/events"', self.src)
        self.assertIn('chmod 700 "$RC_HOME/events"', self.src)

    def test_prints_snippet_path(self):
        self.assertIn("docs/hooks/settings.snippet.json", self.src)


if __name__ == "__main__":
    unittest.main()
