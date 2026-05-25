"""Async client for Lidarr API v1."""

import os
from typing import Any, Dict, List, Optional

import httpx


class LidarrClient:
    """Thin HTTP client for Lidarr."""

    def __init__(self) -> None:
        base = os.getenv("LIDARR_URL", "").rstrip("/")
        if not base:
            raise ValueError("LIDARR_URL environment variable is required")
        self.base_url = base
        self.api_key = os.getenv("LIDARR_API_KEY", "")
        if not self.api_key:
            raise ValueError("LIDARR_API_KEY environment variable is required")
        self.client = httpx.AsyncClient(timeout=30.0)
        self._base_url_verified = False

    def _headers(self) -> Dict[str, str]:
        return {"X-Api-Key": self.api_key, "Content-Type": "application/json"}

    def _candidate_base_urls(self) -> List[str]:
        """Build candidate API roots (direct port vs reverse-proxy subpath)."""
        base = self.base_url.rstrip("/")
        candidates = [base]
        if base.endswith("/lidarr"):
            candidates.append(base[: -len("/lidarr")].rstrip("/"))
        else:
            candidates.append(f"{base}/lidarr")
        seen = set()
        ordered: List[str] = []
        for url in candidates:
            if url and url not in seen:
                seen.add(url)
                ordered.append(url)
        return ordered

    async def _ensure_base_url(self) -> None:
        """Pick a base URL that returns JSON from the Lidarr API."""
        if self._base_url_verified:
            return

        last_error: Optional[Exception] = None
        for candidate in self._candidate_base_urls():
            try:
                response = await self.client.get(
                    f"{candidate}/api/v1/system/status",
                    headers=self._headers(),
                )
                if response.status_code != 200:
                    continue
                content_type = response.headers.get("content-type", "")
                if "application/json" not in content_type:
                    continue
                response.json()
                self.base_url = candidate
                self._base_url_verified = True
                return
            except Exception as exc:
                last_error = exc

        if last_error:
            raise last_error
        raise ValueError(
            "Could not reach Lidarr API. For Docker use LIDARR_URL=http://lidarr:8686 "
            "(no /lidarr suffix unless your reverse proxy serves Lidarr on a subpath)."
        )

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        await self._ensure_base_url()
        response = await self.client.get(
            f"{self.base_url}{path}",
            params=params or {},
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()

    async def _post(self, path: str, payload: Dict[str, Any]) -> Any:
        await self._ensure_base_url()
        response = await self.client.post(
            f"{self.base_url}{path}",
            json=payload,
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()

    async def ping(self) -> bool:
        try:
            await self._ensure_base_url()
            return True
        except Exception:
            return False

    async def lookup_artist(self, term: str) -> List[Dict[str, Any]]:
        results = await self._get("/api/v1/artist/lookup", {"term": term})
        return results if isinstance(results, list) else []

    async def lookup_album(self, term: str) -> List[Dict[str, Any]]:
        results = await self._get("/api/v1/album/lookup", {"term": term})
        return results if isinstance(results, list) else []

    async def list_artists(self) -> List[Dict[str, Any]]:
        results = await self._get("/api/v1/artist")
        return results if isinstance(results, list) else []

    async def get_root_folders(self) -> List[Dict[str, Any]]:
        results = await self._get("/api/v1/rootFolder")
        return results if isinstance(results, list) else []

    async def get_quality_profiles(self) -> List[Dict[str, Any]]:
        results = await self._get("/api/v1/qualityprofile")
        return results if isinstance(results, list) else []

    async def get_metadata_profiles(self) -> List[Dict[str, Any]]:
        results = await self._get("/api/v1/metadataprofile")
        return results if isinstance(results, list) else []

    async def add_artist(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._post("/api/v1/artist", payload)
        return result if isinstance(result, dict) else {}

    async def add_album(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._post("/api/v1/album", payload)
        return result if isinstance(result, dict) else {}

    async def get_albums_by_artist(self, artist_id: int) -> List[Dict[str, Any]]:
        results = await self._get("/api/v1/album", {"artistId": artist_id})
        return results if isinstance(results, list) else []

    async def close(self) -> None:
        await self.client.aclose()
