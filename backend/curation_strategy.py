"""Deterministic playlist assembly and scoring helpers.

Navidrome remains the source of truth for library contents. This module only
derives transient ranking/selection data from the current Navidrome payload.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


SCORING_VERSION = "engagement-diversity-v1"


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def stable_track_key(track: Dict[str, Any]) -> str:
    """Build a best-effort stable key for remapping across Navidrome ID churn."""
    parts = [
        normalize_text(track.get("artist")),
        normalize_text(track.get("album")),
        normalize_text(track.get("title")),
        str(track.get("duration") or ""),
        str(track.get("track_number") or track.get("track") or ""),
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def score_track_components(track: Dict[str, Any], library_stats: Dict[str, Any]) -> Tuple[float, Dict[str, float]]:
    """Return total score and explainable score components for one track."""
    max_play_count = max(float(library_stats.get("max_play_count") or 0), 0.0)
    play_count = float(track.get("play_count") or 0)
    play_score = (play_count / max_play_count) * 100 if max_play_count else 0.0

    liked_score = 50.0 if track.get("local_library_likes") or track.get("loved") or track.get("favorited") else 0.0
    rating_score = float(track.get("rating") or 0) * 10
    playlist_score = min(float(track.get("playlist_appearances") or 0) * 5, 50)

    recency_score = 0.0
    last_played = track.get("last_played") or track.get("played")
    if last_played:
        try:
            last_played_date = datetime.fromisoformat(str(last_played).replace("Z", "+00:00"))
            days_since = (datetime.now() - last_played_date.replace(tzinfo=None)).days
            if days_since <= 30:
                recency_score = float(max(0, 30 - days_since))
        except (TypeError, ValueError):
            recency_score = 0.0

    components = {
        "play_score": play_score,
        "liked_score": liked_score,
        "rating_score": rating_score,
        "playlist_score": playlist_score,
        "recency_score": recency_score,
    }
    return sum(components.values()), components


def artist_cap_for_playlist(playlist_size: int, artist_concentration: float) -> int:
    """Translate a 0..1 concentration knob into a max tracks-per-artist cap."""
    concentration = min(max(artist_concentration, 0.0), 1.0)
    if playlist_size <= 1:
        return 1
    min_cap = 2
    max_cap = max(min_cap, math.ceil(playlist_size * 0.55))
    return max(min_cap, round(min_cap + (max_cap - min_cap) * concentration))


def album_cap_for_playlist(playlist_size: int, album_concentration: float = 0.25) -> int:
    concentration = min(max(album_concentration, 0.0), 1.0)
    min_cap = 1 if playlist_size <= 20 else 2
    max_cap = max(min_cap, math.ceil(playlist_size * 0.3))
    return max(min_cap, round(min_cap + (max_cap - min_cap) * concentration))


def assemble_playlist_candidates(
    tracks: List[Dict[str, Any]],
    target_size: int,
    library_stats: Dict[str, Any],
    artist_concentration: float = 0.35,
    album_concentration: float = 0.25,
    seed_artist: Optional[str] = None,
    seed_artist_dominance: Optional[float] = None,
    reserve_multiplier: int = 2,
) -> Dict[str, Any]:
    """Select a deterministic playlist plus reserve candidates using diversity constraints."""
    scored: List[Dict[str, Any]] = []
    for track in tracks:
        score, components = score_track_components(track, library_stats)
        item = dict(track)
        item["_score"] = score
        item["_score_components"] = components
        item["_stable_key"] = stable_track_key(track)
        scored.append(item)

    scored.sort(key=lambda item: item.get("_score", 0), reverse=True)

    artist_cap = artist_cap_for_playlist(target_size, artist_concentration)
    album_cap = album_cap_for_playlist(target_size, album_concentration)
    seed_artist_norm = normalize_text(seed_artist)
    seed_target = None
    if seed_artist_norm and seed_artist_dominance is not None:
        seed_target = round(target_size * min(max(seed_artist_dominance, 0.0), 1.0))

    selected: List[Dict[str, Any]] = []
    selected_ids = set()
    artist_counts: Counter[str] = Counter()
    album_counts: Counter[str] = Counter()

    def can_select(track: Dict[str, Any], relaxed: bool = False) -> bool:
        if track.get("id") in selected_ids:
            return False
        artist = normalize_text(track.get("artist"))
        album = normalize_text(track.get("album"))
        if not relaxed:
            if artist_counts[artist] >= artist_cap:
                return False
            if album and album_counts[album] >= album_cap:
                return False
            if selected and normalize_text(selected[-1].get("artist")) == artist:
                return False
        return True

    # Optional seed dominance: reserve some slots for the seed artist before filling broadly.
    if seed_target:
        for track in scored:
            if len([t for t in selected if normalize_text(t.get("artist")) == seed_artist_norm]) >= seed_target:
                break
            if normalize_text(track.get("artist")) != seed_artist_norm:
                continue
            if can_select(track, relaxed=True):
                selected.append(track)
                selected_ids.add(track.get("id"))
                artist_counts[normalize_text(track.get("artist"))] += 1
                album_counts[normalize_text(track.get("album"))] += 1

    for track in scored:
        if len(selected) >= target_size:
            break
        if can_select(track):
            selected.append(track)
            selected_ids.add(track.get("id"))
            artist_counts[normalize_text(track.get("artist"))] += 1
            album_counts[normalize_text(track.get("album"))] += 1

    # Relax constraints to guarantee enough tracks when the library slice is small.
    for track in scored:
        if len(selected) >= target_size:
            break
        if can_select(track, relaxed=True):
            selected.append(track)
            selected_ids.add(track.get("id"))

    reserve_limit = max(target_size, target_size * reserve_multiplier)
    reserve = [track for track in scored if track.get("id") not in selected_ids][:reserve_limit]

    return {
        "selected_tracks": selected[:target_size],
        "reserve_tracks": reserve,
        "scored_tracks": scored,
        "metadata": {
            "scoring_version": SCORING_VERSION,
            "source_count": len(tracks),
            "target_size": target_size,
            "artist_concentration": artist_concentration,
            "album_concentration": album_concentration,
            "artist_cap": artist_cap,
            "album_cap": album_cap,
            "selected_count": len(selected[:target_size]),
        },
    }


def genre_mix_llm_pool_cap(target_size: int) -> int:
    """Max tracks sent to the LLM: draft plus a small reserve swap pool."""
    reserve_slots = min(20, max(10, target_size // 3))
    return target_size + reserve_slots


def build_genre_mix_llm_pool(
    assembly: Dict[str, Any],
    target_size: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    Build the capped candidate list for Genre Mix LLM polish.

    Returns annotated tracks (heuristic seeds marked) and counts for logging.
    """
    cap = genre_mix_llm_pool_cap(target_size)
    selected = assembly["selected_tracks"][:target_size]
    reserves = assembly.get("reserve_tracks") or []

    pool: List[Dict[str, Any]] = []
    for track in selected:
        annotated = dict(track)
        annotated["_heuristic_seed"] = True
        pool.append(annotated)

    reserve_slots = max(0, cap - len(pool))
    if reserve_slots > 0:
        pool.extend(reserves[:reserve_slots])

    metadata = {
        "llm_pool_cap": cap,
        "llm_pool_count": len(pool),
        "llm_seed_count": len(selected),
        "llm_reserve_count": min(reserve_slots, len(reserves)),
    }
    return pool, metadata


def serialize_score_params(params: Dict[str, Any]) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"))
