import json
import os
import tempfile
import unittest

from cc_dictionary_cache import DictionaryAiCache, DictionaryAiCacheError


class TestDictionaryAiCache(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = os.path.join(self.temp_dir.name, "cache.json")

    def test_missing_cache_and_round_trip(self):
        cache = DictionaryAiCache(self.path)
        self.assertIsNone(cache.get("run", "sig-a"))
        cache.put("run", "sig-a", "extra usage")
        self.assertEqual(cache.get("run", "sig-a"), "extra usage")
        self.assertIsNone(cache.get("run", "sig-b"))

    def test_replacement_and_limit(self):
        cache = DictionaryAiCache(self.path, limit=2)
        cache.put("one", "sig", "first")
        cache.put("two", "sig", "second")
        cache.put("one", "sig", "updated")
        cache.put("three", "sig", "third")
        self.assertEqual(cache.get("one", "sig"), "updated")
        self.assertEqual(cache.get("three", "sig"), "third")
        self.assertIsNone(cache.get("two", "sig"))
        with open(self.path, "r", encoding="utf-8") as stream:
            self.assertEqual(len(json.load(stream)), 2)

    def test_corrupt_cache_is_explicit(self):
        with open(self.path, "w", encoding="utf-8") as stream:
            stream.write("{broken")
        with self.assertRaises(DictionaryAiCacheError):
            DictionaryAiCache(self.path).get("run", "sig")


if __name__ == "__main__":
    unittest.main()
