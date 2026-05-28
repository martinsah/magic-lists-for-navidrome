from fastapi import FastAPI, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.responses import HTMLResponse
from fastapi import Query
import uvicorn
import os
import logging
import logging.handlers
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio

# Load environment variables first
load_dotenv()

# Get log level from environment (ERROR=minimal, INFO=normal, DEBUG=verbose)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Configure logging for scheduler activities with rotation
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.handlers.RotatingFileHandler(
            'scheduler.log',
            maxBytes=5*1024*1024,  # 5MB per file
            backupCount=2,         # Keep 2 old files (total ~10MB)
            encoding='utf-8'
        ),
        logging.StreamHandler()  # Also log to console
    ]
)

# Create a specific logger for scheduler activities
scheduler_logger = logging.getLogger('scheduler')
logging.getLogger('llm').setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

# Reduce httpx logging verbosity to avoid cluttering scheduler.log
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)

from .navidrome_client import NavidromeClient
from .ai_client import AIClient
from .database import DatabaseManager, get_db
from .schemas import CreatePlaylistRequest, CreateGenrePlaylistRequest, Playlist, RediscoverWeeklyResponse, RediscoverWeeklyV2Response, CreateRediscoverPlaylistRequest, PlaylistWithScheduleInfo, LidarrAddRequest, LidarrBulkAddRequest, PlaylistSettingsRequest, ScheduledPlaylist, MetaGenreResponse, MetaGenreInsightsResponse, MetaGenreSettingsRequest, MetaGenreSettingsResponse
from .recipe_manager import recipe_manager
from .rediscover import RediscoverWeekly, ReDiscoverV2Processor
from .track_scoring import filter_tracks_for_this_is_playlist
from .curation_strategy import (
    SCORING_VERSION,
    assemble_playlist_candidates,
    build_genre_mix_llm_pool,
)
from .suggestion_service import (
    process_playlist_suggestions,
    unpack_curation_result,
    missing_recommendations_enabled,
)
from .lidarr_service import LidarrService, lidarr_integration_enabled, lidarr_configured
from .services.genre_distillation_service import GenreDistillationService
# SYSTEM CHECK FEATURE - START
from .services.health_check_service import HealthCheckService
# SYSTEM CHECK FEATURE - END


async def _apply_missing_recommendations(
    nav_client: NavidromeClient,
    db: DatabaseManager,
    playlist_db_id: int,
    navidrome_playlist_id: str,
    suggested_tracks: list,
    curated_track_ids: list,
    track_id_to_title: dict,
    library_ids: Optional[List[str]] = None,
) -> tuple:
    """Post-create pass: append library matches, persist missing suggestions."""
    if not missing_recommendations_enabled() or not suggested_tracks:
        song_titles = [track_id_to_title.get(tid, "Unknown") for tid in curated_track_ids]
        return song_titles, [], 0

    _, added_count, song_titles = await process_playlist_suggestions(
        nav_client=nav_client,
        db=db,
        playlist_db_id=playlist_db_id,
        navidrome_playlist_id=navidrome_playlist_id,
        suggested_tracks=suggested_tracks,
        existing_track_ids=curated_track_ids,
        track_id_to_title=track_id_to_title,
        library_ids=library_ids,
    )
    playlists = await db.get_all_playlists_with_schedule_info()
    row = next((p for p in playlists if p.get("id") == playlist_db_id), None)
    missing = row.get("recommended_missing", []) if row else []
    return song_titles, missing, added_count


def _comment_with_playlist_date(base_comment: Optional[str], is_update: bool) -> str:
    """Append create/update date to playlist comment persisted in Navidrome."""
    date_stamp = datetime.now().strftime("%Y-%m-%d")
    label = "Last updated" if is_update else "Created"
    date_line = f"{label}: {date_stamp}"
    content = (base_comment or "").strip()
    if content:
        return f"{content}\n\n{date_line}"
    return date_line


app = FastAPI(title="MagicLists Navidrome MVP")

@app.on_event("startup")
async def startup_event():
    """Initialize scheduler on app startup"""
    global scheduler, system_check_passed, system_check_results
    scheduler = AsyncIOScheduler()
    scheduler.start()
    scheduler_logger.info("✅ Scheduler started successfully")
    # Auto-start the cron job
    await start_scheduler_job()
    scheduler_logger.info("✅ Cron job auto-started on application startup")
    
    # SYSTEM CHECK FEATURE - START
    # Run system checks on startup
    try:
        health_service = HealthCheckService()
        system_check_results = await health_service.run_checks()
        system_check_passed = system_check_results.get("all_passed", False)
        
        if system_check_passed:
            scheduler_logger.info("✅ System health checks passed on startup")
        else:
            scheduler_logger.warning("⚠️ System health checks failed on startup - user will be redirected to system check page")
            
        # Log individual check results with enhanced AI provider logging
        for check in system_check_results.get("checks", []):
            status_emoji = "✅" if check["status"] == "success" else "⚠️" if check["status"] == "warning" else "ℹ️" if check["status"] == "info" else "❌"
            
            # Enhanced logging for AI Provider checks
            if "AI Provider" in check["name"]:
                ai_provider = os.getenv("AI_PROVIDER", "openrouter")
                if check["status"] == "success":
                    # Extract model from success message (e.g., "service reachable (model: llama3.2)")
                    if "model:" in check["message"]:
                        model_part = check["message"].split("model: ")[1].rstrip(")")
                        scheduler_logger.info(f"🤖 AI Provider: {ai_provider.title()} with model '{model_part}' - Ready")
                    else:
                        scheduler_logger.info(f"🤖 AI Provider: {ai_provider.title()} - Ready")
                elif check["status"] == "warning":
                    if "not set" in check["message"]:
                        scheduler_logger.info(f"🤖 AI Provider: {ai_provider.title()} - No API key (using fallback algorithms)")
                    else:
                        scheduler_logger.warning(f"🤖 AI Provider: {ai_provider.title()} - {check['message']}")
                elif check["status"] == "error":
                    scheduler_logger.error(f"🤖 AI Provider: {ai_provider.title()} - {check['message']}")
            else:
                # Standard logging for other checks
                scheduler_logger.info(f"{status_emoji} {check['name']}: {check['status']}")
            
    except Exception as e:
        scheduler_logger.error(f"❌ Failed to run system checks on startup: {e}")
        system_check_passed = False
        system_check_results = {
            "all_passed": False,
            "checks": [{
                "name": "System Check Service",
                "status": "error", 
                "message": f"Failed to run health checks: {str(e)}",
                "suggestion": "Check application logs and restart the service"
            }]
        }
    # SYSTEM CHECK FEATURE - END

@app.on_event("shutdown") 
async def shutdown_event():
    """Cleanup scheduler on app shutdown"""
    global scheduler
    if scheduler:
        scheduler.shutdown()
        scheduler_logger.info("🛑 Scheduler shutdown completed")

# Mount static files
app.mount("/static", StaticFiles(directory="frontend/static"), name="static")

# Templates
templates = Jinja2Templates(directory="frontend/templates")

# Initialize clients (lazy loading)
navidrome_client = None
ai_client = None
genre_distillation_service = None

# Initialize scheduler (will be started on app startup)
scheduler = None

# SYSTEM CHECK FEATURE - START
# App state to track system check results
system_check_passed = False
system_check_results = None
# SYSTEM CHECK FEATURE - END

def get_navidrome_client():
    global navidrome_client
    if navidrome_client is None:
        navidrome_client = NavidromeClient()
    return navidrome_client

def get_ai_client():
    global ai_client
    if ai_client is None:
        ai_client = AIClient()
    return ai_client


def get_genre_distillation_service():
    global genre_distillation_service
    if genre_distillation_service is None:
        genre_distillation_service = GenreDistillationService()
    return genre_distillation_service


def get_lidarr_service(db: DatabaseManager) -> LidarrService:
    return LidarrService(db=db)


def _meta_genre_source_key(library_ids: Optional[List[str]], min_song_count: int) -> str:
    ids = ",".join(sorted([str(i) for i in (library_ids or [])]))
    return f"libraries={ids}|min_song_count={min_song_count}"


def _meta_refresh_due(last_refreshed_iso: Optional[str], frequency: str) -> bool:
    if frequency == "none":
        return False
    if not last_refreshed_iso:
        return True
    try:
        last = datetime.fromisoformat(last_refreshed_iso)
    except ValueError:
        return True

    elapsed = datetime.now() - last
    if frequency == "daily":
        return elapsed >= timedelta(days=1)
    if frequency == "weekly":
        return elapsed >= timedelta(days=7)
    if frequency == "monthly":
        return elapsed >= timedelta(days=30)
    return False


def _normalize_meta_frequency(value: Optional[str]) -> str:
    normalized = (value or "weekly").strip().lower()
    if normalized not in {"none", "daily", "weekly", "monthly"}:
        return "weekly"
    return normalized


