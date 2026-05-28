from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime

class Artist(BaseModel):
    """Schema for Navidrome artist"""
    id: str
    name: str
    album_count: int = 0
    song_count: int = 0

class CreatePlaylistRequest(BaseModel):
    """Request schema for creating a playlist"""
    artist_ids: List[str]
    playlist_name: Optional[str] = None  # Optional, will auto-generate if not provided
    refresh_frequency: str = "none"  # "none", "daily", "weekly", "monthly"
    playlist_length: int = 25  # Number of tracks to include
    library_ids: List[str] = []  # List of library IDs to filter tracks
    artist_concentration: float = 0.8  # 0=diverse, 1=allow seed artist dominance
    album_concentration: float = 0.35
    llm_polish: bool = False

class CreateGenrePlaylistRequest(BaseModel):
    """Request schema for creating a genre mix playlist"""
    genre: Optional[str] = None
    meta_genre: Optional[str] = None
    genre_selection_mode: str = "raw"  # raw | meta
    playlist_name: Optional[str] = None  # Optional, will auto-generate if not provided
    refresh_frequency: str = "none"  # "none", "daily", "weekly", "monthly"
    playlist_length: int = 25  # Number of tracks to include
    library_ids: List[str] = []  # List of library IDs to filter tracks
    artist_concentration: float = 0.35  # 0=more artists, 1=allow repeats
    album_concentration: float = 0.25
    llm_polish: bool = False


class MetaGenreGroup(BaseModel):
    meta_genre: str
    genres: List[str]
    total_song_count: int = 0


class MetaGenreResponse(BaseModel):
    source_key: str
    generated_at: str
    raw_genre_count: int
    source_hash: str
    model_name: Optional[str] = None
    stale: bool = False
    groups: List[MetaGenreGroup] = []
    diagnostics: Dict[str, Any] = {}


class MetaGenreSettingsRequest(BaseModel):
    refresh_frequency: str = "weekly"  # none | daily | weekly | monthly
    min_song_count: int = 0
    min_raw_genres: int = 30
    cache_hours: int = 168
    granularity: str = "balanced"  # coarse | balanced | fine


class MetaGenreSettingsResponse(BaseModel):
    refresh_frequency: str
    min_song_count: int
    min_raw_genres: int
    cache_hours: int
    granularity: str


class MetaGenreInsightsResponse(BaseModel):
    source_key: str
    generated_at: Optional[str] = None
    last_refresh_at: Optional[str] = None
    next_refresh_at: Optional[str] = None
    raw_genre_count: int = 0
    source_hash: Optional[str] = None
    model_name: Optional[str] = None
    stale: bool = True
    total_groups: int = 0
    singleton_groups: int = 0
    singleton_ratio: float = 0.0
    settings: MetaGenreSettingsResponse
    groups: List[MetaGenreGroup] = []
    diagnostics: Dict[str, Any] = {}

class SuggestedMissingTrack(BaseModel):
    """A track suggested by AI that is not in the library"""
    title: str
    artist: str
    album: Optional[str] = None
    note: Optional[str] = None
    lidarr: Optional[Dict[str, Any]] = None


class LidarrAddRequest(BaseModel):
    """Request to add a missing recommendation to Lidarr"""
    index: int
    mode: str  # artist | album
    foreign_artist_id: Optional[str] = None
    foreign_album_id: Optional[str] = None


class LidarrBulkAddRequest(BaseModel):
    """Request to add all missing recommendations to Lidarr"""
    search: bool = True
    monitor_only_target_album: bool = True
    skip_ambiguous: bool = True
    prefer_album: bool = True


class PlaylistSettingsRequest(BaseModel):
    """Editable playlist settings from Manage Playlists"""
    playlist_length: int
    refresh_frequency: str = "none"


class Playlist(BaseModel):
    """Schema for a stored playlist"""
    id: int
    artist_id: str
    playlist_name: str
    songs: List[str] = []
    reasoning: Optional[str] = None
    navidrome_playlist_id: Optional[str] = None
    library_ids: List[str] = []
    curation_options: Dict[str, Any] = {}
    recommended_missing: List[SuggestedMissingTrack] = []
    added_from_suggestions: int = 0
    created_at: str
    updated_at: str

class Song(BaseModel):
    """Schema for a song"""
    id: str
    title: str
    artist: str
    album: str
    duration: Optional[int] = None
    track_number: Optional[int] = None

class PlaylistResponse(BaseModel):
    """Response schema for playlist operations"""
    playlist: Playlist
    message: str

class RediscoverTrack(BaseModel):
    """Schema for a Re-Discover Weekly track"""
    id: str
    title: str
    artist: str
    album: str
    score: float
    historical_plays: int
    days_since_last_play: str

class RediscoverWeeklyResponse(BaseModel):
    """Response schema for Re-Discover Weekly"""
    tracks: List[RediscoverTrack]
    total_tracks: int
    message: str

class RediscoverWeeklyV2Response(BaseModel):
    """Response schema for Re-Discover Weekly v2.0"""
    name: str
    tracks: List[Dict[str, Any]]
    theme: str
    mode: str
    reasoning: str
    user_id: str
    server_id: str
    generated_at: str
    is_fallback: Optional[bool] = False

class CreateRediscoverPlaylistRequest(BaseModel):
    """Request schema for creating a Re-Discover Weekly playlist"""
    refresh_frequency: str = "weekly"  # "daily", "weekly", "monthly"
    playlist_length: int = 25  # Number of tracks to include
    library_ids: List[str] = []  # List of library IDs to filter tracks

class ScheduledPlaylist(BaseModel):
    """Schema for a scheduled playlist"""
    id: int
    playlist_type: str  # "rediscover_weekly"
    navidrome_playlist_id: str
    refresh_frequency: str
    next_refresh: str
    created_at: str
    updated_at: str

class PlaylistWithScheduleInfo(BaseModel):
    """Schema for playlist with schedule information"""
    id: int
    artist_id: str
    playlist_name: str
    songs: List[str]
    created_at: str
    updated_at: str
    navidrome_playlist_id: Optional[str] = None
    refresh_frequency: Optional[str] = None
    next_refresh: Optional[str] = None
    playlist_type: Optional[str] = None