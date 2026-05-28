"""Unit tests for LLM logging helpers."""

import json
import unittest

from backend.services.llm_logging import (
    infer_call_label,
    summarize_parsed_response,
    _truncate_user_prompt_for_log,
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

    def test_truncate_large_candidate_blob(self):
        huge = (
            "genre_mix: Select 50 tracks. Candidates (i=index, t=title): "
            + json.dumps([{"i": n, "t": f"Track {n}"} for n in range(500)])
            + ". Keep reasoning short."
        )
        condensed = _truncate_user_prompt_for_log(huge, max_chars=2000)
        self.assertIn("500 candidate objects", condensed)
        self.assertLess(len(condensed), len(huge))


if __name__ == "__main__":
    unittest.main()
