import unittest

from backend.services.meta_genre_granularity import (
    build_meta_distillation_prompts,
    normalize_meta_genre_granularity,
    target_meta_group_range,
)


class MetaGenreGranularityTests(unittest.TestCase):
    def test_normalize_invalid_defaults_to_balanced(self):
        self.assertEqual(normalize_meta_genre_granularity("weird"), "balanced")
        self.assertEqual(normalize_meta_genre_granularity(None), "balanced")

    def test_target_ranges_scale_with_library_size(self):
        coarse_low, coarse_high = target_meta_group_range(443, "coarse")
        fine_low, fine_high = target_meta_group_range(443, "fine")
        self.assertLess(coarse_high, fine_low)
        self.assertGreaterEqual(fine_low, 40)

    def test_prompt_includes_granularity_and_target_band(self):
        canonical = [{"name": "Techno", "songCount": 10}, {"name": "House", "songCount": 8}]
        _system, user, meta = build_meta_distillation_prompts(canonical, "fine")
        self.assertEqual(meta["granularity"], "fine")
        self.assertIn("Granularity: fine", user)
        self.assertIn(str(meta["target_group_low"]), user)
        self.assertIn(str(meta["target_group_high"]), user)


if __name__ == "__main__":
    unittest.main()
