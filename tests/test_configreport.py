"""configreport.py: per-device config parity report (git state, skills,
plugins, rules, settings) — read-only, stdlib only, never raises, never
reads skill file contents (one device-only skill can hold a live secret)."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import configreport


class _FakePluginResult:
    def __init__(self, returncode, stdout, stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _stub_run(cmd, **kw):
    """Default injected `run`: real git (so git-state tests exercise real
    plumbing) but a canned answer for `claude plugin list` and a hard
    failure on anything else, so the suite never shells out to a real
    `claude` binary and never needs one on PATH."""
    if cmd and cmd[0] == "git":
        return subprocess.run(cmd, capture_output=True, text=True, timeout=kw.get("timeout", 10))
    if cmd[:2] == ["claude", "plugin"]:
        return _FakePluginResult(0, "watch@official  v1.0\n")
    raise AssertionError("unexpected command in test stub: %r" % (cmd,))


def _git(cwd, *args):
    subprocess.run(["git", "-C", cwd] + list(args), check=True,
                    capture_output=True, text=True)


class CollectConfigReportTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.cfg = os.path.join(self.home, "claude-config")
        os.makedirs(os.path.join(self.cfg, "skills"))
        os.makedirs(os.path.join(self.cfg, "agents"))
        os.makedirs(os.path.join(self.cfg, "rules", "local"))
        os.makedirs(os.path.join(self.cfg, "config"))
        os.makedirs(os.path.join(self.cfg, "external", "watch"))

        # A normal skill: real dir with a SKILL.md.
        os.makedirs(os.path.join(self.cfg, "skills", "plain"))
        with open(os.path.join(self.cfg, "skills", "plain", "SKILL.md"), "w") as f:
            f.write("# plain\n")

        # A deps-installed skill: symlink into external/, currently intact.
        os.symlink(os.path.join(self.cfg, "external", "watch"),
                    os.path.join(self.cfg, "skills", "watch"))

        # A deps-missing skill: symlink into external/ that's broken
        # (as if deps/install.sh was never run on this device).
        os.symlink(os.path.join(self.cfg, "external", "never-cloned"),
                    os.path.join(self.cfg, "skills", "uninstalled-dep"))

        # A dangling-for-another-reason skill: broken symlink NOT into external/.
        os.symlink("/nonexistent-target-xyz",
                    os.path.join(self.cfg, "skills", "truly-broken"))

        with open(os.path.join(self.cfg, "agents", "claude.md"), "w") as f:
            f.write("# claude agent\n")
        with open(os.path.join(self.cfg, "rules", "local", ".gitkeep"), "w") as f:
            f.write("")
        with open(os.path.join(self.cfg, "plugins.txt"), "w") as f:
            f.write("watch@official\nghost@official\n")
        with open(os.path.join(self.cfg, "marketplaces.txt"), "w") as f:
            f.write("official https://example.com/official\n")
        settings = {"hooks": {"Stop": []}, "remoteControlAtStartup": True, "model": "claude-opus"}
        with open(os.path.join(self.cfg, "config", "settings.json"), "w") as f:
            json.dump(settings, f)

        # Device-only skill: gitignored, secret content that must never be
        # read (only its NAME may appear anywhere in the report).
        os.makedirs(os.path.join(self.cfg, "skills", "google-workspace"))
        with open(os.path.join(self.cfg, "skills", "google-workspace", "SKILL.md"), "w") as f:
            f.write("super-secret-token-do-not-read\n")

        with open(os.path.join(self.cfg, ".gitignore"), "w") as f:
            f.write("/external/\n/skills/google-workspace/\n")

        _git(self.cfg, "init", "-q")
        _git(self.cfg, "config", "user.email", "test@example.com")
        _git(self.cfg, "config", "user.name", "test")
        _git(self.cfg, "add", "-A")
        _git(self.cfg, "commit", "-q", "-m", "init")
        os.makedirs(os.path.join(self.home, ".claude"), exist_ok=True)
        os.symlink(os.path.join(self.cfg, "skills"), os.path.join(self.home, ".claude", "skills"))
        os.symlink(os.path.join(self.cfg, "config", "settings.json"),
                    os.path.join(self.home, ".claude", "settings.json"))

    def _fake_run_with_plugins(self, plugin_lines):
        def fake_run(cmd, **kw):
            if cmd[:2] == ["claude", "plugin"]:
                return _FakePluginResult(0, "\n".join(plugin_lines) + "\n")
            return _stub_run(cmd, **kw)
        return fake_run

    def test_reports_git_state(self):
        report = configreport.collect_config_report(home=self.home, run=_stub_run)
        self.assertTrue(report["claude_config"]["head"])
        self.assertEqual(len(report["claude_config"]["short_head"]), 7)
        self.assertFalse(report["claude_config"]["dirty"])
        self.assertIsInstance(report["errors"], list)

    def test_dirty_true_when_non_settings_file_dirty(self):
        with open(os.path.join(self.cfg, "agents", "claude.md"), "a") as f:
            f.write("more\n")
        report = configreport.collect_config_report(home=self.home, run=_stub_run)
        self.assertTrue(report["claude_config"]["dirty"])
        self.assertIn("agents/claude.md", report["claude_config"]["dirty_files"])

    def test_dirty_false_when_only_settings_json_dirty(self):
        with open(os.path.join(self.cfg, "config", "settings.json"), "a") as f:
            f.write("\n")
        report = configreport.collect_config_report(home=self.home, run=_stub_run)
        self.assertFalse(report["claude_config"]["dirty"])
        self.assertIn("config/settings.json", report["claude_config"]["dirty_files"])

    def test_deps_missing_vs_dangling_split(self):
        report = configreport.collect_config_report(home=self.home, run=_stub_run)
        self.assertIn("uninstalled-dep", report["skills"]["deps_missing"])
        self.assertIn("truly-broken", report["skills"]["dangling"])
        self.assertNotIn("uninstalled-dep", report["skills"]["dangling"])
        self.assertNotIn("truly-broken", report["skills"]["deps_missing"])
        self.assertNotIn("watch", report["skills"]["deps_missing"])
        self.assertNotIn("watch", report["skills"]["dangling"])

    def test_device_only_skill_from_gitignore_never_reads_contents(self):
        report = configreport.collect_config_report(home=self.home, run=_stub_run)
        self.assertIn("google-workspace", report["skills"]["device_only"])
        dumped = json.dumps(report)
        self.assertNotIn("super-secret-token-do-not-read", dumped)

    def test_agents_and_rules_empty_local_not_error(self):
        report = configreport.collect_config_report(home=self.home, run=_stub_run)
        self.assertIn("claude", report["agents"])
        self.assertEqual(report["rules"]["shared"], [])
        self.assertEqual(report["rules"]["local"], [])
        self.assertEqual(report["errors"], [])

    def test_plugins_declared_vs_installed_ignores_skills_dir_marketplace(self):
        fake_run = self._fake_run_with_plugins([
            "watch@official  v1.0",
            "extra-plugin@official  v2.0",
            "google-workspace@skills-dir  v0.0",
        ])
        report = configreport.collect_config_report(home=self.home, run=fake_run)
        self.assertIn("watch@official", report["plugins"]["declared"])
        self.assertIn("ghost@official", report["plugins"]["missing"])
        self.assertIn("extra-plugin@official", report["plugins"]["extra"])
        self.assertNotIn("google-workspace@skills-dir", report["plugins"]["installed"])
        self.assertNotIn("google-workspace@skills-dir", report["plugins"]["extra"])

    def test_settings_hooks_symlink_and_sha256(self):
        report = configreport.collect_config_report(home=self.home, run=_stub_run)
        self.assertTrue(report["settings"]["hooks_present"])
        self.assertTrue(report["settings"]["remote_control_at_startup"])
        self.assertTrue(report["settings"]["settings_symlinked"])
        self.assertTrue(report["settings"]["skills_symlinked"])
        expected = hashlib.sha256(
            open(os.path.join(self.cfg, "config", "settings.json"), "rb").read()
        ).hexdigest()
        self.assertEqual(report["settings"]["sha256"], expected)

    def test_effective_model_from_env_overrides_settings(self):
        old = os.environ.get("ANTHROPIC_MODEL")
        os.environ["ANTHROPIC_MODEL"] = "claude-sonnet-env"
        try:
            report = configreport.collect_config_report(home=self.home, run=_stub_run)
            self.assertEqual(report["effective_model"], "claude-sonnet-env")
        finally:
            if old is None:
                os.environ.pop("ANTHROPIC_MODEL", None)
            else:
                os.environ["ANTHROPIC_MODEL"] = old

    def test_effective_model_falls_back_to_settings_json(self):
        os.environ.pop("ANTHROPIC_MODEL", None)
        report = configreport.collect_config_report(home=self.home, run=_stub_run)
        self.assertEqual(report["effective_model"], "claude-opus")

    def test_claude_local_md_detected(self):
        with open(os.path.join(self.home, ".claude", "CLAUDE.local.md"), "w") as f:
            f.write("local notes\n")
        report = configreport.collect_config_report(home=self.home, run=_stub_run)
        self.assertTrue(report["claude_local_md"])

    def test_never_raises_on_missing_repo(self):
        empty_home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty_home, ignore_errors=True)
        report = configreport.collect_config_report(home=empty_home, run=_stub_run)
        self.assertIsNone(report["claude_config"]["head"])
        self.assertEqual(report["skills"]["names"], [])
        self.assertGreater(len(report["errors"]), 0)

    def test_generated_at_is_int_epoch(self):
        report = configreport.collect_config_report(home=self.home, run=_stub_run)
        self.assertIsInstance(report["generated_at"], int)

    def test_self_referential_symlink_in_skill_returns_quickly(self):
        loop_dir = os.path.join(self.cfg, "skills", "loopy")
        os.makedirs(loop_dir)
        os.symlink(loop_dir, os.path.join(loop_dir, "self"))
        start = time.monotonic()
        report = configreport.collect_config_report(home=self.home, run=_stub_run)
        self.assertLess(time.monotonic() - start, 5)
        self.assertIn("loopy", report["skills"]["names"])