def _safe_int(value: Optional[str], default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


async def _get_effective_meta_genre_settings(db: DatabaseManager) -> Dict[str, int | str]:
    refresh_frequency = _normalize_meta_frequency(
        await db.get_config("meta_genre_refresh_frequency")
        or os.getenv("META_GENRE_REFRESH_FREQUENCY", "weekly")
    )
    min_song_count = _safe_int(
        await db.get_config("meta_genre_min_song_count")
        or os.getenv("META_GENRE_MIN_SONG_COUNT", "0"),
        default=0,
        minimum=0,
        maximum=20000,
    )
    min_raw_genres = _safe_int(
        await db.get_config("meta_genre_min_raw_genres")
        or os.getenv("META_GENRE_MIN_RAW_GENRES", "30"),
        default=30,
        minimum=1,
        maximum=5000,
    )
    cache_hours = _safe_int(
        await db.get_config("meta_genre_cache_hours")
        or os.getenv("META_GENRE_CACHE_HOURS", "168"),
        default=168,
        minimum=1,
        maximum=24 * 365,
    )
    return {
        "refresh_frequency": refresh_frequency,
        "min_song_count": min_song_count,
        "min_raw_genres": min_raw_genres,
        "cache_hours": cache_hours,
    }


async def _set_meta_genre_settings(db: DatabaseManager, request: MetaGenreSettingsRequest) -> MetaGenreSettingsResponse:
    refresh_frequency = _normalize_meta_frequency(request.refresh_frequency)
    min_song_count = _safe_int(str(request.min_song_count), default=0, minimum=0, maximum=20000)
    min_raw_genres = _safe_int(str(request.min_raw_genres), default=30, minimum=1, maximum=5000)
    cache_hours = _safe_int(str(request.cache_hours), default=168, minimum=1, maximum=24 * 365)

    await db.set_config("meta_genre_refresh_frequency", refresh_frequency)
    await db.set_config("meta_genre_min_song_count", str(min_song_count))
    await db.set_config("meta_genre_min_raw_genres", str(min_raw_genres))
    await db.set_config("meta_genre_cache_hours", str(cache_hours))

    return MetaGenreSettingsResponse(
        refresh_frequency=refresh_frequency,
        min_song_count=min_song_count,
        min_raw_genres=min_raw_genres,
        cache_hours=cache_hours,
    )


async def _build_or_refresh_meta_genres(
    db: DatabaseManager,
    library_ids: Optional[List[str]],
    min_song_count: int,
    force: bool = False,
    min_raw_genres_override: Optional[int] = None,
    cache_hours_override: Optional[int] = None,
) -> Dict[str, Any]:
    nav_client = get_navidrome_client()
    distillation_service = get_genre_distillation_service()
    source_key = _meta_genre_source_key(library_ids, min_song_count)
    settings = await _get_effective_meta_genre_settings(db)
    cache_hours = int(cache_hours_override if cache_hours_override is not None else settings["cache_hours"])
    min_raw_genres = int(min_raw_genres_override if min_raw_genres_override is not None else settings["min_raw_genres"])

    raw_genres = await nav_client.get_genres(library_ids)
    if min_song_count > 0:
        raw_genres = [g for g in raw_genres if int(g.get("songCount", 0)) >= min_song_count]
    scheduler_logger.info(
        "🧠 Meta-genre distillation request: source_key=%s, raw_genres=%s, force=%s",
        source_key,
        len(raw_genres),
        force,
    )
    source_hash = distillation_service.source_hash(raw_genres)

    snapshot = await db.get_meta_genre_snapshot(source_key)
    is_stale = await db.is_meta_genre_snapshot_stale(source_key, max_age_hours=cache_hours)

    if (
        snapshot
        and not force
        and not is_stale
        and snapshot.get("source_hash") == source_hash
        and snapshot.get("payload")
    ):
        payload = snapshot["payload"]
        payload["stale"] = False
        payload["source_hash"] = snapshot.get("source_hash")
        payload["source_key"] = source_key
        payload["model_name"] = snapshot.get("model_name")
        return payload

    if len(raw_genres) < min_raw_genres and snapshot and not force:
        payload = snapshot["payload"]
        payload["stale"] = True
        payload["source_hash"] = snapshot.get("source_hash")
        payload["source_key"] = source_key
        payload["model_name"] = snapshot.get("model_name")
        return payload

    distilled = await distillation_service.distill(raw_genres)
    payload = {
        "groups": distilled.get("groups", []),
        "generated_at": distilled.get("generated_at") or datetime.now().isoformat(),
        "raw_genre_count": distilled.get("raw_genre_count", len(raw_genres)),
        "diagnostics": distilled.get("diagnostics", {}),
    }

    new_groups = payload.get("groups") or []
    new_singleton_ratio = (
        sum(1 for group in new_groups if len(group.get("genres") or []) <= 1) / len(new_groups)
        if new_groups else 0.0
    )
    if snapshot and new_groups:
        previous_payload = snapshot.get("payload") or {}
        previous_groups = previous_payload.get("groups") or []
        previous_ratio = (
            sum(1 for group in previous_groups if len(group.get("genres") or []) <= 1) / len(previous_groups)
            if previous_groups else 1.0
        )
        if new_singleton_ratio >= 0.95 and previous_ratio < 0.95:
            scheduler_logger.warning(
                "⚠️ Rejecting degraded meta-genre snapshot for %s: new singleton ratio %.2f > previous %.2f",
                source_key,
                new_singleton_ratio,
                previous_ratio,
            )
            preserved = previous_payload
            preserved["stale"] = False
            preserved["source_hash"] = snapshot.get("source_hash")
            preserved["source_key"] = source_key
            preserved["model_name"] = snapshot.get("model_name")
            diagnostics = preserved.get("diagnostics") or {}
            diagnostics["degraded_snapshot_rejected"] = True
            diagnostics["rejected_singleton_ratio"] = new_singleton_ratio
            diagnostics["previous_singleton_ratio"] = previous_ratio
            preserved["diagnostics"] = diagnostics
            return preserved

    await db.upsert_meta_genre_snapshot(
        source_key=source_key,
        source_hash=source_hash,
        payload=payload,
        raw_genre_count=payload["raw_genre_count"],
        model_name=distilled.get("model_name"),
        generated_at=payload["generated_at"],
    )
    payload["source_hash"] = source_hash
    payload["source_key"] = source_key
    payload["model_name"] = distilled.get("model_name")
    payload["stale"] = False
    payload["diagnostics"] = distilled.get("diagnostics", {})
    scheduler_logger.info(
        "🧠 Meta-genre snapshot updated: source_key=%s, groups=%s, singleton_ratio=%.2f, fallback=%s",
        source_key,
        len(payload.get("groups") or []),
        (
            sum(1 for group in (payload.get("groups") or []) if len(group.get("genres") or []) <= 1)
            / len(payload.get("groups") or [])
            if payload.get("groups") else 0.0
        ),
        bool((payload.get("diagnostics") or {}).get("fallback_used")),
    )
    return payload

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Serve the main HTML page"""
    # SYSTEM CHECK FEATURE - START
    # Redirect to system check if checks haven't passed
    if not system_check_passed:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/system-check", status_code=302)
    # SYSTEM CHECK FEATURE - END
    
    return templates.TemplateResponse(request=request, name="index.html")

# SYSTEM CHECK FEATURE - START
@app.get("/system-check", response_class=HTMLResponse)
async def system_check_page(request: Request):
    """Serve the system check page"""
    return templates.TemplateResponse(request=request, name="index.html")
# SYSTEM CHECK FEATURE - END

@app.get("/api/artists")
async def get_artists(library_id: List[str] = Query(None)):
    """Get list of artists from Navidrome"""
    try:
        client = get_navidrome_client()
        artists = await client.get_artists(library_id)
        return artists
    except Exception as e:
        error_msg = str(e)
        # Check if it's an authentication error and return appropriate status code
        if "Invalid username or password" in error_msg or "No authentication method available" in error_msg:
            raise HTTPException(status_code=401, detail=error_msg)
        elif "Network error" in error_msg or "connecting to Navidrome" in error_msg:
            raise HTTPException(status_code=503, detail=f"Cannot connect to Navidrome server: {error_msg}")
        else:
            raise HTTPException(status_code=500, detail=f"Failed to fetch artists: {error_msg}")

@app.get("/api/genres")
async def get_genres(
    library_id: List[str] = Query(None),
    min_song_count: int = Query(0, ge=0),
):
    """Get list of genres from Navidrome, optionally filtered by minimum track count."""
    try:
        client = get_navidrome_client()
        genres = await client.get_genres(library_id)
        if min_song_count > 0:
            genres = [g for g in genres if g.get("songCount", 0) >= min_song_count]
        return genres
    except Exception as e:
        error_msg = str(e)
        # Check if it's an authentication error and return appropriate status code
        if "Invalid username or password" in error_msg or "No authentication method available" in error_msg:
            raise HTTPException(status_code=401, detail=error_msg)
        elif "Network error" in error_msg or "connecting to Navidrome" in error_msg:
            raise HTTPException(status_code=503, detail=f"Cannot connect to Navidrome server: {error_msg}")
        else:
            raise HTTPException(status_code=500, detail=f"Failed to fetch genres: {error_msg}")


@app.get("/api/genres/meta", response_model=MetaGenreResponse)
async def get_meta_genres(
    db: DatabaseManager = Depends(get_db),
    library_id: List[str] = Query(None),
    min_song_count: int = Query(0, ge=0),
):
    """Get distilled meta-genres while preserving raw genre support."""
    try:
        payload = await _build_or_refresh_meta_genres(
            db=db,
            library_ids=library_id,
            min_song_count=min_song_count,
            force=False,
        )
        return payload
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch meta genres: {str(e)}")


@app.post("/api/genres/meta/refresh", response_model=MetaGenreResponse)
async def refresh_meta_genres(
    db: DatabaseManager = Depends(get_db),
    library_id: List[str] = Query(None),
    min_song_count: int = Query(0, ge=0),
):
    """Manually refresh distilled meta-genre cache."""
    try:
        scheduler_logger.info(
            "🧪 Manual meta-genre refresh requested: library_ids=%s, min_song_count=%s",
            library_id,
            min_song_count,
        )
        payload = await _build_or_refresh_meta_genres(
            db=db,
            library_ids=library_id,
            min_song_count=min_song_count,
            force=True,
        )
        await db.set_config("meta_genre_last_refresh_at", datetime.now().isoformat())
        scheduler_logger.info("✅ Manual meta-genre refresh completed for source_key=%s", payload.get("source_key"))
        return payload
    except Exception as e:
        scheduler_logger.error("❌ Manual meta-genre refresh failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to refresh meta genres: {str(e)}")


@app.get("/api/genres/meta/insights", response_model=MetaGenreInsightsResponse)
async def get_meta_genre_insights(
    db: DatabaseManager = Depends(get_db),
    library_id: List[str] = Query(None),
):
    """Return snapshot + cadence insights for meta-genre distillation."""
    settings = await _get_effective_meta_genre_settings(db)
    source_key = _meta_genre_source_key(library_id, int(settings["min_song_count"]))
    snapshot = await db.get_meta_genre_snapshot(source_key)
    last_refresh_at = await db.get_config("meta_genre_last_refresh_at")

    groups = []
    generated_at = None
    source_hash = None
    model_name = None
    raw_genre_count = 0
    stale = True
    diagnostics: Dict[str, Any] = {}
    if snapshot:
        payload = snapshot.get("payload") or {}
        groups = payload.get("groups") or []
        generated_at = payload.get("generated_at") or snapshot.get("generated_at")
        source_hash = snapshot.get("source_hash")
        model_name = snapshot.get("model_name")
        raw_genre_count = int(payload.get("raw_genre_count") or snapshot.get("raw_genre_count") or 0)
        diagnostics = payload.get("diagnostics") or {}
        stale = await db.is_meta_genre_snapshot_stale(source_key, int(settings["cache_hours"]))
        if not diagnostics:
            legacy_total_groups = len(groups)
            legacy_singleton_ratio = (
                sum(1 for group in groups if len(group.get("genres") or []) <= 1) / legacy_total_groups
                if legacy_total_groups else 0.0
            )
            diagnostics = {
                "llm_attempted": model_name is not None,
                "provider_available": model_name is not None,
                "fallback_used": legacy_singleton_ratio >= 0.95 if legacy_total_groups else False,
                "fallback_reason": "legacy_snapshot_without_diagnostics",
                "raw_genre_count": raw_genre_count,
            }

    total_groups = len(groups)
    singleton_groups = sum(1 for group in groups if len(group.get("genres") or []) <= 1)
    singleton_ratio = (singleton_groups / total_groups) if total_groups else 0.0

    next_refresh_at = None
    refresh_frequency = str(settings["refresh_frequency"])
    if refresh_frequency != "none" and last_refresh_at:
        try:
            last = datetime.fromisoformat(last_refresh_at)
            if refresh_frequency == "daily":
                next_refresh_at = (last + timedelta(days=1)).isoformat()
            elif refresh_frequency == "weekly":
                next_refresh_at = (last + timedelta(days=7)).isoformat()
            elif refresh_frequency == "monthly":
                next_refresh_at = (last + timedelta(days=30)).isoformat()
        except ValueError:
            next_refresh_at = None

    return MetaGenreInsightsResponse(
        source_key=source_key,
        generated_at=generated_at,
        last_refresh_at=last_refresh_at,
        next_refresh_at=next_refresh_at,
        raw_genre_count=raw_genre_count,
        source_hash=source_hash,
        model_name=model_name,
        stale=stale,
        total_groups=total_groups,
        singleton_groups=singleton_groups,
        singleton_ratio=singleton_ratio,
        settings=MetaGenreSettingsResponse(
            refresh_frequency=refresh_frequency,
            min_song_count=int(settings["min_song_count"]),
            min_raw_genres=int(settings["min_raw_genres"]),
            cache_hours=int(settings["cache_hours"]),
        ),
        groups=groups,
        diagnostics=diagnostics,
    )


@app.patch("/api/genres/meta/settings", response_model=MetaGenreSettingsResponse)
async def update_meta_genre_settings(
    request: MetaGenreSettingsRequest,
    db: DatabaseManager = Depends(get_db),
):
    """Persist user-tunable meta-genre cadence/threshold settings."""
    return await _set_meta_genre_settings(db, request)


@app.get("/api/music-folders")
async def get_music_folders():
    """Get list of music folders/libraries from Navidrome"""
    try:
        client = get_navidrome_client()
        folders = await client.get_music_folders()
        return folders
    except Exception as e:
        error_msg = str(e)
        # Check if it's an authentication error and return appropriate status code
        if "Invalid username or password" in error_msg or "No authentication method available" in error_msg:
            raise HTTPException(status_code=401, detail=error_msg)
        elif "Network error" in error_msg or "connecting to Navidrome" in error_msg:
            raise HTTPException(status_code=503, detail=f"Cannot connect to Navidrome server: {error_msg}")
        else:
            raise HTTPException(status_code=500, detail=f"Failed to fetch music folders: {error_msg}")


# SYSTEM CHECK FEATURE - START
@app.get("/api/health-check")
async def get_health_check():
    """Get system health check results"""
    global system_check_passed, system_check_results
    
    try:
        # Run fresh health checks
        health_service = HealthCheckService()
        fresh_results = await health_service.run_checks()
        
        # Update app state with fresh results
        system_check_passed = fresh_results.get("all_passed", False)
        system_check_results = fresh_results
        
        # Log the result
        if system_check_passed:
            scheduler_logger.info("✅ System health checks passed via API")
        else:
            scheduler_logger.warning("⚠️ System health checks failed via API")
        
        return fresh_results
        
    except Exception as e:
        scheduler_logger.error(f"❌ Failed to run health checks via API: {e}")
        error_results = {
            "all_passed": False,
            "checks": [{
                "name": "System Check Service",
                "status": "error",
                "message": f"Failed to run health checks: {str(e)}",
                "suggestion": "Check application logs and restart the service"
            }]
        }
        return error_results
# SYSTEM CHECK FEATURE - END


@app.post("/api/create_playlist", response_model=Playlist)
async def create_playlist(
    request: CreatePlaylistRequest,
    db: DatabaseManager = Depends(get_db)
):
    """Create an AI-curated 'This Is' playlist for a single artist"""
    try:
        # Get clients
        nav_client = get_navidrome_client()
        ai_client_instance = get_ai_client()
        
        # Get artist info
        all_artists = await nav_client.get_artists()
        selected_artists = [a for a in all_artists if a["id"] in request.artist_ids]
        
        if not selected_artists:
            raise HTTPException(status_code=404, detail="Artists not found")
        
        # Limit to single artist only - use first artist from the request
        if request.artist_ids:
            first_artist_id = request.artist_ids[0]
            selected_artists = [a for a in all_artists if a["id"] == first_artist_id]
            artist_names = [a["name"] for a in selected_artists]
        else:
            raise HTTPException(status_code=400, detail="At least one artist must be selected")

        # Generate playlist name if not provided - for single artist
        playlist_name = request.playlist_name or f"This Is: {artist_names[0]}"
        
        # Get tracks for only the first artist
        all_tracks = []
        tracks = await nav_client.get_tracks_by_artist(first_artist_id, request.library_ids)
        if tracks:
            all_tracks.extend(tracks)
        
        if not all_tracks:
            raise HTTPException(status_code=404, detail="No tracks found for the selected artists")
        
        # NEW: Apply smart filtering for "This Is" playlists to optimize LLM payload
        library_stats = await nav_client.get_library_stats()
        
        filtered_tracks, filter_metadata = filter_tracks_for_this_is_playlist(
            source_tracks=all_tracks,
            target_playlist_size=request.playlist_length,
            library_stats=library_stats
        )
        
        # Log filtering results for analytics/debugging
        if filter_metadata['filtered']:
            scheduler_logger.info(f"🎯 Smart filtering applied: {filter_metadata['source_count']} → {filter_metadata['sent_count']} tracks (multiplier: {filter_metadata['threshold_multiplier']}x)")
            scheduler_logger.info(f"📊 Score range: {filter_metadata['score_range']['highest']:.1f} - {filter_metadata['score_range']['lowest']:.1f} (cutoff: {filter_metadata['score_range']['cutoff']:.1f})")
        else:
            scheduler_logger.info(f"✅ No filtering needed: {filter_metadata['source_count']} tracks below threshold")
        
        # Use filtered tracks for LLM processing
        tracks_for_llm = filtered_tracks
        
        # Use AI to curate the playlist (always include reasoning for new recipe format)
        curation_result = await ai_client_instance.curate_this_is(
            artist_name=', '.join(artist_names),
            tracks_json=tracks_for_llm,
            num_tracks=request.playlist_length,
            include_reasoning=True
        )
        
        curated_track_ids, reasoning, suggested_tracks = unpack_curation_result(curation_result)

        # Check for validation failures or empty results
        if not curated_track_ids:
            if reasoning and "Playlist generation failed" in reasoning:
                # This is a validation failure - don't create playlist
                scheduler_logger.error(f"❌ Playlist creation aborted: {reasoning}")
                raise HTTPException(status_code=400, detail=f"Playlist generation failed: {reasoning}")
            else:
                # This is an empty result without explanation
                scheduler_logger.error(f"❌ AI curation returned no tracks for {', '.join(artist_names)}")
                raise HTTPException(status_code=500, detail="AI curation failed to return any tracks")

        # Log the AI reasoning for debugging (truncated)
        if reasoning:
            reasoning_preview = reasoning[:200] + "..." if len(reasoning) > 200 else reasoning
            scheduler_logger.info(f"🎵 AI curation applied for {', '.join(artist_names)} (reasoning length: {len(reasoning)} chars): {reasoning_preview}")
        else:
            scheduler_logger.info(f"⚠️ No AI reasoning provided for {', '.join(artist_names)}")

        # Create playlist in Navidrome with AI reasoning as comment
        comment_to_use = _comment_with_playlist_date(reasoning, is_update=False)
        comment_preview = comment_to_use[:200] + "..." if comment_to_use and len(comment_to_use) > 200 else comment_to_use
        scheduler_logger.info(f"💬 Creating playlist with comment (length: {len(comment_to_use) if comment_to_use else 0}): {comment_preview}")

        navidrome_playlist_id = await nav_client.create_playlist(
            name=playlist_name,
            track_ids=curated_track_ids,
            comment=comment_to_use
        )
        
        track_id_to_title = {track["id"]: track["title"] for track in all_tracks}
        track_titles = [track_id_to_title.get(track_id, "Unknown") for track_id in curated_track_ids]

        # Store playlist in local database (using the first artist_id for now)
        playlist = await db.create_playlist(
            artist_id=request.artist_ids[0],
            playlist_name=playlist_name,
            songs=track_titles,
            reasoning=reasoning,
            navidrome_playlist_id=navidrome_playlist_id,
            playlist_length=request.playlist_length,
            library_ids=request.library_ids,
            curation_options={
                "artist_concentration": request.artist_concentration,
                "album_concentration": request.album_concentration,
                "llm_polish": request.llm_polish,
            },
        )

        recommended_missing = []
        added_from_suggestions = 0
        if playlist and suggested_tracks:
            track_titles, recommended_missing, added_from_suggestions = await _apply_missing_recommendations(
                nav_client,
                db,
                playlist.id,
                navidrome_playlist_id,
                suggested_tracks,
                curated_track_ids,
                track_id_to_title,
                request.library_ids,
            )
            if added_from_suggestions:
                await db.update_playlist_songs(playlist.id, track_titles)
        
        # Handle scheduling if not "none" or "never"
        if request.refresh_frequency not in ["none", "never"]:
            next_refresh = calculate_next_refresh(request.refresh_frequency)
            
            # Store the scheduled playlist
            await db.create_scheduled_playlist(
                playlist_type="this_is",
                navidrome_playlist_id=navidrome_playlist_id,
                refresh_frequency=request.refresh_frequency,
                next_refresh=next_refresh
            )
            
            # Schedule the refresh job
            schedule_playlist_refresh()
            scheduler_logger.info(f"📅 Scheduled {request.refresh_frequency} refresh for This Is playlist: {playlist_name}")
        
        # Add Navidrome playlist ID to response
        playlist_dict = playlist.dict() if hasattr(playlist, 'dict') else playlist.__dict__
        playlist_dict["navidrome_playlist_id"] = navidrome_playlist_id
        playlist_dict["refresh_frequency"] = request.refresh_frequency
        playlist_dict["recommended_missing"] = recommended_missing
        playlist_dict["added_from_suggestions"] = added_from_suggestions
        if added_from_suggestions:
            playlist_dict["songs"] = track_titles
        
        if request.refresh_frequency != "none":
            playlist_dict["next_refresh"] = calculate_next_refresh(request.refresh_frequency).isoformat()
        
        return playlist_dict
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create playlist: {str(e)}")

@app.post("/api/create_playlist_with_reasoning")
async def create_playlist_with_reasoning(
    request: CreatePlaylistRequest,
    db: DatabaseManager = Depends(get_db)
):
    """Create an AI-curated 'This Is' playlist with AI reasoning explanation"""
    try:
        # Get clients
        nav_client = get_navidrome_client()
        ai_client_instance = get_ai_client()
        
        # Get artist info - use first artist from the array
        artists = await nav_client.get_artists()
        if not request.artist_ids or len(request.artist_ids) == 0:
            raise HTTPException(status_code=400, detail="At least one artist must be selected")
        first_artist_id = request.artist_ids[0]
        artist = next((a for a in artists if a["id"] == first_artist_id), None)
        
        if not artist:
            raise HTTPException(status_code=404, detail="Artist not found")
        
        artist_name = artist["name"]
        
        # Generate playlist name if not provided
        playlist_name = getattr(request, 'playlist_name', None) or f"This Is: {artist_name}"
        
        # Get tracks for the artist
        tracks = await nav_client.get_tracks_by_artist(first_artist_id)
        
        if not tracks:
            raise HTTPException(status_code=404, detail="No tracks found for this artist")
        
        # Use AI to curate the playlist WITH reasoning
        curated_track_ids, reasoning = await ai_client_instance.curate_this_is(
            artist_name=artist_name,
            tracks_json=tracks,
            num_tracks=20,
            include_reasoning=True
        )

        # Create playlist in Navidrome with AI reasoning as comment
        navidrome_playlist_id = await nav_client.create_playlist(
            name=playlist_name,
            track_ids=curated_track_ids,
            comment=_comment_with_playlist_date(reasoning, is_update=False)
        )
        
        # Get track titles for database storage
        track_titles = []
        track_id_to_title = {track["id"]: track["title"] for track in tracks}
        for track_id in curated_track_ids:
            if track_id in track_id_to_title:
                track_titles.append(track_id_to_title[track_id])
        
        # Store playlist in local database
        playlist = await db.create_playlist(
            artist_id=first_artist_id,
            playlist_name=playlist_name,
            songs=track_titles,
            navidrome_playlist_id=navidrome_playlist_id
        )
        
        # Add Navidrome playlist ID and AI reasoning to response
        playlist_dict = playlist.dict() if hasattr(playlist, 'dict') else playlist.__dict__
        playlist_dict["navidrome_playlist_id"] = navidrome_playlist_id
        playlist_dict["ai_reasoning"] = reasoning
        
        return playlist_dict
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create playlist with reasoning: {str(e)}")

@app.post("/api/create_genre_playlist", response_model=Playlist)
async def create_genre_playlist(
    request: CreateGenrePlaylistRequest,
    db: DatabaseManager = Depends(get_db)
):
    """Create an AI-curated 'Genre Mix' playlist for a specific genre"""
    try:
        # Get clients
        nav_client = get_navidrome_client()
        ai_client_instance = get_ai_client()

        selection_mode = (request.genre_selection_mode or "raw").lower()
        if selection_mode not in {"raw", "meta"}:
            raise HTTPException(status_code=400, detail="genre_selection_mode must be 'raw' or 'meta'")

        selected_genre_label = request.genre
        source_genres: List[str] = []
        if selection_mode == "meta" or request.meta_genre:
            if not request.meta_genre:
                raise HTTPException(status_code=400, detail="meta_genre is required when genre_selection_mode='meta'")
            selected_genre_label = request.meta_genre
            meta_settings = await _get_effective_meta_genre_settings(db)
            min_song_count = int(meta_settings["min_song_count"])
            source_key = _meta_genre_source_key(request.library_ids, min_song_count)
            snapshot = await db.get_meta_genre_snapshot(source_key)
            if not snapshot:
                await _build_or_refresh_meta_genres(
                    db=db,
                    library_ids=request.library_ids,
                    min_song_count=min_song_count,
                    force=True,
                    min_raw_genres_override=int(meta_settings["min_raw_genres"]),
                    cache_hours_override=int(meta_settings["cache_hours"]),
                )
                snapshot = await db.get_meta_genre_snapshot(source_key)
            source_genres = get_genre_distillation_service().resolve_meta_genre(request.meta_genre, snapshot)
            if not source_genres:
                raise HTTPException(status_code=404, detail=f"Meta-genre '{request.meta_genre}' has no mapped genres")
        else:
            if not request.genre:
                raise HTTPException(status_code=400, detail="genre is required when genre_selection_mode='raw'")
            source_genres = [request.genre]

        # Generate playlist name if not provided
        playlist_name = request.playlist_name or f"Genre Mix: {selected_genre_label}"

        # Get tracks for one or many source genres
        all_tracks = []
        seen_track_ids = set()
        for source_genre in source_genres:
            tracks = await nav_client.get_tracks_by_genre(source_genre, request.library_ids)
            for track in tracks:
                track_id = track.get("id")
                if track_id and track_id not in seen_track_ids:
                    seen_track_ids.add(track_id)
                    all_tracks.append(track)
        scheduler_logger.info(
            f"🎵 Found {len(all_tracks)} total tracks for Genre Mix selection "
            f"'{selected_genre_label}' from {len(source_genres)} source genre(s)"
        )

        if not all_tracks:
            raise HTTPException(status_code=404, detail=f"No tracks found for selection: {selected_genre_label}")

        # Assemble a deterministic draft from the full genre slice before any LLM pass.
        library_stats = await nav_client.get_library_stats()
        assembly = assemble_playlist_candidates(
            tracks=all_tracks,
            target_size=request.playlist_length,
            library_stats=library_stats,
            artist_concentration=request.artist_concentration,
            album_concentration=request.album_concentration,
        )
        assembly_metadata = assembly["metadata"]
        await db.record_scoring_run(
            source_key=f"navidrome:{nav_client.base_url}",
            recipe_id=f"genre_mix:{selected_genre_label}",
            scoring_version=SCORING_VERSION,
            params=assembly_metadata,
            scored_tracks=assembly["scored_tracks"],
        )

        # Log filtering results for analytics/debugging
        scheduler_logger.info(
            "🎯 Genre mix heuristic assembly: "
            f"{assembly_metadata['source_count']} source tracks → "
            f"{assembly_metadata['selected_count']} draft tracks "
            f"(artist cap: {assembly_metadata['artist_cap']}, album cap: {assembly_metadata['album_cap']})"
        )

        llm_pool_meta: Dict[str, int] = {}
        tracks_for_llm, llm_pool_meta = build_genre_mix_llm_pool(
            assembly, request.playlist_length
        )
        if llm_pool_meta.get("llm_pool_count", 0) < (
            len(assembly["selected_tracks"]) + len(assembly.get("reserve_tracks") or [])
        ):
            scheduler_logger.info(
                "🎯 Genre mix LLM pool capped: "
                f"{llm_pool_meta.get('llm_seed_count', 0)} seeds + "
                f"{llm_pool_meta.get('llm_reserve_count', 0)} reserves "
                f"(cap {llm_pool_meta.get('llm_pool_cap', 0)})"
            )

        if request.llm_polish:
            # Use AI as a polish/repair pass over the deterministic draft and reserves.
            curation_result = await ai_client_instance.curate_genre_mix(
                genre=selected_genre_label,
                tracks_json=tracks_for_llm,
                num_tracks=request.playlist_length,
                include_reasoning=True,
                variety_context=(
                    f"Heuristic draft uses artist_concentration={request.artist_concentration:.2f}; "
                    "prefer the h=true draft tracks unless replacing one improves variety or flow."
                ),
            )

            curated_track_ids, reasoning, suggested_tracks = unpack_curation_result(curation_result)
        else:
            curated_track_ids = [track["id"] for track in assembly["selected_tracks"]]
            reasoning = (
                "Heuristic curation: selected tracks by local engagement while limiting repeated artists "
                f"to {assembly_metadata['artist_cap']} and repeated albums to {assembly_metadata['album_cap']}."
            )
            suggested_tracks = []

        # Check for validation failures or empty results
        if not curated_track_ids:
            if reasoning and "Playlist generation failed" in reasoning:
                # This is a validation failure - don't create playlist
                scheduler_logger.error(f"❌ Playlist creation aborted: {reasoning}")
                raise HTTPException(status_code=400, detail=f"Playlist generation failed: {reasoning}")
            else:
                # This is an empty result without explanation
                scheduler_logger.error(f"❌ AI curation returned no tracks for {selected_genre_label}")
                raise HTTPException(status_code=500, detail="AI curation failed to return any tracks")

        # Log the AI reasoning for debugging (truncated)
        if reasoning:
            reasoning_preview = reasoning[:200] + "..." if len(reasoning) > 200 else reasoning
            scheduler_logger.info(f"🎵 AI curation applied for {selected_genre_label} (reasoning length: {len(reasoning)} chars): {reasoning_preview}")
        else:
            scheduler_logger.info(f"⚠️ No AI reasoning provided for {selected_genre_label}")

        # Create playlist in Navidrome with AI reasoning as comment
        comment_to_use = _comment_with_playlist_date(reasoning, is_update=False)
        comment_preview = comment_to_use[:200] + "..." if comment_to_use and len(comment_to_use) > 200 else comment_to_use
        scheduler_logger.info(f"💬 Creating playlist with comment (length: {len(comment_to_use) if comment_to_use else 0}): {comment_preview}")

        navidrome_playlist_id = await nav_client.create_playlist(
            name=playlist_name,
            track_ids=curated_track_ids,
            comment=comment_to_use
        )

        track_id_to_title = {track["id"]: track["title"] for track in all_tracks}
        track_titles = [track_id_to_title.get(track_id, "Unknown") for track_id in curated_track_ids]

        # Store playlist in local database (using genre as identifier)
        playlist = await db.create_playlist(
            artist_id=selected_genre_label,
            playlist_name=playlist_name,
            songs=track_titles,
            reasoning=reasoning,
            navidrome_playlist_id=navidrome_playlist_id,
            playlist_length=request.playlist_length,
            library_ids=request.library_ids,
            curation_options={
                "artist_concentration": request.artist_concentration,
                "album_concentration": request.album_concentration,
                "llm_polish": request.llm_polish,
                "genre_selection_mode": selection_mode,
                "genre": request.genre,
                "meta_genre": request.meta_genre,
                "source_genres": source_genres,
            },
        )

        recommended_missing = []
        added_from_suggestions = 0
        if playlist and suggested_tracks:
            track_titles, recommended_missing, added_from_suggestions = await _apply_missing_recommendations(
                nav_client,
                db,
                playlist.id,
                navidrome_playlist_id,
                suggested_tracks,
                curated_track_ids,
                track_id_to_title,
                request.library_ids,
            )
            if added_from_suggestions:
                await db.update_playlist_songs(playlist.id, track_titles)

        # Handle scheduling if not "none" or "never"
        if request.refresh_frequency not in ["none", "never"]:
            next_refresh = calculate_next_refresh(request.refresh_frequency)

            # Store the scheduled playlist
            await db.create_scheduled_playlist(
                playlist_type="genre_mix",
                navidrome_playlist_id=navidrome_playlist_id,
                refresh_frequency=request.refresh_frequency,
                next_refresh=next_refresh
            )

        if playlist:
            playlist_dict = playlist.dict() if hasattr(playlist, "dict") else playlist.__dict__
            playlist_dict["recommended_missing"] = recommended_missing
            playlist_dict["added_from_suggestions"] = added_from_suggestions
            if added_from_suggestions:
                playlist_dict["songs"] = track_titles
            return playlist_dict
        return playlist

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create genre playlist: {str(e)}")

@app.get("/api/rediscover-weekly", response_model=RediscoverWeeklyResponse)
async def get_rediscover_weekly():
    """Generate Re-Discover Weekly playlist based on listening history"""
    try:
        # Get Navidrome client
        nav_client = get_navidrome_client()
        
        # Create RediscoverWeekly instance
        rediscover = RediscoverWeekly(nav_client)
        
        # Generate the playlist with AI curation
        tracks = await rediscover.generate_rediscover_weekly(use_ai=True)
        
        # Extract AI curation info for response
        ai_curated = tracks[0].get("ai_curated", False) if tracks else False
        message = f"Generated Re-Discover Weekly with {len(tracks)} tracks"
        if ai_curated:
            message += " (AI curated)"
        else:
            message += " (algorithmic selection)"
        
        return RediscoverWeeklyResponse(
            tracks=tracks,
            total_tracks=len(tracks),
            message=message
        )
        
    except Exception as e:
        error_msg = str(e)
        if "No listening history found" in error_msg:
            raise HTTPException(status_code=404, detail="No listening history found. Make sure you've played some music in Navidrome.")
        elif "No tracks found for re-discovery" in error_msg:
            raise HTTPException(status_code=404, detail="No tracks found for re-discovery. Try listening to more music first.")
        elif "Invalid username or password" in error_msg or "No authentication method available" in error_msg:
            raise HTTPException(status_code=401, detail=error_msg)
        elif "Network error" in error_msg or "connecting to Navidrome" in error_msg:
            raise HTTPException(status_code=503, detail=f"Cannot connect to Navidrome server: {error_msg}")
        else:
            raise HTTPException(status_code=500, detail=f"Failed to generate Re-Discover Weekly: {error_msg}")

@app.get("/api/rediscover-weekly-v2", response_model=RediscoverWeeklyV2Response)
async def get_rediscover_weekly_v2(library_ids: Optional[List[str]] = Query(None), db: DatabaseManager = Depends(get_db)):
    """Generate Re-Discover Weekly v2.0 playlist using temporal analysis and two-phase AI"""
    try:
        # Get clients
        nav_client = get_navidrome_client()
        ai_client = get_ai_client()

        # Get user and server IDs
        user_id = await db.get_or_create_user_id()
        server_id = nav_client.base_url or "unknown_server"  # Use base URL as server identifier

        # Create ReDiscoverV2Processor instance
        processor = ReDiscoverV2Processor(nav_client, ai_client, db)

        # Generate the playlist
        result = await processor.generate_playlist(user_id, server_id, library_ids)

        return RediscoverWeeklyV2Response(**result)

    except Exception as e:
        error_msg = str(e)
        if "Insufficient listening history" in error_msg:
            raise HTTPException(status_code=404, detail="Insufficient listening history. Star favorites and listen regularly. Check back in 2-3 weeks!")
        elif "Invalid username or password" in error_msg or "No authentication method available" in error_msg:
            raise HTTPException(status_code=401, detail=error_msg)
        elif "Network error" in error_msg or "connecting to Navidrome" in error_msg:
            raise HTTPException(status_code=503, detail=f"Cannot connect to Navidrome server: {error_msg}")
        else:
            raise HTTPException(status_code=500, detail=f"Failed to generate Re-Discover Weekly v2.0: {error_msg}")

@app.post("/api/create-rediscover-playlist-v2")
async def create_rediscover_playlist_v2(
    request: CreateRediscoverPlaylistRequest,
    db: DatabaseManager = Depends(get_db)
):
    """Create a Re-Discover Weekly v2.0 playlist in Navidrome"""
    try:
        scheduler_logger.info(f"🎵 Starting Re-Discover v2.0 playlist creation with length {request.playlist_length}, library_ids: {request.library_ids}")

        # Get clients
        nav_client = get_navidrome_client()
        ai_client = get_ai_client()

        # Get user and server IDs
        user_id = await db.get_or_create_user_id()
        server_id = nav_client.base_url or "unknown_server"

        # Create ReDiscoverV2Processor instance
        processor = ReDiscoverV2Processor(nav_client, ai_client, db)

        # Generate the playlist
        playlist_data = await processor.generate_playlist(user_id, server_id, request.library_ids)
        tracks = playlist_data.get("tracks", [])

        if not tracks:
            scheduler_logger.error("❌ No tracks generated for Re-Discover Weekly v2.0")
            raise HTTPException(status_code=404, detail="No tracks found for Re-Discover Weekly v2.0")

        scheduler_logger.info(f"✅ Generated {len(tracks)} tracks for Re-Discover Weekly v2.0")

        # Extract AI reasoning if available
        ai_reasoning = playlist_data.get("reasoning", "")
        ai_curated = any(track.get("ai_curated", False) for track in tracks)

        # If AI curated, get reasoning from the tracks instead of Phase 1
        if ai_curated:
            track_reasoning = next((track.get("ai_reasoning", "") for track in tracks if track.get("ai_curated", False) and track.get("ai_reasoning")), "")
            if track_reasoning:
                ai_reasoning = track_reasoning

        scheduler_logger.info(f"🎵 AI curated: {ai_curated}, reasoning length: {len(ai_reasoning)}")

        # Log the AI reasoning for debugging (truncated)
        if ai_reasoning and ai_curated:
            reasoning_preview = ai_reasoning[:200] + "..." if len(ai_reasoning) > 200 else ai_reasoning
            scheduler_logger.info(f"🎵 AI curation applied for Re-Discover Weekly v2.0 (reasoning length: {len(ai_reasoning)} chars): {reasoning_preview}")
        else:
            scheduler_logger.info(f"⚠️ Re-Discover Weekly v2.0 used fallback strategy")

        # Create playlist name based on refresh frequency
        frequency_names = {
            "daily": "Re-Discover Daily ✨",
            "weekly": "Re-Discover Weekly ✨",
            "monthly": "Re-Discover Monthly ✨",
            "never": "Re-Discover ✨"
        }
        playlist_name = frequency_names.get(request.refresh_frequency, "Re-Discover Weekly ✨")
        if playlist_data.get("is_fallback"):
            playlist_name += " (Fallback)"
        scheduler_logger.info(f"📝 Creating playlist: {playlist_name}")

        # Extract track IDs
        track_ids = [track["id"] for track in tracks]
        scheduler_logger.info(f"🎵 Track IDs: {track_ids[:5]}... (total: {len(track_ids)})")

        # Create playlist in Navidrome with reasoning as comment
        comment_to_use = _comment_with_playlist_date(
            ai_reasoning if ai_reasoning else f"Theme: {playlist_data.get('theme', 'Mixed')}",
            is_update=False,
        )
        comment_preview = comment_to_use[:200] + "..." if len(comment_to_use) > 200 else comment_to_use
        scheduler_logger.info(f"💬 Creating Re-Discover v2.0 playlist with comment (length: {len(comment_to_use)}): {comment_preview}")

        scheduler_logger.info("🎵 Calling nav_client.create_playlist...")
        navidrome_playlist_id = await nav_client.create_playlist(
            name=playlist_name,
            track_ids=track_ids,
            comment=comment_to_use
        )
        scheduler_logger.info(f"✅ Navidrome playlist created: {navidrome_playlist_id}")

        # Get track titles for database storage
        track_titles = [track.get("title", "Unknown") for track in tracks]
        scheduler_logger.info(f"📊 Storing {len(track_titles)} track titles in database")

        # Store playlist in local database (using a synthetic artist_id for rediscover playlists)
        playlist_record = await db.create_playlist(
            artist_id="rediscover_v2",
            playlist_name=playlist_name,
            songs=track_titles,
            reasoning=ai_reasoning,
            navidrome_playlist_id=navidrome_playlist_id,
            playlist_length=len(tracks),
            library_ids=request.library_ids
        )
        scheduler_logger.info(f"💾 Database playlist created: {playlist_record}")

        suggested_tracks = playlist_data.get("suggested_tracks", [])
        recommended_missing = []
        added_from_suggestions = 0
        if playlist_record and suggested_tracks:
            track_id_to_title = {track["id"]: track.get("title", "Unknown") for track in tracks}
            track_titles, recommended_missing, added_from_suggestions = await _apply_missing_recommendations(
                nav_client,
                db,
                playlist_record.id,
                navidrome_playlist_id,
                suggested_tracks,
                track_ids,
                track_id_to_title,
                request.library_ids,
            )
            if added_from_suggestions:
                await db.update_playlist_songs(playlist_record.id, track_titles)

        # Set up scheduling if requested
        if request.refresh_frequency != "never":
            scheduler_logger.info(f"⏰ Setting up {request.refresh_frequency} refresh schedule")
            scheduled_playlist = await db.create_scheduled_playlist(
                playlist_type="rediscover_weekly_v2",
                navidrome_playlist_id=navidrome_playlist_id,
                refresh_frequency=request.refresh_frequency,
                next_refresh=calculate_next_refresh(request.refresh_frequency)
            )
            scheduler_logger.info(f"✅ Scheduled playlist created: {scheduled_playlist}")
        else:
            scheduler_logger.info("⏰ No scheduling requested (refresh_frequency='never')")

        return {
            "message": f"Re-Discover Weekly v2.0 playlist created successfully with {len(tracks)} tracks",
            "playlist_id": navidrome_playlist_id,
            "track_count": len(track_titles) if added_from_suggestions else len(tracks),
            "theme": playlist_data.get("theme", "Mixed"),
            "mode": playlist_data.get("mode", "Unknown"),
            "is_fallback": playlist_data.get("is_fallback", False),
            "recommended_missing": recommended_missing,
            "added_from_suggestions": added_from_suggestions,
        }

    except HTTPException:
        raise
    except Exception as e:
        scheduler_logger.error(f"❌ Failed to create Re-Discover Weekly v2.0 playlist: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create Re-Discover Weekly v2.0 playlist: {str(e)}")

@app.post("/api/create-rediscover-playlist")
async def create_rediscover_playlist(
    request: CreateRediscoverPlaylistRequest,
    db: DatabaseManager = Depends(get_db)
):
    """Create a Re-Discover Weekly playlist in Navidrome"""
    try:
        scheduler_logger.info(f"🎵 Starting Re-Discover playlist creation with length {request.playlist_length}, library_ids: {request.library_ids}")

        # Get Navidrome client
        nav_client = get_navidrome_client()

        # Create RediscoverWeekly instance
        rediscover = RediscoverWeekly(nav_client)

        # Generate the playlist tracks with user-specified length and AI curation
        scheduler_logger.info("🎵 Generating rediscover tracks...")
        tracks = await rediscover.generate_rediscover_weekly(max_tracks=request.playlist_length, use_ai=True, library_id=request.library_ids[0] if request.library_ids else "", variety_context="")
        scheduler_logger.info(f"🎵 Generated {len(tracks) if tracks else 0} tracks")
        
        if not tracks:
            scheduler_logger.error("❌ No tracks generated for Re-Discover Weekly")
            raise HTTPException(status_code=404, detail="No tracks found for Re-Discover Weekly")

        scheduler_logger.info(f"✅ Generated {len(tracks)} tracks for Re-Discover Weekly")

        # Extract AI reasoning if available
        ai_reasoning = ""
        ai_curated = False
        if tracks:
            first_track = tracks[0]
            ai_reasoning = first_track.get("ai_reasoning", "")
            ai_curated = first_track.get("ai_curated", False)
            scheduler_logger.info(f"🎵 AI curated: {ai_curated}, reasoning length: {len(ai_reasoning)}")
        
        # Log the AI reasoning for debugging (truncated)
        if ai_reasoning and ai_curated:
            reasoning_preview = ai_reasoning[:200] + "..." if len(ai_reasoning) > 200 else ai_reasoning
            scheduler_logger.info(f"🎵 AI curation applied for Re-Discover Weekly (reasoning length: {len(ai_reasoning)} chars): {reasoning_preview}")
        else:
            scheduler_logger.info(f"⚠️ Re-Discover Weekly used algorithmic selection (no AI reasoning)")
        
        # Create playlist name based on frequency
        frequency_names = {
            "daily": "Re-Discover Daily ✨",
            "weekly": "Re-Discover Weekly ✨",
            "monthly": "Re-Discover Monthly ✨",
            "never": "Re-Discover ✨"
        }
        playlist_name = frequency_names.get(request.refresh_frequency, "Re-Discover Weekly ✨")
        scheduler_logger.info(f"📝 Creating playlist: {playlist_name}")

        # Extract track IDs
        track_ids = [track["id"] for track in tracks]
        scheduler_logger.info(f"🎵 Track IDs: {track_ids[:5]}... (total: {len(track_ids)})")

        # Create playlist in Navidrome with AI reasoning as comment if available
        comment_to_use = _comment_with_playlist_date(
            ai_reasoning if (ai_reasoning and ai_curated) else "",
            is_update=False,
        )
        comment_preview = comment_to_use[:200] + "..." if comment_to_use and len(comment_to_use) > 200 else comment_to_use
        scheduler_logger.info(f"💬 Creating Re-Discover playlist with comment (length: {len(comment_to_use) if comment_to_use else 0}): {comment_preview}")

        scheduler_logger.info("🎵 Calling nav_client.create_playlist...")
        navidrome_playlist_id = await nav_client.create_playlist(
            name=playlist_name,
            track_ids=track_ids,
            comment=comment_to_use
        )
        scheduler_logger.info(f"✅ Navidrome playlist created: {navidrome_playlist_id}")
        
        # Get track titles for database storage
        track_titles = [track["title"] for track in tracks]
        scheduler_logger.info(f"📊 Storing {len(track_titles)} track titles in database")

        # Store playlist in local database (using a synthetic artist_id for rediscover playlists)
        scheduler_logger.info("💾 Creating playlist in database...")
        playlist = await db.create_playlist(
            artist_id="rediscover",
            playlist_name=playlist_name,
            songs=track_titles,
            reasoning=ai_reasoning if ai_curated else "Algorithmic selection",
            navidrome_playlist_id=navidrome_playlist_id,
            playlist_length=request.playlist_length
        )
        scheduler_logger.info(f"✅ Database playlist created: {playlist}")
        
        # Handle scheduling if not "never"
        if request.refresh_frequency != "never":
            next_refresh = calculate_next_refresh(request.refresh_frequency)
            
            # Store the scheduled playlist
            await db.create_scheduled_playlist(
                playlist_type="rediscover",
                navidrome_playlist_id=navidrome_playlist_id,
                refresh_frequency=request.refresh_frequency,
                next_refresh=next_refresh
            )
            
            # Schedule the refresh job
            schedule_playlist_refresh()
            scheduler_logger.info(f"📅 Scheduled {request.refresh_frequency} refresh for playlist: {playlist_name}")
        else:
            scheduler_logger.info(f"📅 No scheduling for playlist: {playlist_name} (refresh frequency: never)")
        
        # Add Navidrome playlist ID to response
        playlist_dict = playlist.dict() if hasattr(playlist, 'dict') else playlist.__dict__
        playlist_dict["navidrome_playlist_id"] = navidrome_playlist_id
        playlist_dict["tracks"] = tracks
        playlist_dict["refresh_frequency"] = request.refresh_frequency
        playlist_dict["next_refresh"] = calculate_next_refresh(request.refresh_frequency).isoformat()
        
        return playlist_dict
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create Re-Discover Weekly playlist: {str(e)}")

def calculate_next_refresh(frequency: str) -> datetime:
    """Calculate the next refresh time based on frequency"""
    now = datetime.now()
    if frequency == "daily":
        # Next day at 1:00 AM
        next_day = now + timedelta(days=1)
        return next_day.replace(hour=1, minute=0, second=0, microsecond=0)
    elif frequency == "weekly":
        # Next Monday at 1:00 AM
        days_until_monday = (7 - now.weekday()) % 7
        if days_until_monday == 0 and now.hour >= 1:
            days_until_monday = 7  # If it's Monday after 1 AM, go to next Monday
        next_monday = now + timedelta(days=days_until_monday)
        return next_monday.replace(hour=1, minute=0, second=0, microsecond=0)
    elif frequency == "monthly":
        # 1st of next month at 1:00 AM
        if now.month == 12:
            next_month = now.replace(year=now.year + 1, month=1, day=1, hour=1, minute=0, second=0, microsecond=0)
        else:
            next_month = now.replace(month=now.month + 1, day=1, hour=1, minute=0, second=0, microsecond=0)
        return next_month
    else:
        return now  # Fallback

def normalize_refresh_frequency(frequency: Optional[str]) -> str:
    """Normalize UI refresh values to scheduler values."""
    value = (frequency or "none").lower()
    if value in ("never", "manual"):
        return "none"
    if value not in ("none", "daily", "weekly", "monthly"):
        raise ValueError("refresh_frequency must be one of none, daily, weekly, monthly")
    return value

def infer_playlist_type(playlist: Dict[str, Any]) -> str:
    """Infer playlist type for rows that do not currently have a schedule."""
    existing_type = playlist.get("playlist_type")
    if existing_type:
        return existing_type
    artist_id = playlist.get("artist_id")
    playlist_name = playlist.get("playlist_name") or ""
    if artist_id in ("rediscover", "rediscover_v2"):
        return "rediscover"
    if playlist_name.startswith("Genre Mix:"):
        return "genre_mix"
    return "this_is"

def scheduled_from_playlist(playlist: Dict[str, Any], refresh_frequency: Optional[str] = None) -> ScheduledPlaylist:
    """Create a ScheduledPlaylist-like object for immediate refresh calls."""
    frequency = normalize_refresh_frequency(refresh_frequency or playlist.get("refresh_frequency") or "none")
    return ScheduledPlaylist(
        id=playlist.get("schedule_id") or 0,
        playlist_type=infer_playlist_type(playlist),
        navidrome_playlist_id=playlist["navidrome_playlist_id"],
        refresh_frequency=frequency,
        next_refresh=playlist.get("next_refresh") or datetime.now().isoformat(),
        created_at=playlist.get("created_at") or datetime.now().isoformat(),
        updated_at=playlist.get("updated_at") or datetime.now().isoformat(),
    )

async def refresh_one_playlist_now(playlist: Dict[str, Any], db: DatabaseManager):
    """Refresh one playlist using the same handlers as the scheduler."""
    scheduled_playlist = scheduled_from_playlist(playlist)
    if scheduled_playlist.playlist_type in ("rediscover", "rediscover_weekly_v2"):
        await refresh_rediscover_playlist(scheduled_playlist, db)
    elif scheduled_playlist.playlist_type == "this_is":
        await refresh_this_is_playlist(scheduled_playlist, db)
    elif scheduled_playlist.playlist_type == "genre_mix":
        await refresh_genre_mix_playlist(scheduled_playlist, db)
    else:
        raise ValueError(f"Unsupported playlist type: {scheduled_playlist.playlist_type}")

def schedule_playlist_refresh():
    """Schedule the playlist refresh job to run every 12 hours"""
    if not scheduler.get_job('playlist_refresh'):
        scheduler.add_job(
            refresh_scheduled_playlists,
            'cron',
            hour='1,13',  # Run at 1 AM and 1 PM
            minute=1,     # Run at 1 minute past (1:01 AM and 1:01 PM)
            id='playlist_refresh',
            replace_existing=True
        )
        scheduler_logger.info("🔄 Playlist refresh job scheduled to run every 12 hours (1:01 AM and 1:01 PM)")


async def maybe_refresh_meta_genres(db: DatabaseManager):
    """Periodically refresh distilled meta-genre cache based on configured cadence."""
    settings = await _get_effective_meta_genre_settings(db)
    frequency = str(settings["refresh_frequency"])
    if frequency == "none":
        return

    last_refreshed = await db.get_config("meta_genre_last_refresh_at")
    if not _meta_refresh_due(last_refreshed, frequency):
        return

    min_song_count = int(settings["min_song_count"])
    try:
        await _build_or_refresh_meta_genres(
            db=db,
            library_ids=None,
            min_song_count=min_song_count,
            force=True,
            min_raw_genres_override=int(settings["min_raw_genres"]),
            cache_hours_override=int(settings["cache_hours"]),
        )
        await db.set_config("meta_genre_last_refresh_at", datetime.now().isoformat())
        scheduler_logger.info("✅ Refreshed meta-genre distillation cache")
    except Exception as exc:
        scheduler_logger.warning(f"⚠️ Meta-genre distillation refresh skipped due to error: {exc}")


async def refresh_scheduled_playlists():
    """Check for and refresh scheduled playlists that are due"""
    try:
        current_time = datetime.now()
        
        # Only log heartbeat in DEBUG mode, always log when tasks are found
        if LOG_LEVEL == "DEBUG":
            scheduler_logger.debug(f"🔄 Scheduler auto-run initiated at {current_time.strftime('%H:%M:%S')}")
        
        if LOG_LEVEL == "DEBUG":
            scheduler_logger.debug("🔍 Checking for playlists due for refresh...")
        else:
            scheduler_logger.info("🔍 Checking for playlists due for refresh...")
        
        # Get database path from environment variable with smart defaults
        # Docker: /app/data/magiclists.db (set in docker-compose.yml)
        # Standalone: ./magiclists.db (current directory)
        default_path = "/app/data/magiclists.db" if os.path.exists("/app/data") else "./magiclists.db"
        db_path = os.getenv("DATABASE_PATH", default_path)
        db = DatabaseManager(db_path)
        current_time = datetime.now()
        await maybe_refresh_meta_genres(db)
        
        # Get playlists due for refresh (including 7-day catch-up window)
        scheduled_playlists = await db.get_scheduled_playlists_due(current_time, grace_hours=168)
        
        if not scheduled_playlists:
            if LOG_LEVEL == "DEBUG":
                scheduler_logger.debug("✅ No playlists due for refresh at this time")
            return
        
        # Group by navidrome_playlist_id to prevent duplicate processing
        # Only process the most recent overdue refresh for each playlist
        unique_playlists = {}
        for playlist in scheduled_playlists:
            playlist_id = playlist.navidrome_playlist_id
            if playlist_id not in unique_playlists:
                unique_playlists[playlist_id] = playlist
            else:
                # Keep the more recent one (closer to current time)
                existing = datetime.fromisoformat(unique_playlists[playlist_id].next_refresh)
                current = datetime.fromisoformat(playlist.next_refresh)
                if current > existing:
                    unique_playlists[playlist_id] = playlist
        
        final_playlists = list(unique_playlists.values())
        
        scheduler_logger.info(f"📋 Found {len(final_playlists)} playlist(s) due for refresh (deduplicated from {len(scheduled_playlists)} total)")
        
        for scheduled_playlist in final_playlists:
            # Check if this is a catch-up refresh
            scheduled_time = datetime.fromisoformat(scheduled_playlist.next_refresh)
            if scheduled_time < current_time:
                overdue_hours = (current_time - scheduled_time).total_seconds() / 3600
                scheduler_logger.info(f"🕐 Catching up on overdue playlist {scheduled_playlist.navidrome_playlist_id} (missed by {overdue_hours:.1f} hours)")
            
            if scheduled_playlist.playlist_type in ("rediscover", "rediscover_weekly_v2"):
                await refresh_rediscover_playlist(scheduled_playlist, db)
            elif scheduled_playlist.playlist_type == "this_is":
                await refresh_this_is_playlist(scheduled_playlist, db)
            elif scheduled_playlist.playlist_type == "genre_mix":
                await refresh_genre_mix_playlist(scheduled_playlist, db)
            else:
                scheduler_logger.warning(
                    f"⚠️ Unsupported scheduled playlist type '{scheduled_playlist.playlist_type}' "
                    f"for playlist {scheduled_playlist.navidrome_playlist_id}"
                )
                
    except Exception as e:
        scheduler_logger.error(f"❌ Error checking scheduled playlists: {e}")

async def refresh_rediscover_playlist(scheduled_playlist, db: DatabaseManager):
    """Refresh a specific Re-Discover Weekly playlist"""
    try:
        scheduler_logger.info(f"🔄 Starting refresh for playlist ID: {scheduled_playlist.navidrome_playlist_id} (frequency: {scheduled_playlist.refresh_frequency})")
        
        # Get clients
        nav_client = get_navidrome_client()
        
        # Get original playlist to find user's preferred length
        playlists = await db.get_all_playlists_with_schedule_info()
        original_playlist = next((p for p in playlists if p.get("navidrome_playlist_id") == scheduled_playlist.navidrome_playlist_id), None)
        
        if not original_playlist:
            scheduler_logger.error(f"❌ Could not find original playlist data for {scheduled_playlist.navidrome_playlist_id}")
            return
        
        # Get original playlist length (MUST respect user's choice)
        original_length = original_playlist.get("playlist_length", 20)
        scheduler_logger.info(f"🎯 Using original playlist length: {original_length}")
        
        # Get previous playlist songs for variety context
        previous_songs = original_playlist.get("songs", [])[:10]
        variety_instruction = f"REFRESH CHALLENGE: The current playlist opens with these tracks in this order: {', '.join(previous_songs[:5])}. Your goal is to create a FRESH arrangement that tells a different musical story. You may include some of the same excellent tracks if they're rediscovery-worthy, but avoid replicating the same opening sequence or overall flow. Think creatively about re-ordering, substituting, or finding better transitions to ensure a genuinely refreshed listening experience." if previous_songs else ""
        
        # Get AI client for v2.0 processor
        ai_client = get_ai_client()

        # Get user and server IDs for v2.0 processor
        user_id = await db.get_or_create_user_id()
        server_id = nav_client.base_url or "unknown_server"

        # Create ReDiscoverV2Processor instance (improved fallback handling)
        processor = ReDiscoverV2Processor(nav_client, ai_client, db)

        # Prepare library IDs for v2.0 processor
        library_ids = [scheduled_playlist.library_id] if hasattr(scheduled_playlist, 'library_id') and scheduled_playlist.library_id else None

        # Log refresh context for debugging
        scheduler_logger.info(f"🔄 Re-Discover v2.0 refresh context - Previous tracks: {len(previous_songs)}, Library IDs: {library_ids}")

        # Generate new tracks using v2.0 processor with improved fallback handling
        result = await processor.generate_playlist(user_id, server_id, library_ids)

        # Extract tracks from v2.0 result format
        tracks = result.get("tracks", [])

        # Ensure tracks have the expected format for the rest of the refresh logic
        # The v2.0 tracks should already have ai_curated and ai_reasoning fields
        
        # The rediscover.generate_rediscover_weekly() method now uses the new recipe system internally
        
        if tracks:
            scheduler_logger.info(f"🎵 Generated {len(tracks)} new tracks for refresh")
            
            # VALIDATE: Ensure we got the expected number of tracks
            if len(tracks) != original_length:
                scheduler_logger.warning(f"⚠️ Generated {len(tracks)} tracks but user requested {original_length}")
            else:
                scheduler_logger.info(f"✅ Generated exact number of requested tracks: {len(tracks)}")
            
            # Extract AI reasoning if available
            ai_reasoning = ""
            ai_curated = False
            if tracks:
                first_track = tracks[0]
                ai_reasoning = first_track.get("ai_reasoning", "")
                ai_curated = first_track.get("ai_curated", False)
            
            # Log the AI reasoning for scheduled refresh (truncated)
            if ai_reasoning and ai_curated:
                reasoning_preview = ai_reasoning[:200] + "..." if len(ai_reasoning) > 200 else ai_reasoning
                scheduler_logger.info(f"🎵 AI curation applied for scheduled Re-Discover refresh (reasoning length: {len(ai_reasoning)} chars): {reasoning_preview}")
            else:
                scheduler_logger.info(f"⚠️ Scheduled Re-Discover refresh used algorithmic selection")
            
            # Update the existing playlist in Navidrome with reasoning
            track_ids = [track["id"] for track in tracks]
            comment_to_use = _comment_with_playlist_date(
                ai_reasoning if (ai_reasoning and ai_curated) else "Re-Discover Weekly v2.0 - Automatically refreshed",
                is_update=True,
            )
            await nav_client.update_playlist(
                playlist_id=scheduled_playlist.navidrome_playlist_id,
                track_ids=track_ids,
                comment=comment_to_use
            )
            
            # Update the local database with new songs and reasoning
            track_titles = [track["title"] for track in tracks]
            reasoning_to_store = ai_reasoning if ai_curated else "Algorithmic selection"
            await db.update_playlist_content(
                navidrome_playlist_id=scheduled_playlist.navidrome_playlist_id,
                songs=track_titles,
                reasoning=reasoning_to_store
            )
            
            # Calculate next refresh time
            next_refresh = calculate_next_refresh(scheduled_playlist.refresh_frequency)
            
            # Update the scheduled playlist record
            await db.update_scheduled_playlist_next_refresh(
                scheduled_playlist.id, 
                next_refresh
            )
            
            scheduler_logger.info(f"✅ Successfully refreshed playlist {scheduled_playlist.navidrome_playlist_id}. Next refresh: {next_refresh.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            scheduler_logger.warning(f"⚠️ No tracks generated for playlist {scheduled_playlist.navidrome_playlist_id}")
        
    except Exception as e:
        scheduler_logger.error(f"❌ Error refreshing playlist {scheduled_playlist.navidrome_playlist_id}: {e}")

async def refresh_this_is_playlist(scheduled_playlist, db: DatabaseManager):
    """Refresh a specific This Is playlist"""
    try:
        scheduler_logger.info(f"🔄 Starting refresh for This Is playlist ID: {scheduled_playlist.navidrome_playlist_id} (frequency: {scheduled_playlist.refresh_frequency})")
        
        # Get clients
        nav_client = get_navidrome_client()
        ai_client_instance = get_ai_client()
        
        # Find the original playlist to get artist info
        playlists = await db.get_all_playlists_with_schedule_info()
        original_playlist = next((p for p in playlists if p.get("navidrome_playlist_id") == scheduled_playlist.navidrome_playlist_id), None)
        
        if not original_playlist:
            scheduler_logger.error(f"❌ Could not find original playlist data for {scheduled_playlist.navidrome_playlist_id}")
            return
        
        # Get artist IDs from the original playlist (we'll need to store this better in future)
        # For now, we'll use the artist_id field, but this limits us to single artists for refresh
        artist_id = original_playlist["artist_id"]
        
        # Get all artists to find the name
        all_artists = await nav_client.get_artists()
        artist = next((a for a in all_artists if a["id"] == artist_id), None)
        
        if not artist:
            scheduler_logger.error(f"❌ Could not find artist data for ID: {artist_id}")
            return
        
        artist_name = artist["name"]
        
        # FRESH DATA: Re-fetch ALL tracks for the artist (gets latest play counts, dates)
        tracks = await nav_client.get_tracks_by_artist(artist_id)
        
        if tracks:
            scheduler_logger.info(f"🎵 Found {len(tracks)} tracks for artist: {artist_name} (fresh data)")
            
            # ENFORCE original playlist length (MUST respect user's choice)
            original_length = original_playlist.get("playlist_length", 25)
            scheduler_logger.info(f"🎯 ENFORCING original playlist length: {original_length}")
            
            # Check if we have enough tracks
            if len(tracks) < original_length:
                scheduler_logger.warning(f"⚠️ Artist only has {len(tracks)} tracks, but user requested {original_length}. Using all available tracks.")
                original_length = len(tracks)
            
            # Get previous playlist songs for STRONG variety enforcement
            previous_songs = original_playlist.get("songs", [])
            variety_instruction = f"REFRESH CONSTRAINT: This is a REFRESH, not a copy. Previous playlist had these tracks: {', '.join(previous_songs[:10])}. Create a completely different track selection and arrangement. Prioritize tracks NOT in the previous list. Tell a fresh musical story. Avoid identical opening sequences." if previous_songs else "Create a fresh, engaging playlist arrangement."
            
            # Prepare tracks with variety instruction - use a more direct approach
            tracks_for_ai = tracks.copy()
            
            # Use AI to curate a FRESH playlist with STRONG variety enforcement
            curation_result = await ai_client_instance.curate_this_is(
                artist_name=artist_name,
                tracks_json=tracks_for_ai,
                num_tracks=original_length,
                include_reasoning=True,
                variety_context=variety_instruction
            )
            
            # Handle both old and new return formats
            if isinstance(curation_result, tuple):
                curated_track_ids, reasoning = curation_result
            else:
                curated_track_ids = curation_result
                reasoning = ""
            
            if curated_track_ids:
                # VALIDATE: Ensure we got the right number of tracks
                if len(curated_track_ids) < original_length and len(tracks) >= original_length:
                    scheduler_logger.warning(f"⚠️ AI returned only {len(curated_track_ids)} tracks but user requested {original_length}. Using fallback to fill gap.")
                    # Fill the gap with remaining tracks
                    used_ids = set(curated_track_ids)
                    remaining_tracks = [t for t in tracks if t["id"] not in used_ids]
                    additional_needed = original_length - len(curated_track_ids)
                    additional_tracks = remaining_tracks[:additional_needed]
                    curated_track_ids.extend([t["id"] for t in additional_tracks])
                
                scheduler_logger.info(f"🎯 Final track count: {len(curated_track_ids)} (requested: {original_length})")
                
                # Update the existing playlist in Navidrome with new reasoning
                await nav_client.update_playlist(
                    playlist_id=scheduled_playlist.navidrome_playlist_id,
                    track_ids=curated_track_ids,
                    comment=_comment_with_playlist_date(reasoning, is_update=True)
                )
                
                # Update the local database with new songs and reasoning
                track_titles = []
                track_id_to_title = {track["id"]: track["title"] for track in tracks}
                for track_id in curated_track_ids:
                    if track_id in track_id_to_title:
                        track_titles.append(track_id_to_title[track_id])
                
                await db.update_playlist_content(
                    navidrome_playlist_id=scheduled_playlist.navidrome_playlist_id,
                    songs=track_titles,
                    reasoning=reasoning
                )
                
                # Calculate next refresh time
                next_refresh = calculate_next_refresh(scheduled_playlist.refresh_frequency)
                
                # Update the scheduled playlist record
                await db.update_scheduled_playlist_next_refresh(
                    scheduled_playlist.id, 
                    next_refresh
                )
                
                scheduler_logger.info(f"✅ Successfully refreshed This Is playlist {scheduled_playlist.navidrome_playlist_id}. Next refresh: {next_refresh.strftime('%Y-%m-%d %H:%M:%S')}")
            else:
                scheduler_logger.warning(f"⚠️ No curated tracks generated for This Is playlist {scheduled_playlist.navidrome_playlist_id}")
        else:
            scheduler_logger.warning(f"⚠️ No tracks found for artist {artist_name} in playlist {scheduled_playlist.navidrome_playlist_id}")
        
    except Exception as e:
        scheduler_logger.error(f"❌ Error refreshing This Is playlist {scheduled_playlist.navidrome_playlist_id}: {e}")

async def refresh_genre_mix_playlist(scheduled_playlist, db: DatabaseManager):
    """Refresh a specific Genre Mix playlist."""
    try:
        scheduler_logger.info(f"🔄 Starting refresh for Genre Mix playlist ID: {scheduled_playlist.navidrome_playlist_id} (frequency: {scheduled_playlist.refresh_frequency})")

        nav_client = get_navidrome_client()

        playlists = await db.get_all_playlists_with_schedule_info()
        original_playlist = next((p for p in playlists if p.get("navidrome_playlist_id") == scheduled_playlist.navidrome_playlist_id), None)

        if not original_playlist:
            scheduler_logger.error(f"❌ Could not find original playlist data for {scheduled_playlist.navidrome_playlist_id}")
            return

        genre = original_playlist["artist_id"]
        original_length = original_playlist.get("playlist_length", 25)
        library_ids = original_playlist.get("library_ids") or None
        curation_options = original_playlist.get("curation_options") or {}
        artist_concentration = float(curation_options.get("artist_concentration", 0.35))
        album_concentration = float(curation_options.get("album_concentration", 0.25))
        selection_mode = str(curation_options.get("genre_selection_mode", "raw")).lower()
        source_genres = curation_options.get("source_genres") or ([curation_options.get("genre")] if curation_options.get("genre") else [genre])
        source_genres = [str(g) for g in source_genres if str(g).strip()]
        selected_label = curation_options.get("meta_genre") if selection_mode == "meta" else (curation_options.get("genre") or genre)
        selected_label = selected_label or genre

        scheduler_logger.info(f"🎯 Refreshing Genre Mix '{selected_label}' with original length: {original_length}")

        all_tracks = []
        seen_track_ids = set()
        for source_genre in source_genres:
            tracks = await nav_client.get_tracks_by_genre(source_genre, library_ids)
            for track in tracks:
                track_id = track.get("id")
                if track_id and track_id not in seen_track_ids:
                    seen_track_ids.add(track_id)
                    all_tracks.append(track)
        scheduler_logger.info(
            f"🎵 Found {len(all_tracks)} total tracks for Genre Mix selection "
            f"'{selected_label}' from {len(source_genres)} source genre(s)"
        )

        if not all_tracks:
            scheduler_logger.warning(f"⚠️ No tracks found for selection {selected_label} in playlist {scheduled_playlist.navidrome_playlist_id}")
            return

        if len(all_tracks) < original_length:
            scheduler_logger.warning(f"⚠️ Genre only has {len(all_tracks)} tracks, but user requested {original_length}. Using all available tracks.")
            original_length = len(all_tracks)

        library_stats = await nav_client.get_library_stats()
        assembly = assemble_playlist_candidates(
            tracks=all_tracks,
            target_size=original_length,
            library_stats=library_stats,
            artist_concentration=artist_concentration,
            album_concentration=album_concentration,
        )
        assembly_metadata = assembly["metadata"]
        await db.record_scoring_run(
            source_key=f"navidrome:{nav_client.base_url}",
            recipe_id=f"genre_mix:{selected_label}",
            scoring_version=SCORING_VERSION,
            params=assembly_metadata,
            scored_tracks=assembly["scored_tracks"],
        )

        scheduler_logger.info(
            "🎯 Genre mix heuristic assembly: "
            f"{assembly_metadata['source_count']} source tracks → "
            f"{assembly_metadata['selected_count']} draft tracks "
            f"(artist cap: {assembly_metadata['artist_cap']}, album cap: {assembly_metadata['album_cap']})"
        )

        # Scheduled refreshes stay heuristic-only; LLM polish runs on create when opted in.
        scheduler_logger.info(
            "🎯 Genre mix scheduled refresh: heuristic-only (skipping LLM to save API quota)"
        )
        curated_track_ids = [track["id"] for track in assembly["selected_tracks"]]
        reasoning = (
            "Scheduled refresh: heuristic curation from engagement scoring with artist/album caps "
            f"(artist cap {assembly_metadata['artist_cap']}, album cap {assembly_metadata['album_cap']})."
        )

        if curated_track_ids:
            if len(curated_track_ids) < original_length and len(all_tracks) >= original_length:
                scheduler_logger.warning(
                    f"⚠️ Heuristic selection returned only {len(curated_track_ids)} tracks "
                    f"but user requested {original_length}. Filling from library."
                )
                used_ids = set(curated_track_ids)
                remaining_tracks = [track for track in all_tracks if track["id"] not in used_ids]
                additional_needed = original_length - len(curated_track_ids)
                curated_track_ids.extend([track["id"] for track in remaining_tracks[:additional_needed]])

            scheduler_logger.info(f"🎯 Final Genre Mix track count: {len(curated_track_ids)} (requested: {original_length})")

            await nav_client.update_playlist(
                playlist_id=scheduled_playlist.navidrome_playlist_id,
                track_ids=curated_track_ids,
                comment=_comment_with_playlist_date(reasoning, is_update=True),
            )

            track_id_to_title = {track["id"]: track["title"] for track in all_tracks}
            track_titles = [
                track_id_to_title.get(track_id, "Unknown")
                for track_id in curated_track_ids
            ]

            await db.update_playlist_content(
                navidrome_playlist_id=scheduled_playlist.navidrome_playlist_id,
                songs=track_titles,
                reasoning=reasoning,
            )

            next_refresh = calculate_next_refresh(scheduled_playlist.refresh_frequency)
            await db.update_scheduled_playlist_next_refresh(
                scheduled_playlist.id,
                next_refresh,
            )

            scheduler_logger.info(f"✅ Successfully refreshed Genre Mix playlist {scheduled_playlist.navidrome_playlist_id}. Next refresh: {next_refresh.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            scheduler_logger.warning(f"⚠️ No curated tracks generated for Genre Mix playlist {scheduled_playlist.navidrome_playlist_id}")

    except Exception as e:
        scheduler_logger.error(f"❌ Error refreshing Genre Mix playlist {scheduled_playlist.navidrome_playlist_id}: {e}")

@app.get("/api/playlists")
async def get_all_playlists(db: DatabaseManager = Depends(get_db)):
    """Get all playlists with scheduling information"""
    try:
        playlists = await db.get_all_playlists_with_schedule_info()
        # Add track count to each playlist
        for playlist in playlists:
            songs = playlist.get("songs", [])
            playlist["track_count"] = len(songs) if isinstance(songs, list) else 0
        return playlists
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch playlists: {str(e)}")


@app.patch("/api/playlists/{playlist_id}/settings")
async def update_playlist_settings(
    playlist_id: int,
    request: PlaylistSettingsRequest,
    db: DatabaseManager = Depends(get_db),
):
    """Update editable playlist settings and schedule metadata."""
    if request.playlist_length < 1 or request.playlist_length > 500:
        raise HTTPException(status_code=400, detail="playlist_length must be between 1 and 500")

    try:
        refresh_frequency = normalize_refresh_frequency(request.refresh_frequency)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    playlist = await db.get_playlist_by_id_with_schedule_info(playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    navidrome_playlist_id = playlist.get("navidrome_playlist_id")
    if not navidrome_playlist_id:
        raise HTTPException(status_code=400, detail="Playlist is missing Navidrome playlist ID")

    await db.update_playlist_settings(playlist_id, request.playlist_length)

    if refresh_frequency == "none":
        await db.delete_scheduled_playlist_by_navidrome_id(navidrome_playlist_id)
    else:
        await db.upsert_scheduled_playlist(
            playlist_type=infer_playlist_type(playlist),
            navidrome_playlist_id=navidrome_playlist_id,
            refresh_frequency=refresh_frequency,
            next_refresh=calculate_next_refresh(refresh_frequency),
        )
        schedule_playlist_refresh()

    updated = await db.get_playlist_by_id_with_schedule_info(playlist_id)
    if updated:
        songs = updated.get("songs", [])
        updated["track_count"] = len(songs) if isinstance(songs, list) else 0
    return updated


@app.post("/api/playlists/{playlist_id}/refresh")
async def refresh_playlist_now(playlist_id: int, db: DatabaseManager = Depends(get_db)):
    """Refresh one playlist immediately."""
    playlist = await db.get_playlist_by_id_with_schedule_info(playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    if not playlist.get("navidrome_playlist_id"):
        raise HTTPException(status_code=400, detail="Playlist is missing Navidrome playlist ID")

    try:
        await refresh_one_playlist_now(playlist, db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        scheduler_logger.error(f"❌ Manual playlist refresh failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to refresh playlist: {exc}")

    updated = await db.get_playlist_by_id_with_schedule_info(playlist_id)
    if updated:
        songs = updated.get("songs", [])
        updated["track_count"] = len(songs) if isinstance(songs, list) else 0
    return updated


@app.get("/api/lidarr/status")
async def get_lidarr_status(db: DatabaseManager = Depends(get_db)):
    """Return Lidarr integration configuration and reachability."""
    service = get_lidarr_service(db)
    return await service.get_status()


@app.get("/api/lidarr/lookup")
async def lidarr_lookup(
    type: str = Query(..., pattern="^(artist|album)$"),
    term: str = Query(..., min_length=1),
    artist: Optional[str] = Query(None),
    db: DatabaseManager = Depends(get_db),
):
    """Preview Lidarr lookup candidates for disambiguation."""
    if not lidarr_integration_enabled() or not lidarr_configured():
        raise HTTPException(status_code=400, detail="Lidarr integration is not enabled or configured")
    try:
        service = get_lidarr_service(db)
        return await service.lookup(type, term, artist=artist)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Lidarr lookup failed: {exc}")


@app.post("/api/playlists/{playlist_id}/missing/lidarr")
async def add_missing_to_lidarr(
    playlist_id: int,
    request: LidarrAddRequest,
    db: DatabaseManager = Depends(get_db),
):
    """Add one recommended-missing item to Lidarr as artist or album."""
    if not lidarr_integration_enabled() or not lidarr_configured():
        raise HTTPException(status_code=400, detail="Lidarr integration is not enabled or configured")

    entries = await db.get_recommended_missing(playlist_id)
    if not entries:
        raise HTTPException(status_code=404, detail="Playlist has no missing recommendations")
    if request.index < 0 or request.index >= len(entries):
        raise HTTPException(status_code=400, detail="Invalid missing recommendation index")

    suggestion = entries[request.index]
    service = get_lidarr_service(db)

    try:
        result = await service.add_missing_item(
            suggestion=suggestion,
            mode=request.mode,
            foreign_artist_id=request.foreign_artist_id,
            foreign_album_id=request.foreign_album_id,
        )
    except Exception as exc:
        scheduler_logger.error(f"Lidarr add failed: {exc}")
        raise HTTPException(status_code=502, detail=f"Lidarr add failed: {exc}")

    if result.get("status") == "ambiguous":
        return result

    if result.get("status") in ("added_artist", "added_album", "already_exists"):
        await db.update_recommended_missing_entry(
            playlist_id,
            request.index,
            {"lidarr": result},
        )

    return {
        **result,
        "index": request.index,
        "playlist_id": playlist_id,
    }


@app.post("/api/playlists/{playlist_id}/missing/lidarr/bulk")
async def add_all_missing_to_lidarr(
    playlist_id: int,
    request: LidarrBulkAddRequest,
    db: DatabaseManager = Depends(get_db),
):
    """Add all recommended-missing items to Lidarr with runtime options."""
    if not lidarr_integration_enabled() or not lidarr_configured():
        raise HTTPException(status_code=400, detail="Lidarr integration is not enabled or configured")

    entries = await db.get_recommended_missing(playlist_id)
    if not entries:
        raise HTTPException(status_code=404, detail="Playlist has no missing recommendations")

    service = get_lidarr_service(db)
    try:
        result = await service.add_missing_items_bulk(
            suggestions=entries,
            search=request.search,
            monitor_only_target_album=request.monitor_only_target_album,
            skip_ambiguous=request.skip_ambiguous,
            prefer_album=request.prefer_album,
        )
    except Exception as exc:
        scheduler_logger.error(f"Lidarr bulk add failed: {exc}")
        raise HTTPException(status_code=502, detail=f"Lidarr bulk add failed: {exc}")

    for item_result in result.get("results", []):
        index = item_result.get("index")
        if isinstance(index, int):
            await db.update_recommended_missing_entry(
                playlist_id,
                index,
                {"lidarr": item_result},
            )

    return {
        **result,
        "playlist_id": playlist_id,
    }


@app.delete("/api/playlists/{playlist_id}")
async def delete_playlist(playlist_id: int, db: DatabaseManager = Depends(get_db)):
    """Delete a playlist from both local database and Navidrome"""
    try:
        # First, get the specific playlist to find the Navidrome playlist ID
        # Use a direct query instead of fetching all playlists
        playlist = await db.get_playlist_by_id_with_schedule_info(playlist_id)
        
        if not playlist:
            raise HTTPException(status_code=404, detail="Playlist not found")
        
        # Delete from Navidrome if we have a playlist ID
        navidrome_playlist_id = playlist.get("navidrome_playlist_id")
        if navidrome_playlist_id:
            nav_client = get_navidrome_client()
            try:
                print(f"🗑️ Deleting playlist {playlist_id} from Navidrome (Navidrome ID: {navidrome_playlist_id})")
                deletion_result = await nav_client.delete_playlist(navidrome_playlist_id)
                print(f"✅ Navidrome deletion result: {deletion_result}")
            except Exception as e:
                print(f"❌ Warning: Failed to delete playlist from Navidrome: {e}")
                # Continue with local deletion even if Navidrome deletion fails
        else:
            print(f"⚠️ No Navidrome playlist ID found for local playlist {playlist_id}, skipping Navidrome deletion")
        
        # Delete from scheduled playlists if it exists
        if navidrome_playlist_id:
            await db.delete_scheduled_playlist_by_navidrome_id(navidrome_playlist_id)
        
        # Delete from local database
        success = await db.delete_playlist(playlist_id)
        
        if not success:
            raise HTTPException(status_code=404, detail="Playlist not found in database")
        
        return {"message": "Playlist deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete playlist: {str(e)}")

@app.get("/api/recipes")
async def get_available_recipes():
    """Get information about available playlist generation recipes"""
    try:
        recipes_info = recipe_manager.list_available_recipes()
        return recipes_info
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load recipes: {str(e)}")

@app.get("/api/recipes/validate")
async def validate_recipes():
    """Validate all recipe files and return any errors"""
    try:
        registry = recipe_manager._load_registry()
        validation_results = {}
        
        for playlist_type, recipe_filename in registry.items():
            errors = recipe_manager.validate_recipe(recipe_filename)
            validation_results[playlist_type] = {
                "recipe_file": recipe_filename,
                "valid": len(errors) == 0,
                "errors": errors
            }
        
        return validation_results
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to validate recipes: {str(e)}")

@app.get("/api/scheduler/status")
async def get_scheduler_status():
    """Get scheduler status and active jobs"""
    try:
        global scheduler
        if scheduler:
            jobs = list(scheduler.get_jobs())
            job_info = []
            for job in jobs:
                job_info.append({
                    "id": job.id,
                    "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                    "func": job.func.__name__ if hasattr(job, 'func') else str(job.func)
                })
            
            return {
                "scheduler_running": scheduler.running,
                "active_jobs": len(jobs),
                "jobs": job_info,
                "scheduler_state": str(scheduler.state)
            }
        else:
            return {
                "scheduler_running": False,
                "error": "Scheduler not initialized"
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get scheduler status: {str(e)}")

@app.post("/api/scheduler/trigger")
async def trigger_scheduler_check():
    """Manually trigger the scheduler to check for playlists due for refresh"""
    try:
        scheduler_logger.info("🧪 Manual scheduler trigger requested via API")
        await refresh_scheduled_playlists()
        return {"message": "Scheduler check completed successfully"}
    except Exception as e:
        scheduler_logger.error(f"❌ Error in manual scheduler trigger: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to trigger scheduler: {str(e)}")

@app.post("/api/scheduler/start")
async def start_scheduler_job():
    """Manually start the recurring scheduler job"""
    try:
        schedule_playlist_refresh()
        global scheduler
        jobs = list(scheduler.get_jobs()) if scheduler else []
        scheduler_logger.info(f"🔄 Scheduler job registration requested. Active jobs: {len(jobs)}")
        return {
            "message": "Scheduler job started",
            "active_jobs": len(jobs),
            "jobs": [{"id": job.id, "next_run": job.next_run_time.isoformat() if job.next_run_time else None} for job in jobs]
        }
    except Exception as e:
        scheduler_logger.error(f"❌ Error starting scheduler job: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start scheduler job: {str(e)}")

@app.get("/api/ai-model-info")
async def get_ai_model_info():
    """Get current AI model information for analytics"""
    try:
        ai_client_instance = get_ai_client()
        return {
            "provider": ai_client_instance.provider.provider_type,
            "model": ai_client_instance.model or "unknown",
            "has_api_key": bool(ai_client_instance.api_key)
        }
    except Exception as e:
        return {
            "provider": "unknown",
            "model": "unknown", 
            "has_api_key": False
        }

@app.post("/api/track-library-size")
async def track_library_size(db: DatabaseManager = Depends(get_db)):
    """Track library size for analytics (called post-launch)"""
    try:
        # Check if we should track (90+ days since last tracking)
        should_track = await db.should_track_library_size()
        if not should_track:
            return {"message": "Library size tracking not needed yet", "tracked": False}
        
        # Get Navidrome client and query library size
        nav_client = get_navidrome_client()
        song_count = await nav_client.get_total_song_count()
        
        # Get or create user ID and record the data
        user_id = await db.get_or_create_user_id()
        await db.record_library_size(song_count)
        
        scheduler_logger.info(f"📊 Library size tracked: {song_count} songs for user {user_id}")
        
        return {
            "message": "Library size tracked successfully",
            "tracked": True,
            "song_count": song_count,
            "user_id": user_id
        }
        
    except Exception as e:
        scheduler_logger.error(f"❌ Error tracking library size: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to track library size: {str(e)}")

# SPA ROUTING - Smart catch-all for client-side routing (MUST be last route)
@app.get("/{path:path}", response_class=HTMLResponse)
async def spa_router(request: Request, path: str):
    """Handle SPA routing - serve app for known paths, redirect unknown paths"""
    # Known SPA paths - serve the app and let frontend handle routing
    spa_paths = ["this-is", "re-discover", "genre-mix", "playlists", "genre-insights", "system-check"]
    
    if path in spa_paths:
        # Apply same system check logic as root
        if not system_check_passed:
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url="/system-check", status_code=302)
        return templates.TemplateResponse(request=request, name="index.html")
    
    # Unknown paths - redirect to home
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/", status_code=302)

if __name__ == "__main__":
    # Custom logging config to filter out Umami heartbeat requests
    import uvicorn.config
    
    class FilteredUvicornFormatter(uvicorn.formatters.DefaultFormatter):
        def format(self, record):
            # Filter out GET / requests (Umami heartbeats) from access logs
            if hasattr(record, 'args') and record.args:
                # Look for GET / HTTP patterns in the log message
                message = str(record.args[2]) if len(record.args) > 2 else ""
                if 'GET / HTTP' in message:
                    return ""  # Return empty string to suppress this log
            return super().format(record)
    
    # Configure uvicorn with custom formatter
    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["access"]["()"] = FilteredUvicornFormatter
    
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000,
        log_config=log_config
    )