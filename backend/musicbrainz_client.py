"""MusicBrainz API client for recording search fallback."""

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional

import httpx


class MusicBrainzClient:
    """Search recordings to refine artist/album metadata for Lidarr lookup."""

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(timeout=20.0)
        self.user_agent = os.getenv(
            "MUSICBRAINZ_USER_AGENT",
            "MagicLists/1.0 (https://github.com/martinsah/magic-lists-for-navidrome)",
        )
        self._last_request_at = 0.0

    async def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_at
        if elapsed < 1.0:
            await asyncio.sleep(1.0 - elapsed)
        self._last_request_at = time.time()

    async def search_recording(self, title: str, artist: str) -> List[Dict[str, Any]]:
        """Return normalized hits: artist, album, artist_mbid, release_group_mbid."""
        if not title.strip() or not artist.strip():
            return []

        await self._rate_limit()
        query = f'recording:"{title}" AND artist:"{artist}"'
        try:
            response = await self.client.get(
                "https://musicbrainz.org/ws/2/recording",
                params={"query": query, "fmt": "json", "limit": 5},
                headers={"User-Agent": self.user_agent, "Accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            print(f"MusicBrainz search failed: {exc}")
            return []

        hits: List[Dict[str, Any]] = []
        for recording in data.get("recordings", []):
            parsed = self._parse_recording(recording)
            if parsed:
                hits.append(parsed)
        return hits

    def _parse_recording(self, recording: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        title = recording.get("title", "").strip()
        if not title:
            return None

        artist_name = ""
        artist_mbid = ""
        for credit in recording.get("artist-credit", []):
            artist_obj = credit.get("artist") or {}
            artist_name = artist_obj.get("name", "").strip()
            artist_mbid = artist_obj.get("id", "")
            if artist_name:
                break

        album_title = ""
        release_group_mbid = ""
        for release in recording.get("releases", []):
            album_title = release.get("title", "").strip()
            release_group = release.get("release-group") or {}
            release_group_mbid = release_group.get("id", "")
            if album_title:
                break

        if not artist_name:
            return None

        return {
            "title": title,
            "artist": artist_name,
            "album": album_title,
            "artist_mbid": artist_mbid,
            "release_group_mbid": release_group_mbid,
        }

    async def close(self) -> None:
        await self.client.aclose()
