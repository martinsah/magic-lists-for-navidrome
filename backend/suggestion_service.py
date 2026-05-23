"""Post-curation processing for AI-suggested tracks not in the candidate pool."""

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple


def _decode_json_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return value.replace('\\"', '"').strip()

from .navidrome_client import NavidromeClient
from .database import DatabaseManager


def missing_recommendations_enabled() -> bool:
    return os.getenv("ENABLE_MISSING_RECOMMENDATIONS", "").lower() in ("1", "true", "yes")


def append_library_matches_enabled() -> bool:
    return os.getenv("APPEND_LIBRARY_MATCHES", "true").lower() not in ("0", "false", "no")


def max_suggested_missing() -> int:
    try:
        return max(1, min(25, int(os.getenv("MAX_SUGGESTED_MISSING", "10"))))
    except ValueError:
        return 10


def suggestion_prompt_suffix() -> str:
    if not missing_recommendations_enabled():
        return ""
    n = max_suggested_missing()
    return (
        f" Also suggest up to {n} additional tracks NOT in the candidate list that would "
        f"strengthen this playlist. Return them in suggested_tracks as objects with "
        f"title and artist only (omit album and note to save space). Only suggest tracks "
        f"not already represented in the candidate list."
    )


def extract_suggested_tracks_from_partial_text(content: str) -> List[Dict[str, Any]]:
    """Recover complete suggestion objects from truncated JSON responses."""
    if '"suggested_tracks"' not in content:
        return []

    section = content[content.find('"suggested_tracks"'):]
    suggestions: List[Dict[str, Any]] = []
    pattern = re.compile(
        r'\{\s*"title"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"artist"\s*:\s*"((?:[^"\\]|\\.)*)"',
        re.DOTALL,
    )

    for match in pattern.finditer(section):
        title = _decode_json_string(match.group(1))
        artist = _decode_json_string(match.group(2))
        if title.strip() and artist.strip():
            suggestions.append({"title": title.strip(), "artist": artist.strip()})
        if len(suggestions) >= max_suggested_missing():
            break

    return suggestions


def extract_suggested_tracks(response_data: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not missing_recommendations_enabled() or not isinstance(response_data, dict):
        return []

    raw = response_data.get("suggested_tracks") or []
    if not isinstance(raw, list):
        return []

    suggestions: List[Dict[str, Any]] = []
    for item in raw[: max_suggested_missing()]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        artist = str(item.get("artist", "")).strip()
        if not title or not artist:
            continue
        entry: Dict[str, Any] = {"title": title, "artist": artist}
        album = str(item.get("album", "")).strip()
        note = str(item.get("note", "")).strip()
        if album:
            entry["album"] = album
        if note:
            entry["note"] = note
        suggestions.append(entry)
    return suggestions


def unpack_curation_result(curation_result) -> Tuple[List[str], str, List[Dict[str, Any]]]:
    if isinstance(curation_result, tuple):
        if len(curation_result) >= 3:
            return curation_result[0], curation_result[1] or "", curation_result[2] or []
        if len(curation_result) == 2:
            return curation_result[0], curation_result[1] or "", []
        if len(curation_result) == 1:
            return curation_result[0], "", []
    return curation_result or [], "", []


async def process_playlist_suggestions(
    nav_client: NavidromeClient,
    db: DatabaseManager,
    playlist_db_id: int,
    navidrome_playlist_id: str,
    suggested_tracks: List[Dict[str, Any]],
    existing_track_ids: List[str],
    track_id_to_title: Dict[str, str],
    library_ids: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], int, List[str]]:
    """Resolve suggestions: append library matches, persist missing list."""
    if not missing_recommendations_enabled() or not suggested_tracks:
        return [], 0, [track_id_to_title.get(tid, "Unknown") for tid in existing_track_ids]

    existing_ids = set(existing_track_ids)
    missing: List[Dict[str, Any]] = []
    added_ids: List[str] = []
    song_titles = [track_id_to_title.get(tid, "Unknown") for tid in existing_track_ids]

    for suggestion in suggested_tracks:
        match = await nav_client.find_song_by_artist_title(
            suggestion["artist"],
            suggestion["title"],
            album=suggestion.get("album"),
            library_ids=library_ids,
        )
        if match and match["id"] not in existing_ids:
            if append_library_matches_enabled():
                added_ids.append(match["id"])
                existing_ids.add(match["id"])
                song_titles.append(match.get("title", suggestion["title"]))
            continue
        if not match:
            missing.append(suggestion)

    added_count = 0
    if added_ids and append_library_matches_enabled():
        await nav_client.append_tracks_to_playlist(navidrome_playlist_id, added_ids)
        added_count = len(added_ids)
        await db.update_playlist_songs(playlist_db_id, song_titles)

    await db.update_playlist_suggestions(playlist_db_id, missing, added_count)
    return missing, added_count, song_titles
