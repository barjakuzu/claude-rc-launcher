import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stats

class TokenHistoryTest(unittest.TestCase):
    def setUp(self):
        stats._HISTORY.clear()

    def test_sample_appends_and_caps_at_12(self):
        for i in range(15):
            stats.sample_tokens(lambda: i * 1000)
        hist = stats.token_history()
        self.assertEqual(len(hist), 12)
        self.assertEqual(hist[-1], 14000)
        self.assertEqual(hist[0], 3000)

    def test_history_is_a_copy(self):
        stats.sample_tokens(lambda: 5)
        stats.token_history().append(999)
        self.assertEqual(stats.token_history(), [5])

class SystemStatsTest(unittest.TestCase):
    def test_system_stats_shape(self):
        s = stats.system_stats()
        self.assertIn("loadavg", s); self.assertEqual(len(s["loadavg"]), 3)
        self.assertIsInstance(s["cores"], int)
        self.assertTrue(s["cores"] >= 1)
        self.assertIsInstance(s["os"], str)
        self.assertTrue(len(s["os"]) > 0)

if __name__ == "__main__":
    unittest.main()
