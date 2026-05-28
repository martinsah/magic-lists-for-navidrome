"""Prompt/granularity helpers for meta-genre distillation (no heavy deps)."""
import json
from typing import Any, Dict, List, Optional, Tuple

VALID_META_GENRE_GRANULARITIES = frozenset({"coarse", "balanced", "fine"})


def normalize_meta_genre_granularity(value: Optional[str]) -> str:
    normalized = (value or "balanced").strip().lower()
    if normalized not in VALID_META_GENRE_GRANULARITIES:
        return "balanced"
    return normalized


def target_meta_group_range(raw_count: int, granularity: str) -> Tuple[int, int]:
    """Derive an LLM target group count band from library size and granularity."""
    raw_count = max(0, int(raw_count))
    granularity = normalize_meta_genre_granularity(granularity)
    if raw_count == 0:
        return 0, 0
    if granularity == "fine":
        low = max(40, int(raw_count * 0.12))
        high = max(low + 10, int(raw_count * 0.28))
    elif granularity == "coarse":
        low = max(12, int(raw_count * 0.03))
        high = max(low + 5, int(raw_count * 0.10))
    else:
        low = max(25, int(raw_count * 0.06))
        high = max(low + 8, int(raw_count * 0.15))
    high = min(high, raw_count)
    low = min(low, high)
    return low, high


def build_meta_distillation_prompts(
    canonical: List[Dict[str, Any]],
    granularity: str,
) -> Tuple[str, str, Dict[str, Any]]:
    granularity = normalize_meta_genre_granularity(granularity)
    raw_count = len(canonical)
    target_low, target_high = target_meta_group_range(raw_count, granularity)

    if granularity == "fine":
        grouping_guidance = (
            "Prefer finer-grained meta-genres. Combine only tags that clearly describe the same "
            "musical tradition or scene. Keep distinct umbrella labels separate (for example "
            "Electronic vs Hip-Hop vs Jazz). Avoid catch-all buckets like Miscellaneous."
        )
    elif granularity == "coarse":
        grouping_guidance = (
            "Prefer fewer, broader meta-genres. Merge related sub-styles into clear umbrella labels "
            "when they serve the same browsing purpose."
        )
    else:
        grouping_guidance = (
            "Balance breadth and detail: merge obvious duplicates and near-synonyms, but keep "
            "clearly different traditions in separate meta-genres."
        )

    meta = {
        "granularity": granularity,
        "target_group_low": target_low,
        "target_group_high": target_high,
    }

    system_prompt = (
        "You are a music taxonomy assistant. Group raw Navidrome genre tags into meta-genres "
        "for playlist browsing. Return JSON only. Each raw genre must appear exactly once across "
        "all groups. Meta-genre names should be concise (typically 2-5 words)."
    )
    user_prompt = (
        f"Granularity: {granularity}. Aim for roughly {target_low}-{target_high} meta-genres "
        f"from {raw_count} raw tags ({grouping_guidance})\n\n"
        "Return JSON with shape "
        '{"groups":[{"meta_genre":"string","genres":["raw1","raw2"],"total_song_count":123}]}. '
        "Set total_song_count to the sum of member song counts.\n\n"
        f"raw_genres={json.dumps(canonical, ensure_ascii=False)}"
    )
    return system_prompt, user_prompt, meta
