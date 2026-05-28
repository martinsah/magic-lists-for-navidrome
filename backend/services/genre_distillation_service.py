import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..services.ai_providers import get_ai_provider


class GenreDistillationService:
    """Build and validate a simplified meta-genre grouping from raw genres."""

    def _canonicalize_raw_genres(self, raw_genres: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        canonical = []
        for genre in raw_genres or []:
            name = str(genre.get("name") or "").strip()
            if not name:
                continue
            canonical.append(
                {
                    "name": name,
                    "songCount": int(genre.get("songCount") or 0),
                }
            )
        canonical.sort(key=lambda g: (g["name"].lower(), -g["songCount"]))
        return canonical

    def source_hash(self, raw_genres: List[Dict[str, Any]]) -> str:
        canonical = self._canonicalize_raw_genres(raw_genres)
        payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def _fallback_groups(self, raw_genres: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        groups = []
        for item in sorted(raw_genres, key=lambda g: g.get("songCount", 0), reverse=True):
            groups.append(
                {
                    "meta_genre": item["name"],
                    "genres": [item["name"]],
                    "total_song_count": int(item.get("songCount") or 0),
                }
            )
        return groups

    async def distill(self, raw_genres: List[Dict[str, Any]]) -> Dict[str, Any]:
        canonical = self._canonicalize_raw_genres(raw_genres)
        generated_at = datetime.now().isoformat()
        if not canonical:
            return {
                "groups": [],
                "raw_genre_count": 0,
                "generated_at": generated_at,
                "model_name": None,
            }

        provider = None
        try:
            provider = get_ai_provider()
        except Exception:
            provider = None

        if not provider:
            return {
                "groups": self._fallback_groups(canonical),
                "raw_genre_count": len(canonical),
                "generated_at": generated_at,
                "model_name": None,
            }

        system_prompt = (
            "You are a music taxonomy assistant. Group raw genres into broader meta-genres. "
            "Return JSON only. Each raw genre must appear exactly once across all groups."
        )
        user_prompt = (
            "Given this list of raw genres with song counts, return JSON with shape "
            '{"groups":[{"meta_genre":"string","genres":["raw1","raw2"],"total_song_count":123}]}. '
            "Use concise meta-genre names and keep groups meaningful.\n\n"
            f"raw_genres={json.dumps(canonical, ensure_ascii=False)}"
        )

        try:
            content = await provider.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=4096,
                temperature=0.2,
                json_response=True,
            )
            parsed = json.loads(content)
            groups = self._validate_groups(parsed.get("groups", []), canonical)
            if not groups:
                groups = self._fallback_groups(canonical)
            return {
                "groups": groups,
                "raw_genre_count": len(canonical),
                "generated_at": generated_at,
                "model_name": provider.model,
            }
        except Exception:
            return {
                "groups": self._fallback_groups(canonical),
                "raw_genre_count": len(canonical),
                "generated_at": generated_at,
                "model_name": getattr(provider, "model", None),
            }

    def _validate_groups(
        self,
        candidate_groups: List[Dict[str, Any]],
        canonical_genres: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        available = {item["name"] for item in canonical_genres}
        counts = {item["name"]: int(item.get("songCount") or 0) for item in canonical_genres}
        used = set()
        normalized_groups = []

        for group in candidate_groups or []:
            meta_name = str(group.get("meta_genre") or "").strip()
            members = group.get("genres") or []
            if not meta_name or not isinstance(members, list):
                continue
            cleaned_members = []
            for member in members:
                member_name = str(member).strip()
                if not member_name or member_name not in available or member_name in used:
                    continue
                used.add(member_name)
                cleaned_members.append(member_name)
            if not cleaned_members:
                continue
            normalized_groups.append(
                {
                    "meta_genre": meta_name,
                    "genres": sorted(cleaned_members),
                    "total_song_count": sum(counts[g] for g in cleaned_members),
                }
            )

        # Ensure every raw genre appears exactly once by adding leftovers.
        leftovers = sorted(list(available - used))
        for genre_name in leftovers:
            normalized_groups.append(
                {
                    "meta_genre": genre_name,
                    "genres": [genre_name],
                    "total_song_count": counts[genre_name],
                }
            )

        normalized_groups.sort(key=lambda g: g["total_song_count"], reverse=True)
        return normalized_groups

    def resolve_meta_genre(
        self,
        meta_genre: str,
        snapshot: Optional[Dict[str, Any]],
    ) -> List[str]:
        if not meta_genre or not snapshot:
            return []
        payload = snapshot.get("payload") or {}
        groups = payload.get("groups") or []
        for group in groups:
            if str(group.get("meta_genre") or "").strip().lower() == meta_genre.strip().lower():
                return [str(g) for g in (group.get("genres") or []) if str(g).strip()]
        return []
