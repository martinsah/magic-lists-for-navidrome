"""Unit tests for deterministic curation helpers."""

import unittest

from backend.curation_strategy import build_genre_mix_llm_pool, genre_mix_llm_pool_cap


class GenreMixLlmPoolTests(unittest.TestCase):
    def test_pool_cap_scales_with_target_size(self):
        self.assertEqual(genre_mix_llm_pool_cap(25), 35)
        self.assertEqual(genre_mix_llm_pool_cap(50), 66)
        self.assertEqual(genre_mix_llm_pool_cap(100), 120)

    def test_build_pool_marks_seeds_and_caps_reserves(self):
        assembly = {
            "selected_tracks": [{"id": f"s{i}", "title": f"S{i}"} for i in range(50)],
            "reserve_tracks": [{"id": f"r{i}", "title": f"R{i}"} for i in range(100)],
        }
        pool, meta = build_genre_mix_llm_pool(assembly, target_size=50)
        self.assertEqual(len(pool), 66)
        self.assertEqual(meta["llm_seed_count"], 50)
        self.assertEqual(meta["llm_reserve_count"], 16)
        self.assertTrue(all(t.get("_heuristic_seed") for t in pool[:50]))
        self.assertFalse(any(t.get("_heuristic_seed") for t in pool[50:]))


if __name__ == "__main__":
    unittest.main()
