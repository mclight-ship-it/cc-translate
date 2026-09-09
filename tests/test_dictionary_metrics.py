import unittest

from cc_dictionary_metrics import DictionaryMetrics


class TestDictionaryMetrics(unittest.TestCase):
    def test_snapshot_contains_only_aggregate_routing_data(self):
        metrics = DictionaryMetrics(latency_limit=3)
        metrics.record("hit", 1.0)
        metrics.record("miss", 4.0)
        metrics.record("weak", 2.0)
        metrics.record("error", 3.0)
        metrics.record("disabled")
        snapshot = metrics.snapshot()

        self.assertEqual(snapshot["attempts"], 4)
        self.assertEqual(snapshot["hit_rate"], 25.0)
        self.assertEqual(snapshot["p50_ms"], 3.0)
        self.assertEqual(snapshot["p95_ms"], 3.0)
        self.assertEqual(snapshot["retained_latencies"], 3)
        self.assertEqual(snapshot["outcomes"]["disabled"], 1)
        self.assertNotIn("query", snapshot)

    def test_empty_snapshot_has_no_fake_latency_or_rate(self):
        snapshot = DictionaryMetrics().snapshot()
        self.assertEqual(snapshot["attempts"], 0)
        self.assertIsNone(snapshot["hit_rate"])
        self.assertIsNone(snapshot["p50_ms"])
        self.assertIsNone(snapshot["p95_ms"])

    def test_invalid_outcome_is_rejected(self):
        with self.assertRaises(ValueError):
            DictionaryMetrics().record("unknown", 1)


if __name__ == "__main__":
    unittest.main()
