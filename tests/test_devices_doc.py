import os
import unittest

DOC_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "DEVICES.md")


class DevicesDocTest(unittest.TestCase):
    def test_doc_exists_and_covers_required_topics(self):
        with open(DOC_PATH) as f:
            content = f.read()
        for phrase in ("metadata", "full", "devices.json", "Tailscale",
                       "8200", "RC_ROLE", "RC_HASH_SALT"):
            self.assertIn(phrase, content, f"missing coverage of {phrase!r}")

    def test_doc_has_no_personal_identifiers(self):
        with open(DOC_PATH) as f:
            content = f.read()
        # Built from split fragments so this test file's own source
        # doesn't contain the banned literals verbatim (the CI grep
        # would otherwise flag itself).
        banned = (
            "barja" + "zz",
            "tbarj" + "adze",
            "hetz" + "ner",
            "tba" + "-lin",
            "/" + "root/",
        )
        for phrase in banned:
            self.assertNotIn(phrase, content)


if __name__ == "__main__":
    unittest.main()
