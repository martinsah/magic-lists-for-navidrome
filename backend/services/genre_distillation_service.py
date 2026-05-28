import hashlib
import json
import logging
import re
from difflib import SequenceMatcher
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..services.ai_providers import get_ai_provider
from .meta_genre_granularity import (
    build_meta_distillation_prompts,
    normalize_meta_genre_granularity,
    target_meta_group_range,
)


class GenreDistillationService:
    """Build and validate a simplified meta-genre grouping from raw genres."""
    def __init__(self) -> None:
        self.logger = logging.getLogger("scheduler")

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

    def _normalize_genre_key(self, value: str) -> str:
        text = (value or "").lower().strip()
        text = text.replace("&", "and")
        text = re.sub(r"[-_/]", " ", text)
        text = re.sub(r"[^\w\s]", "", text)
        text = re.sub(r"\s+", " ", text)
        return text

    def _singleton_ratio(self, groups: List[Dict[str, Any]]) -> float:
        if not groups:
            return 0.0
        singletons = sum(1 for group in groups if len(group.get("genres") or []) <= 1)
        return singletons / len(groups)

    def _tokenize_genre(self, value: str) -> List[str]:
        stopwords = {"and", "the", "music", "genre"}
        normalized = self._normalize_genre_key(value)
        return [token for token in normalized.split(" ") if token and token not in stopwords]

    def _best_group_for_leftover(
        self,
        leftover: str,
        groups: List[Dict[str, Any]],
    ) -> Tuple[int, float]:
        leftover_norm = self._normalize_genre_key(leftover)
        leftover_tokens = set(self._tokenize_genre(leftover))
        best_idx = -1
        best_score = 0.0
        for idx, group in enumerate(groups):
            group_tokens = set(self._tokenize_genre(str(group.get("meta_genre", ""))))
            members = group.get("genres") or []
            for member in members:
                group_tokens.update(self._tokenize_genre(str(member)))
            token_overlap = (
                len(leftover_tokens & group_tokens) / len(leftover_tokens)
                if leftover_tokens else 0.0
            )
            member_similarity = 0.0
            for member in members:
                member_norm = self._normalize_genre_key(str(member))
                member_similarity = max(
                    member_similarity,
                    SequenceMatcher(None, leftover_norm, member_norm).ratio(),
                )
            meta_similarity = SequenceMatcher(
                None,
                leftover_norm,
                self._normalize_genre_key(str(group.get("meta_genre", ""))),
            ).ratio()
            score = max(member_similarity, meta_similarity) * 0.6 + token_overlap * 0.4
            if score > best_score:
                best_score = score
                best_idx = idx
        return best_idx, best_score

    async def distill(
        self,
        raw_genres: List[Dict[str, Any]],
        granularity: str = "balanced",
    ) -> Dict[str, Any]:
        granularity = normalize_meta_genre_granularity(granularity)
        canonical = self._canonicalize_raw_genres(raw_genres)
        generated_at = datetime.now().isoformat()
        target_low, target_high = target_meta_group_range(len(canonical), granularity)
        diagnostics: Dict[str, Any] = {
            "llm_attempted": False,
            "provider_available": False,
            "fallback_used": False,
            "fallback_reason": None,
            "raw_genre_count": len(canonical),
            "granularity": granularity,
            "target_group_low": target_low,
            "target_group_high": target_high,
        }
        if not canonical:
            return {
                "groups": [],
                "raw_genre_count": 0,
                "generated_at": generated_at,
                "model_name": None,
                "diagnostics": diagnostics,
            }

        provider = None
        try:
            provider = get_ai_provider()
        except Exception:
            provider = None

        diagnostics["provider_available"] = bool(provider)
        if not provider:
            diagnostics["fallback_used"] = True
            diagnostics["fallback_reason"] = "provider_unavailable"
            groups = self._fallback_groups(canonical)
            diagnostics["singleton_ratio"] = self._singleton_ratio(groups)
            self.logger.warning("⚠️ Meta-genre distillation fallback: provider unavailable")
            return {
                "groups": groups,
                "raw_genre_count": len(canonical),
                "generated_at": generated_at,
                "model_name": None,
                "diagnostics": diagnostics,
            }

        system_prompt, user_prompt, _prompt_meta = build_meta_distillation_prompts(
            canonical, granularity
        )
        max_tokens = 8192 if granularity == "fine" and len(canonical) > 150 else 4096

        diagnostics["llm_attempted"] = True
        self.logger.info(
            "Starting meta-genre distillation: raw=%s, granularity=%s, target_groups=%s-%s, model=%s",
            len(canonical),
            granularity,
            target_low,
            target_high,
            provider.model,
        )
        try:
            distillation_schema = {
                "type": "OBJECT",
                "properties": {
                    "groups": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "meta_genre": {"type": "STRING"},
                                "genres": {
                                    "type": "ARRAY",
                                    "items": {"type": "STRING"},
                                },
                                "total_song_count": {"type": "NUMBER"},
                            },
                            "required": ["meta_genre", "genres"],
                        },
                    }
                },
                "required": ["groups"],
            }
            content = await provider.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                temperature=0.2,
                json_response=True,
                response_schema=distillation_schema,
                call_label="meta_genre_distillation",
            )
            diagnostics["response_preview"] = content[:600]
            parsed = json.loads(content)
            if parsed.get("groups") is None and parsed.get("track_ids") is not None:
                diagnostics["fallback_used"] = True
                diagnostics["fallback_reason"] = "schema_mismatch_track_ids_instead_of_groups"
            groups, validation_stats = self._validate_groups(parsed.get("groups", []), canonical)
            diagnostics.update(validation_stats)
            diagnostics["singleton_ratio"] = self._singleton_ratio(groups)
            if diagnostics.get("matched_member_count", 0) == 0 and len(canonical) > 0:
                diagnostics["fallback_used"] = True
                diagnostics["fallback_reason"] = "llm_groups_did_not_match_raw_genres"
            if not groups:
                diagnostics["fallback_used"] = True
                diagnostics["fallback_reason"] = "no_valid_groups_after_validation"
                groups = self._fallback_groups(canonical)
                diagnostics["singleton_ratio"] = self._singleton_ratio(groups)
            self.logger.info(
                "🧠 Meta-genre distillation complete: candidate_groups=%s, validated_groups=%s, matched=%s, leftovers=%s, singleton_ratio=%.2f",
                diagnostics.get("candidate_group_count", 0),
                diagnostics.get("validated_group_count", len(groups)),
                diagnostics.get("matched_member_count", 0),
                diagnostics.get("leftover_count", 0),
                diagnostics.get("singleton_ratio", 0.0),
            )
            return {
                "groups": groups,
                "raw_genre_count": len(canonical),
                "generated_at": generated_at,
                "model_name": provider.model,
                "diagnostics": diagnostics,
            }
        except Exception as exc:
            diagnostics["fallback_used"] = True
            diagnostics["fallback_reason"] = f"llm_exception:{type(exc).__name__}"
            self.logger.warning("⚠️ Meta-genre distillation fallback due to exception: %s", exc)
            groups = self._fallback_groups(canonical)
            diagnostics["singleton_ratio"] = self._singleton_ratio(groups)
            return {
                "groups": groups,
                "raw_genre_count": len(canonical),
                "generated_at": generated_at,
                "model_name": getattr(provider, "model", None),
                "diagnostics": diagnostics,
            }

    def _validate_groups(
        self,
        candidate_groups: List[Dict[str, Any]],
        canonical_genres: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        available = {item["name"] for item in canonical_genres}
        counts = {item["name"]: int(item.get("songCount") or 0) for item in canonical_genres}
        alias_map = {self._normalize_genre_key(name): name for name in available}
        used = set()
        normalized_groups = []
        matched_count = 0
        unmatched_count = 0

        for group in candidate_groups or []:
            meta_name = str(group.get("meta_genre") or "").strip()
            members = group.get("genres") or []
            if not meta_name or not isinstance(members, list):
                continue
            cleaned_members = []
            for member in members:
                member_name = str(member).strip()
                if not member_name:
                    continue
                resolved_member = member_name
                if resolved_member not in available:
                    resolved_member = alias_map.get(self._normalize_genre_key(member_name), "")
                if not resolved_member:
                    unmatched_count += 1
                    continue
                if resolved_member in used:
                    continue
                matched_count += 1
                used.add(resolved_member)
                cleaned_members.append(resolved_member)
            if not cleaned_members:
                continue
            normalized_groups.append(
                {
                    "meta_genre": meta_name,
                    "genres": sorted(cleaned_members),
                    "total_song_count": sum(counts[g] for g in cleaned_members),
                }
            )

        # Ensure every raw genre appears exactly once by assigning leftovers.
        leftovers = sorted(list(available - used))
        merged_leftovers = 0
        singleton_leftovers = 0
        for genre_name in leftovers:
            target_idx, score = self._best_group_for_leftover(genre_name, normalized_groups)
            if target_idx >= 0 and score >= 0.46:
                target_group = normalized_groups[target_idx]
                existing = set(target_group.get("genres") or [])
                if genre_name not in existing:
                    target_group["genres"] = sorted((target_group.get("genres") or []) + [genre_name])
                    target_group["total_song_count"] = int(target_group.get("total_song_count", 0)) + counts[genre_name]
                    merged_leftovers += 1
                    continue
            normalized_groups.append(
                {
                    "meta_genre": genre_name,
                    "genres": [genre_name],
                    "total_song_count": counts[genre_name],
                }
            )
            singleton_leftovers += 1

        normalized_groups.sort(key=lambda g: g["total_song_count"], reverse=True)
        stats = {
            "candidate_group_count": len(candidate_groups or []),
            "validated_group_count": len(normalized_groups),
            "matched_member_count": matched_count,
            "unmatched_member_count": unmatched_count,
            "leftover_count": len(leftovers),
            "merged_leftovers_count": merged_leftovers,
            "singleton_leftovers_count": singleton_leftovers,
        }
        return normalized_groups, stats

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
