import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import overview

class OverviewTest(unittest.TestCase):
    def test_card_from_parts_online(self):
        card = overview.card_from_parts(
            device={"id": "home", "name": "Home", "base_url": "http://tba-lin.ts.net:8200"},
            sessions=[{"tokens": 1000}, {"tokens": 500}],
            stats={"loadavg": [2.0, 1.0, 1.0], "cores": 4, "os": "Ubuntu 24.04", "token_history": [1, 2, 3]},
        )
        self.assertEqual(card["id"], "home")
        self.assertEqual(card["hostname"], "tba-lin.ts.net")
        self.assertTrue(card["online"])
        self.assertEqual(card["sessions"], 2)
        self.assertEqual(card["tokens"], 1500)
        self.assertEqual(card["loadPct"], 50)
        self.assertEqual(card["os"], "Ubuntu 24.04")
        self.assertEqual(card["spark"], [1, 2, 3])

    def test_card_offline_when_no_stats(self):
        card = overview.card_from_parts(
            device={"id": "x", "name": "X", "base_url": "http://x:8200"},
            sessions=None, stats=None,
        )
        self.assertFalse(card["online"])
        self.assertEqual(card["sessions"], 0)
        self.assertEqual(card["tokens"], 0)
        self.assertEqual(card["spark"], [])

    def test_online_with_sessions_but_no_stats(self):
        card = overview.card_from_parts(
            device={"id": "old", "name": "Old", "base_url": "http://old:8200"},
            sessions=[{"tokens": 100}], stats=None, online=True,
        )
        self.assertTrue(card["online"])
        self.assertEqual(card["sessions"], 1)
        self.assertEqual(card["tokens"], 100)
        self.assertEqual(card["loadPct"], 0)
        self.assertEqual(card["os"], "")
        self.assertEqual(card["spark"], [])

    def test_loadpct_caps_at_100(self):
        card = overview.card_from_parts(
            device={"id": "x", "name": "X", "base_url": "http://x:8200"},
            sessions=[], stats={"loadavg": [9.0], "cores": 2, "os": "o", "token_history": []},
        )
        self.assertEqual(card["loadPct"], 100)

if __name__ == "__main__":
    unittest.main()
