"""Business logic for adding missing recommendations to Lidarr."""

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .database import DatabaseManager
from .lidarr_client import LidarrClient
from .musicbrainz_client import MusicBrainzClient


def lidarr_integration_enabled() -> bool:
    return os.getenv("ENABLE_LIDARR_INTEGRATION", "").lower() in ("1", "true", "yes")


def lidarr_configured() -> bool:
    return bool(os.getenv("LIDARR_URL", "").strip() and os.getenv("LIDARR_API_KEY", "").strip())


def lidarr_search_on_add() -> bool:
    return os.getenv("LIDARR_SEARCH_ON_ADD", "true").lower() not in ("0", "false", "no")


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").lower().strip())


def _score_artist_match(candidate: Dict[str, Any], query: str) -> int:
    name = candidate.get("artistName") or candidate.get("artist", {}).get("artistName") or ""
    norm_name = _normalize(name)
    norm_query = _normalize(query)
    if not norm_name or not norm_query:
        return 0
    if norm_name == norm_query:
        return 100
    if norm_query in norm_name or norm_name in norm_query:
        return 60
    return 10


def _score_album_match(candidate: Dict[str, Any], album: str, artist: str) -> int:
    title = candidate.get("title") or ""
    norm_title = _normalize(title)
    norm_album = _normalize(album)
    score = 0
    if norm_title == norm_album:
        score += 80
    elif norm_album in norm_title or norm_title in norm_album:
        score += 50

    artist_name = (candidate.get("artist") or {}).get("artistName") or ""
    score += _score_artist_match({"artistName": artist_name}, artist) // 2
    return score


def _rank_candidates(
    candidates: List[Dict[str, Any]],
    query: str,
    scorer,
) -> List[Dict[str, Any]]:
    scored = []
    for candidate in candidates:
        score = scorer(candidate)
        if score > 0:
            enriched = dict(candidate)
            enriched["_match_score"] = score
            scored.append(enriched)
    scored.sort(key=lambda item: item.get("_match_score", 0), reverse=True)
    return scored


def _is_ambiguous(ranked: List[Dict[str, Any]]) -> bool:
    if len(ranked) < 2:
        return False
    top = ranked[0].get("_match_score", 0)
    second = ranked[1].get("_match_score", 0)
    return second >= top - 5 and second >= 40


def _candidate_summary_artist(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "foreign_artist_id": item.get("foreignArtistId"),
        "artist_name": item.get("artistName"),
        "disambiguation": item.get("disambiguation", ""),
        "match_score": item.get("_match_score", 0),
    }


def _candidate_summary_album(item: Dict[str, Any]) -> Dict[str, Any]:
    artist = item.get("artist") or {}
    return {
        "foreign_album_id": item.get("foreignAlbumId"),
        "foreign_artist_id": artist.get("foreignArtistId"),
        "album_title": item.get("title"),
        "artist_name": artist.get("artistName"),
        "disambiguation": item.get("disambiguation", ""),
        "match_score": item.get("_match_score", 0),
    }


