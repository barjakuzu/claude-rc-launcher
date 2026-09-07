import json
import os
import unittest

SNIPPET_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "hooks", "settings.snippet.json")

_EVENTS = ["SessionStart", "UserPromptSubmit", "Stop", "StopFailure",
           "Notification", "SubagentStop", "PreCompact", "SessionEnd"]


class HooksSnippetTest(unittest.TestCase):
    def test_snippet_is_valid_json_with_hooks_key(self):
        with open(SNIPPET_PATH) as f:
            data = json.load(f)
        self.assertIn("hooks", data)

    def test_every_expected_event_present_and_guarded(self):
        with open(SNIPPET_PATH) as f:
            data = json.load(f)
        hooks = data["hooks"]
        for event in _EVENTS:
            self.assertIn(event, hooks, f"missing {event}")
            entries = hooks[event]
            self.assertTrue(entries, f"{event} has no entries")
            for entry in entries:
                for h in entry["hooks"]:
                    self.assertEqual(h["type"], "command")
                    self.assertIn('[ -x "$HOME/.claude-rc/bin/rc-hook" ]', h["command"])
                    self.assertIn('|| true', h["command"])


if __name__ == "__main__":
    unittest.main()
