import hashlib
import unittest

from cc_macos.translation_cache import TranslationMemoryCache


def key(text):
    return hashlib.sha256(text.encode("utf-8")).digest()


class TranslationMemoryCacheTests(unittest.TestCase):
    def setUp(self):
        self.now = 100

    def cache(self, **options):
        return TranslationMemoryCache(clock=lambda: self.now, **options)

    def test_exact_keys_preserve_whitespace_numbers_and_negation(self):
        cache = self.cache()
        original = "Do not retry 10 times.\n"
        result = "Synthetic result.\n"
        self.assertTrue(cache.put(key(original), result))
        self.assertEqual(cache.get(key(original)), result)
        for other in (original.strip(), original.replace("not ", ""),
                      original.replace("10", "100"), original + " "):
            self.assertIsNone(cache.get(key(other)))

    def test_hits_change_lru_order_but_do_not_extend_ttl(self):
        cache = self.cache(max_entries=2, ttl_seconds=10)
        cache.put(key("one"), "one")
        self.now += 1
        cache.put(key("two"), "two")
        self.assertEqual(cache.get(key("one")), "one")
        cache.put(key("three"), "three")
        self.assertIsNone(cache.get(key("two")))
        self.now = 110
        self.assertIsNone(cache.get(key("one")))
        self.assertEqual(cache.get(key("three")), "three")
        self.assertEqual(cache.count, 1)

    def test_byte_budget_counts_utf8_and_keys_and_replacement(self):
        cache = self.cache(max_bytes=72)
        cache.put(key("one"), "\u4e2d")
        cache.put(key("two"), "plain")
        self.assertEqual(cache.payload_bytes, 72)
        cache.put(key("two"), "\u4e2d\u4e2d")
        self.assertIsNone(cache.get(key("one")))
        self.assertEqual(cache.payload_bytes, 38)
        self.assertFalse(cache.put(key("two"), "x" * 41))
        self.assertEqual(cache.count, 0)
        self.assertEqual(cache.payload_bytes, 0)

    def test_clear_discard_and_sessions_never_share_entries(self):
        cache = self.cache()
        other = self.cache()
        cache.put(key("one"), "private synthetic output")
        self.assertIsNone(other.get(key("one")))
        self.assertNotIn("private", repr(cache._entries))
        cache.discard(key("missing"))
        cache.discard(key("one"))
        self.assertEqual(cache.payload_bytes, 0)
        cache.put(key("one"), "one")
        cache.clear()
        self.assertEqual(cache.count, 0)
        self.assertEqual(cache.payload_bytes, 0)

    def test_clock_rollback_discards_results_and_expiration_is_inclusive(self):
        cache = self.cache(ttl_seconds=5)
        cache.put(key("one"), "one")
        self.now = 99
        self.assertIsNone(cache.get(key("one")))
        cache.put(key("one"), "one")
        self.now = 104
        self.assertEqual(cache.count, 0)

    def test_invalid_limits_keys_output_and_clock_fail_explicitly(self):
        for options in ({"max_entries": 0}, {"max_entries": True}, {"max_bytes": 0},
                        {"ttl_seconds": float("nan")}, {"ttl_seconds": 0}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.cache(**options)
        cache = self.cache()
        for invalid in (b"", "x" * 32, bytearray(32)):
            with self.assertRaises(ValueError):
                cache.get(invalid)
        for invalid in ("", "  ", None, 1):
            with self.assertRaises(ValueError):
                cache.put(key("one"), invalid)
        self.now = float("inf")
        with self.assertRaisesRegex(ValueError, "invalid_translation_cache_clock"):
            cache.get(key("one"))


if __name__ == "__main__":
    unittest.main()
