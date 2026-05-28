"""Unit tests for LLM logging helpers."""

import json
import unittest

from backend.services.llm_logging import (
    humanize_user_prompt_for_log,
    infer_call_label,
    summarize_parsed_response,
)


class LlmLoggingTests(unittest.TestCase):
    def test_infer_call_label_genre_mix(self):
        label = infer_call_label("Genre mix curator", "genre_mix: Select exactly 25 tracks")
        self.assertEqual(label, "genre_mix")

    def test_infer_call_label_meta_distill(self):
        label = infer_call_label("taxonomy", 'raw_genres=[{"name":"Rock","songCount":1}]')
        self.assertEqual(label, "meta_genre_distillation")

    def test_summarize_genre_mix_response(self):
        data = {
            "track_ids": list(range(30)),
            "reasoning": "A flowing electronic mix.",
            "suggested_tracks": [
                {"title": "Windowlicker", "artist": "Aphex Twin", "album": "Single"},
            ],
        }
        lines = summarize_parsed_response(data)
        joined = "\n".join(lines)
        self.assertIn("30", joined)
        self.assertIn("Aphex Twin", joined)

    def test_humanize_genre_mix_candidates(self):
        candidates = [
            {"i": 0, "t": "Palace of OKV in Reverse", "a": "Kurt Vile", "p": 3, "l": False, "h": True},
            {"i": 1, "t": "Night Destroyer", "a": "Red Fang", "p": 2, "l": True, "h": True},
        ]
        prompt = (
            "genre_mix: Select exactly 50 tracks for 'Rock'. "
            "Candidates (i=index, t=title, a=artist, p=play_count, l=liked, h=heuristic_seed): "
            + json.dumps(candidates)
            + ". Keep reasoning short."
        )
        readable = humanize_user_prompt_for_log(prompt)
        self.assertIn("Kurt Vile - Palace of OKV in Reverse", readable)
        self.assertIn("Red Fang - Night Destroyer", readable)
        self.assertIn("heuristic seed", readable)
        self.assertNotIn('{"i":0', readable)

    def test_humanize_raw_genres(self):
        genres = [{"name": "Rock", "songCount": 100}, {"name": "Punk", "songCount": 50}]
        prompt = 'Group genres.\n\nraw_genres=' + json.dumps(genres)
        readable = humanize_user_prompt_for_log(prompt)
        self.assertIn("Rock (100 tracks)", readable)
        self.assertIn("Punk (50 tracks)", readable)


if __name__ == "__main__":
    unittest.main()