class LidarrService:
    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        self.db = db
        self._client: Optional[LidarrClient] = None
        self._mb_client: Optional[MusicBrainzClient] = None
        self._existing_artist_ids: Optional[set] = None

    def _get_client(self) -> LidarrClient:
        if self._client is None:
            self._client = LidarrClient()
        return self._client

    def _get_mb_client(self) -> MusicBrainzClient:
        if self._mb_client is None:
            self._mb_client = MusicBrainzClient()
        return self._mb_client

    async def get_status(self) -> Dict[str, Any]:
        if not lidarr_integration_enabled():
            return {"enabled": False, "configured": False, "reachable": False}
        if not lidarr_configured():
            return {
                "enabled": True,
                "configured": False,
                "reachable": False,
                "message": "Set LIDARR_URL and LIDARR_API_KEY",
            }

        try:
            client = self._get_client()
            reachable = await client.ping()
            root_folder, quality_id, metadata_id = await self._resolve_defaults(client)
            return {
                "enabled": True,
                "configured": True,
                "reachable": reachable,
                "search_on_add": lidarr_search_on_add(),
                "root_folder": root_folder,
                "quality_profile_id": quality_id,
                "metadata_profile_id": metadata_id,
            }
        except Exception as exc:
            return {
                "enabled": True,
                "configured": True,
                "reachable": False,
                "message": str(exc),
            }

    async def lookup(
        self,
        lookup_type: str,
        term: str,
        artist: Optional[str] = None,
    ) -> Dict[str, Any]:
        client = self._get_client()
        if lookup_type == "album":
            search_term = term if artist is None else f"{term} {artist}".strip()
            raw = await client.lookup_album(search_term)
            ranked = _rank_candidates(
                raw,
                term,
                lambda item: _score_album_match(item, term, artist or ""),
            )
            return {
                "type": "album",
                "term": search_term,
                "candidates": [_candidate_summary_album(item) for item in ranked[:10]],
            }

        raw = await client.lookup_artist(term)
        ranked = _rank_candidates(raw, term, lambda item: _score_artist_match(item, term))
        return {
            "type": "artist",
            "term": term,
            "candidates": [_candidate_summary_artist(item) for item in ranked[:10]],
        }

    async def add_missing_item(
        self,
        suggestion: Dict[str, Any],
        mode: str,
        foreign_artist_id: Optional[str] = None,
        foreign_album_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if mode not in ("artist", "album"):
            return {"status": "error", "message": "mode must be artist or album"}

        client = self._get_client()
        root_folder, quality_id, metadata_id = await self._resolve_defaults(client)
        existing_ids = await self._load_existing_artist_ids(client)

        if mode == "artist":
            return await self._add_artist_flow(
                client,
                suggestion,
                root_folder,
                quality_id,
                metadata_id,
                existing_ids,
                foreign_artist_id=foreign_artist_id,
            )

        return await self._add_album_flow(
            client,
            suggestion,
            root_folder,
            quality_id,
            metadata_id,
            existing_ids,
            foreign_album_id=foreign_album_id,
            foreign_artist_id=foreign_artist_id,
        )

    async def add_missing_items_bulk(
        self,
        suggestions: List[Dict[str, Any]],
        search: bool = True,
        monitor_only_target_album: bool = True,
        skip_ambiguous: bool = True,
        prefer_album: bool = True,
    ) -> Dict[str, Any]:
        """Add multiple missing recommendations to Lidarr with per-item results."""
        results = []
        counts = {
            "added": 0,
            "already_exists": 0,
            "ambiguous": 0,
            "not_found": 0,
            "failed": 0,
            "skipped": 0,
        }

        for index, suggestion in enumerate(suggestions):
            try:
                if prefer_album and suggestion.get("album"):
                    result = await self._add_album_targeted_flow(
                        suggestion,
                        search=search,
                        monitor_only_target_album=monitor_only_target_album,
                        skip_ambiguous=skip_ambiguous,
                    )
                else:
                    result = await self.add_missing_item(suggestion, mode="artist")

                result["index"] = index
                status = result.get("status", "failed")
                if status in ("added_album", "added_artist", "monitored_album"):
                    counts["added"] += 1
                elif status == "already_exists":
                    counts["already_exists"] += 1
                elif status == "ambiguous":
                    counts["ambiguous"] += 1
                elif status == "not_found":
                    counts["not_found"] += 1
                elif status == "skipped":
                    counts["skipped"] += 1
                else:
                    counts["failed"] += 1
                results.append(result)
            except Exception as exc:
                counts["failed"] += 1
                results.append({
                    "index": index,
                    "status": "error",
                    "message": str(exc),
                    "mode": "album" if suggestion.get("album") else "artist",
                })

        return {
            "status": "completed",
            "counts": counts,
            "results": results,
        }

    async def _add_album_targeted_flow(
        self,
        suggestion: Dict[str, Any],
        search: bool,
        monitor_only_target_album: bool,
        skip_ambiguous: bool,
    ) -> Dict[str, Any]:
        """Add or monitor only the album that contains the missing recommendation."""
        client = self._get_client()
        root_folder, quality_id, metadata_id = await self._resolve_defaults(client)
        existing_artists = await client.list_artists()
        existing_by_foreign = {
            item.get("foreignArtistId"): item
            for item in existing_artists
            if item.get("foreignArtistId")
        }

        album_title = suggestion.get("album", "").strip()
        artist_name = suggestion.get("artist", "")
        candidate, status, candidates = await self._resolve_album_candidate(
            client,
            album_title,
            artist_name,
            suggestion,
        )

        if status == "ambiguous":
            return {
                "status": "skipped" if skip_ambiguous else "ambiguous",
                "mode": "album",
                "message": "Ambiguous Lidarr album match",
                "candidates": candidates,
            }
        if status == "not_found" or not candidate:
            return {
                "status": "not_found",
                "mode": "album",
                "message": f"No Lidarr album match for {album_title} by {artist_name}",
            }

        artist = dict(candidate.get("artist") or {})
        foreign_artist = artist.get("foreignArtistId")
        foreign_album = candidate.get("foreignAlbumId")
        existing_artist = existing_by_foreign.get(foreign_artist)

        if not existing_artist:
            artist.update({
                "rootFolderPath": root_folder,
                "qualityProfileId": quality_id,
                "metadataProfileId": metadata_id,
                "monitored": True,
                "addOptions": {
                    "monitor": "none" if monitor_only_target_album else "all",
                    "searchForMissingAlbums": False,
                },
            })
            payload = dict(candidate)
            payload["artist"] = artist
            payload["monitored"] = True
            payload["addOptions"] = {"searchForNewAlbum": search}
            added = await client.add_album(payload)

            # Refresh artist data after add so we can enforce album monitoring state.
            existing_artists = await client.list_artists()
            existing_artist = next(
                (item for item in existing_artists if item.get("foreignArtistId") == foreign_artist),
                None,
            )
            target_album_id = added.get("id")
            status_value = "added_album"
        else:
            target_album_id = None
            status_value = "monitored_album"

        monitored_album = None
        if existing_artist and existing_artist.get("id"):
            monitored_album = await self._monitor_target_album(
                client,
                int(existing_artist["id"]),
                foreign_album,
                monitor_only_target_album=monitor_only_target_album,
                search=search,
            )
            if monitored_album:
                target_album_id = monitored_album.get("id")
            else:
                payload = dict(candidate)
                payload["artist"] = existing_artist
                payload["monitored"] = True
                payload["addOptions"] = {"searchForNewAlbum": search}
                added = await client.add_album(payload)
                target_album_id = added.get("id")
                monitored_album = await self._monitor_target_album(
                    client,
                    int(existing_artist["id"]),
                    foreign_album,
                    monitor_only_target_album=monitor_only_target_album,
                    search=False,
                )
                if monitored_album:
                    target_album_id = monitored_album.get("id")

        return {
            "status": status_value,
            "mode": "album",
            "foreign_artist_id": foreign_artist,
            "foreign_album_id": foreign_album,
            "album_title": candidate.get("title"),
            "artist_name": artist.get("artistName") or (existing_artist or {}).get("artistName"),
            "lidarr_artist_id": (existing_artist or {}).get("id"),
            "lidarr_album_id": target_album_id,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _monitor_target_album(
        self,
        client: LidarrClient,
        artist_id: int,
        foreign_album_id: str,
        monitor_only_target_album: bool,
        search: bool,
    ) -> Optional[Dict[str, Any]]:
        albums = await client.get_albums_by_artist(artist_id)
        target_album = None
        for album in albums:
            is_target = album.get("foreignAlbumId") == foreign_album_id
            if is_target:
                target_album = album
            desired_monitored = is_target or (album.get("monitored") and not monitor_only_target_album)
            if album.get("monitored") != desired_monitored:
                updated_album = dict(album)
                updated_album["monitored"] = desired_monitored
                await client.update_album(updated_album)

        if target_album and search:
            await client.search_album(int(target_album["id"]))
        return target_album

    async def _add_artist_flow(
        self,
        client: LidarrClient,
        suggestion: Dict[str, Any],
        root_folder: str,
        quality_id: int,
        metadata_id: int,
        existing_ids: set,
        foreign_artist_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        artist_name = suggestion.get("artist", "")
        candidate, status, candidates = await self._resolve_artist_candidate(
            client,
            artist_name,
            suggestion,
            foreign_artist_id=foreign_artist_id,
        )
        if status == "ambiguous":
            return {"status": "ambiguous", "mode": "artist", "candidates": candidates}
        if status == "not_found" or not candidate:
            return {"status": "not_found", "mode": "artist", "message": f"No Lidarr match for {artist_name}"}

        foreign_id = candidate.get("foreignArtistId")
        if foreign_id in existing_ids:
            return {
                "status": "already_exists",
                "mode": "artist",
                "foreign_artist_id": foreign_id,
                "artist_name": candidate.get("artistName"),
            }

        payload = {
            "artistName": candidate.get("artistName"),
            "foreignArtistId": foreign_id,
            "qualityProfileId": quality_id,
            "metadataProfileId": metadata_id,
            "rootFolderPath": root_folder,
            "monitored": True,
            "addOptions": {
                "monitor": "all",
                "searchForMissingAlbums": lidarr_search_on_add(),
            },
        }
        if candidate.get("artistMetadataId") is not None:
            payload["artistMetadataId"] = candidate["artistMetadataId"]

        added = await client.add_artist(payload)
        return {
            "status": "added_artist",
            "mode": "artist",
            "foreign_artist_id": foreign_id,
            "artist_name": added.get("artistName") or candidate.get("artistName"),
            "lidarr_artist_id": added.get("id"),
            "added_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _add_album_flow(
        self,
        client: LidarrClient,
        suggestion: Dict[str, Any],
        root_folder: str,
        quality_id: int,
        metadata_id: int,
        existing_ids: set,
        foreign_album_id: Optional[str] = None,
        foreign_artist_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        album_title = suggestion.get("album", "").strip()
        artist_name = suggestion.get("artist", "")
        if not album_title:
            return {"status": "error", "message": "Album title required for album add"}

        candidate, status, candidates = await self._resolve_album_candidate(
            client,
            album_title,
            artist_name,
            suggestion,
            foreign_album_id=foreign_album_id,
        )
        if status == "ambiguous":
            return {"status": "ambiguous", "mode": "album", "candidates": candidates}
        if status == "not_found" or not candidate:
            return {
                "status": "not_found",
                "mode": "album",
                "message": f"No Lidarr album match for {album_title} by {artist_name}",
            }

        artist = dict(candidate.get("artist") or {})
        foreign_artist = artist.get("foreignArtistId")
        foreign_album = candidate.get("foreignAlbumId")

        if foreign_artist in existing_ids:
            existing_album = await self._artist_has_album(client, foreign_artist, foreign_album)
            if existing_album:
                return {
                    "status": "already_exists",
                    "mode": "album",
                    "foreign_artist_id": foreign_artist,
                    "foreign_album_id": foreign_album,
                    "album_title": candidate.get("title"),
                    "artist_name": artist.get("artistName"),
                }

        artist.update(
            {
                "rootFolderPath": root_folder,
                "qualityProfileId": quality_id,
                "metadataProfileId": metadata_id,
                "monitored": True,
                "addOptions": {
                    "monitor": "all",
                    "searchForMissingAlbums": lidarr_search_on_add(),
                },
            }
        )

        payload = dict(candidate)
        payload["artist"] = artist
        payload["monitored"] = True
        payload["addOptions"] = {"searchForNewAlbum": lidarr_search_on_add()}

        added = await client.add_album(payload)
        return {
            "status": "added_album",
            "mode": "album",
            "foreign_artist_id": foreign_artist,
            "foreign_album_id": foreign_album,
            "album_title": added.get("title") or candidate.get("title"),
            "artist_name": artist.get("artistName"),
            "lidarr_album_id": added.get("id"),
            "added_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _resolve_artist_candidate(
        self,
        client: LidarrClient,
        artist_name: str,
        suggestion: Dict[str, Any],
        foreign_artist_id: Optional[str] = None,
    ) -> Tuple[Optional[Dict[str, Any]], str, List[Dict[str, Any]]]:
        if foreign_artist_id:
            results = await client.lookup_artist(f"lidarr:{foreign_artist_id}")
            if not results:
                results = await client.lookup_artist(artist_name)
            for item in results:
                if item.get("foreignArtistId") == foreign_artist_id:
                    return item, "ok", []
            if results:
                return results[0], "ok", []

        results = await client.lookup_artist(artist_name)
        ranked = _rank_candidates(results, artist_name, lambda item: _score_artist_match(item, artist_name))

        if not ranked and self.db:
            mb_hit = await self._musicbrainz_fallback(suggestion)
            if mb_hit and mb_hit.get("artist_mbid"):
                results = await client.lookup_artist(f"mbid:{mb_hit['artist_mbid']}")
                ranked = _rank_candidates(
                    results,
                    mb_hit.get("artist", artist_name),
                    lambda item: _score_artist_match(item, mb_hit.get("artist", artist_name)),
                )

        if not ranked:
            return None, "not_found", []

        if _is_ambiguous(ranked):
            return None, "ambiguous", [_candidate_summary_artist(item) for item in ranked[:5]]

        return ranked[0], "ok", []

    async def _resolve_album_candidate(
        self,
        client: LidarrClient,
        album_title: str,
        artist_name: str,
        suggestion: Dict[str, Any],
        foreign_album_id: Optional[str] = None,
    ) -> Tuple[Optional[Dict[str, Any]], str, List[Dict[str, Any]]]:
        search_term = f"{album_title} {artist_name}".strip()
        if foreign_album_id:
            results = await client.lookup_album(f"lidarr:{foreign_album_id}")
            if not results:
                results = await client.lookup_album(search_term)
            for item in results:
                if item.get("foreignAlbumId") == foreign_album_id:
                    return item, "ok", []
            if results:
                return results[0], "ok", []

        results = await client.lookup_album(search_term)
        ranked = _rank_candidates(
            results,
            album_title,
            lambda item: _score_album_match(item, album_title, artist_name),
        )

        if not ranked:
            mb_hit = await self._musicbrainz_fallback(suggestion)
            if mb_hit:
                mb_term = f"{mb_hit.get('album') or album_title} {mb_hit.get('artist') or artist_name}".strip()
                results = await client.lookup_album(mb_term)
                ranked = _rank_candidates(
                    results,
                    mb_hit.get("album") or album_title,
                    lambda item: _score_album_match(
                        item,
                        mb_hit.get("album") or album_title,
                        mb_hit.get("artist") or artist_name,
                    ),
                )

        if not ranked:
            return None, "not_found", []

        if _is_ambiguous(ranked):
            return None, "ambiguous", [_candidate_summary_album(item) for item in ranked[:5]]

        return ranked[0], "ok", []

    async def _musicbrainz_fallback(self, suggestion: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        cache_key = None
        title = suggestion.get("title", "")
        artist = suggestion.get("artist", "")
        if not title or not artist:
            return None

        cache_key = f"mb:recording:{_normalize(title)}:{_normalize(artist)}"
        if self.db:
            cached = await self.db.get_cache(cache_key)
            if cached:
                return json.loads(cached)

        mb = self._get_mb_client()
        hits = await mb.search_recording(title, artist)
        if not hits:
            return None

        best = hits[0]
        if self.db and cache_key:
            await self.db.set_cache(cache_key, json.dumps(best), ttl_seconds=86400)
        return best

    async def _resolve_defaults(self, client: LidarrClient) -> Tuple[str, int, int]:
        root_folder = os.getenv("LIDARR_ROOT_FOLDER", "").strip()
        if not root_folder:
            folders = await client.get_root_folders()
            if folders:
                root_folder = folders[0].get("path", "")
        if not root_folder:
            raise ValueError("LIDARR_ROOT_FOLDER is not set and no Lidarr root folders were found")

        quality_id = int(os.getenv("LIDARR_QUALITY_PROFILE_ID", "0") or "0")
        if not quality_id:
            profiles = await client.get_quality_profiles()
            if not profiles:
                raise ValueError("No Lidarr quality profiles found")
            quality_id = int(profiles[0]["id"])

        metadata_id = int(os.getenv("LIDARR_METADATA_PROFILE_ID", "0") or "0")
        if not metadata_id:
            profiles = await client.get_metadata_profiles()
            if not profiles:
                raise ValueError("No Lidarr metadata profiles found")
            metadata_id = int(profiles[0]["id"])

        return root_folder, quality_id, metadata_id

    async def _load_existing_artist_ids(self, client: LidarrClient) -> set:
        if self._existing_artist_ids is not None:
            return self._existing_artist_ids
        artists = await client.list_artists()
        self._existing_artist_ids = {
            item.get("foreignArtistId")
            for item in artists
            if item.get("foreignArtistId")
        }
        return self._existing_artist_ids

    async def _artist_has_album(
        self,
        client: LidarrClient,
        foreign_artist_id: str,
        foreign_album_id: str,
    ) -> bool:
        artists = await client.list_artists()
        for artist in artists:
            if artist.get("foreignArtistId") != foreign_artist_id:
                continue
            artist_id = artist.get("id")
            if not artist_id:
                return False
            albums = await client.get_albums_by_artist(int(artist_id))
            if isinstance(albums, list):
                return any(a.get("foreignAlbumId") == foreign_album_id for a in albums)
        return False
