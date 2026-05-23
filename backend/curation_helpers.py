"""Shared helpers for AI curation return values."""

from typing import Any, Dict, List, Tuple, Union

from .suggestion_service import extract_suggested_tracks, missing_recommendations_enabled


def finish_curation(
    track_ids: List[str],
    reasoning: str,
    response_data: Dict[str, Any],
    include_reasoning: bool,
) -> Union[List[str], Tuple[List[str], str], Tuple[List[str], str, List[Dict[str, Any]]]]:
    suggested = extract_suggested_tracks(response_data)
    if include_reasoning and missing_recommendations_enabled():
        return track_ids, reasoning, suggested
    if include_reasoning:
        return track_ids, reasoning
    return track_ids
