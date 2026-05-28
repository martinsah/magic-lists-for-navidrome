import sqlite3
import aiosqlite
import os
from typing import Any, List, Optional, Dict
from datetime import datetime, timedelta
import json

from .schemas import Playlist, ScheduledPlaylist

class DatabaseManager:
    """SQLite database manager for storing playlists"""
    
    def __init__(self, db_path: str = "magiclists.db"):
        self.db_path = db_path
    
    async def init_db(self):
        """Initialize the database with required tables"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS playlists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artist_id TEXT NOT NULL,
                    playlist_name TEXT NOT NULL,
                    songs TEXT, -- JSON array of song titles
                    reasoning TEXT, -- AI reasoning/description
                    navidrome_playlist_id TEXT, -- Link to Navidrome playlist
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Add reasoning column if it doesn't exist (for existing databases)
            try:
                await db.execute("ALTER TABLE playlists ADD COLUMN reasoning TEXT")
            except:
                # Column already exists or other error - ignore
                pass
            
            # Add navidrome_playlist_id column if it doesn't exist (for existing databases)
            try:
                await db.execute("ALTER TABLE playlists ADD COLUMN navidrome_playlist_id TEXT")
            except:
                # Column already exists or other error - ignore
                pass

            # Add library_ids column if it doesn't exist (for existing databases)
            try:
                await db.execute("ALTER TABLE playlists ADD COLUMN library_ids TEXT")  # JSON array of library IDs
            except:
                # Column already exists or other error - ignore
                pass
            
            # Add last_refreshed column if it doesn't exist (for tracking refreshes)
            try:
                await db.execute("ALTER TABLE playlists ADD COLUMN last_refreshed TIMESTAMP")
            except:
                # Column already exists or other error - ignore
                pass
            
            # Add playlist_length column if it doesn't exist (for storing original length)
            try:
                await db.execute("ALTER TABLE playlists ADD COLUMN playlist_length INTEGER")
            except:
                # Column already exists or other error - ignore
                pass

            for column_sql in (
                "ALTER TABLE playlists ADD COLUMN recommended_missing TEXT",
                "ALTER TABLE playlists ADD COLUMN added_from_suggestions INTEGER DEFAULT 0",
                "ALTER TABLE playlists ADD COLUMN curation_options TEXT",
            ):
                try:
                    await db.execute(column_sql)
                except Exception:
                    pass

            await db.execute("""
                CREATE TABLE IF NOT EXISTS scheduled_playlists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    playlist_type TEXT NOT NULL,
                    navidrome_playlist_id TEXT NOT NULL,
                    refresh_frequency TEXT NOT NULL,
                    next_refresh TIMESTAMP NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS scheduled_playlists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    playlist_type TEXT NOT NULL,
                    navidrome_playlist_id TEXT NOT NULL,
                    refresh_frequency TEXT NOT NULL,
                    next_refresh TIMESTAMP NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create the app_config table for storing application configuration
            await db.execute("""
                CREATE TABLE IF NOT EXISTS app_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            
            # Create the library_analytics table for tracking library size
            await db.execute("""
                CREATE TABLE IF NOT EXISTS library_analytics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    song_count INTEGER NOT NULL,
                    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create the user_preferences table for storing user settings like selected library
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_preferences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,  -- For future multi-user support
                    selected_library_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create the playlist_history_v2 table for Re-Discover Weekly v2.0 logging
            await db.execute("""
                CREATE TABLE IF NOT EXISTS playlist_history_v2 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    playlist_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    navidrome_server_id TEXT NOT NULL,
                    mode_used TEXT NOT NULL,
                    primary_theme TEXT,
                    tracks_analyzed_count INTEGER NOT NULL,
                    track_ids_json TEXT NOT NULL,  -- JSON array of track IDs
                    track_count INTEGER NOT NULL,
                    reasoning TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create the api_cache table for caching API responses
            await db.execute("""
                CREATE TABLE IF NOT EXISTS api_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cache_key TEXT NOT NULL UNIQUE,
                    cache_value TEXT NOT NULL,
                    expires_at TIMESTAMP NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create index on cache_key for faster lookups
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_api_cache_key ON api_cache(cache_key)
            """)

            # Create index on expires_at for cleanup
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_api_cache_expires ON api_cache(expires_at)
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS meta_genre_snapshots (
                    source_key TEXT PRIMARY KEY,
                    source_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    raw_genre_count INTEGER NOT NULL DEFAULT 0,
                    model_name TEXT,
                    generated_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_meta_genre_generated_at
                ON meta_genre_snapshots(generated_at)
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS library_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_key TEXT NOT NULL UNIQUE,
                    server_type TEXT NOT NULL DEFAULT 'navidrome',
                    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS track_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL,
                    navidrome_track_id TEXT NOT NULL,
                    stable_key TEXT NOT NULL,
                    title TEXT,
                    artist TEXT,
                    album TEXT,
                    duration INTEGER,
                    genres_json TEXT,
                    play_count INTEGER DEFAULT 0,
                    rating INTEGER DEFAULT 0,
                    starred INTEGER DEFAULT 0,
                    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    raw_json TEXT,
                    UNIQUE(source_id, navidrome_track_id),
                    FOREIGN KEY(source_id) REFERENCES library_sources(id)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS score_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL,
                    recipe_id TEXT NOT NULL,
                    scoring_version TEXT NOT NULL,
                    params_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(source_id) REFERENCES library_sources(id)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS track_scores (
                    score_run_id INTEGER NOT NULL,
                    track_snapshot_id INTEGER NOT NULL,
                    score REAL NOT NULL,
                    components_json TEXT,
                    rank INTEGER,
                    PRIMARY KEY(score_run_id, track_snapshot_id),
                    FOREIGN KEY(score_run_id) REFERENCES score_runs(id),
                    FOREIGN KEY(track_snapshot_id) REFERENCES track_snapshots(id)
                )
            """)

            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_track_snapshots_stable_key
                ON track_snapshots(stable_key)
            """)

            await db.commit()
    
    async def create_playlist(self, artist_id: str, playlist_name: str, songs: Optional[List[str]] = None, reasoning: Optional[str] = None, navidrome_playlist_id: Optional[str] = None, playlist_length: Optional[int] = None, library_ids: Optional[List[str]] = None, curation_options: Optional[Dict[str, Any]] = None) -> Optional[Playlist]:
        """Create a new playlist in the database"""
        await self.init_db()
        
        songs_json = json.dumps(songs or [])
        library_ids_json = json.dumps(library_ids or [])
        curation_options_json = json.dumps(curation_options or {})

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                INSERT INTO playlists (artist_id, playlist_name, songs, reasoning, navidrome_playlist_id, playlist_length, library_ids, curation_options)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (artist_id, playlist_name, songs_json, reasoning, navidrome_playlist_id, playlist_length, library_ids_json, curation_options_json))
            
            playlist_id = cursor.lastrowid
            await db.commit()
            
            # Fetch the created playlist
            async with db.execute("""
                SELECT id, artist_id, playlist_name, songs, reasoning, navidrome_playlist_id, created_at, updated_at, playlist_length, library_ids
                FROM playlists WHERE id = ?
            """, (playlist_id,)) as cursor:
                row = await cursor.fetchone()
                
                if row:
                    return Playlist(
                        id=row[0],
                        artist_id=row[1],
                        playlist_name=row[2],
                        songs=json.loads(row[3]),
                        reasoning=row[4],
                        navidrome_playlist_id=row[5],
                        created_at=row[6],
                        updated_at=row[7],
                        library_ids=json.loads(row[9]) if row[9] else []
                    )
                return None
    
    async def get_playlist(self, playlist_id: int) -> Optional[Playlist]:
        """Get a playlist by ID"""
        await self.init_db()
        
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT id, artist_id, playlist_name, songs, reasoning, navidrome_playlist_id, created_at, updated_at, playlist_length, library_ids
                FROM playlists WHERE id = ?
            """, (playlist_id,)) as cursor:
                row = await cursor.fetchone()
                
                if row:
                    return Playlist(
                        id=row[0],
                        artist_id=row[1],
                        playlist_name=row[2],
                        songs=json.loads(row[3]),
                        reasoning=row[4],
                        navidrome_playlist_id=row[5],
                        created_at=row[6],
                        updated_at=row[7],
                        library_ids=json.loads(row[9]) if row[9] else []
                    )
        return None
    
    async def get_playlists_by_artist(self, artist_id: str) -> List[Playlist]:
        """Get all playlists for a specific artist"""
        await self.init_db()
        
        playlists = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT id, artist_id, playlist_name, songs, created_at, updated_at
                FROM playlists WHERE artist_id = ?
                ORDER BY created_at DESC
            """, (artist_id,)) as cursor:
                rows = await cursor.fetchall()
                
                for row in rows:
                    playlist = Playlist(
                        id=row[0],
                        artist_id=row[1],
                        playlist_name=row[2],
                        songs=json.loads(row[3]),
                        created_at=row[4],
                        updated_at=row[5]
                    )
                    playlists.append(playlist)
        
        return playlists
    
    async def get_all_playlists_with_schedule_info(self) -> List[Dict]:
        """Get all playlists with their scheduling information"""
        await self.init_db()
        
        playlists = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT 
                    p.id, 
                    p.artist_id, 
                    p.playlist_name, 
                    p.songs, 
                    p.reasoning,
                    p.navidrome_playlist_id,
                    p.created_at, 
                    p.updated_at,
                    p.last_refreshed,
                    p.playlist_length,
                    p.recommended_missing,
                    p.added_from_suggestions,
                    p.library_ids,
                    p.curation_options,
                    sp.id,
                    sp.refresh_frequency,
                    sp.next_refresh,
                    sp.playlist_type
                FROM playlists p
                LEFT JOIN scheduled_playlists sp ON p.navidrome_playlist_id = sp.navidrome_playlist_id
                ORDER BY p.created_at DESC
            """) as cursor:
                rows = await cursor.fetchall()
                
                for row in rows:
                    playlist_data = {
                        "id": row[0],
                        "artist_id": row[1],
                        "playlist_name": row[2],
                        "songs": json.loads(row[3]) if row[3] else [],
                        "reasoning": row[4],
                        "navidrome_playlist_id": row[5],
                        "created_at": row[6],
                        "updated_at": row[7],
                        "last_refreshed": row[8],
                        "playlist_length": row[9],
                        "recommended_missing": json.loads(row[10]) if row[10] else [],
                        "added_from_suggestions": row[11] or 0,
                        "library_ids": json.loads(row[12]) if row[12] else [],
                        "curation_options": json.loads(row[13]) if row[13] else {},
                        "schedule_id": row[14],
                        "refresh_frequency": row[15],
                        "next_refresh": row[16],
                        "playlist_type": row[17],
                    }
                    playlists.append(playlist_data)
        
        return playlists
    
    async def get_playlist_by_id_with_schedule_info(self, playlist_id: int) -> Optional[Dict]:
        """Get a specific playlist with its scheduling information"""
        await self.init_db()
        
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT 
                    p.id, 
                    p.artist_id, 
                    p.playlist_name, 
                    p.songs, 
                    p.reasoning,
                    p.playlist_length,
                    p.library_ids,
                    p.recommended_missing,
                    p.added_from_suggestions,
                    p.curation_options,
                    p.created_at, 
                    p.updated_at,
                    p.navidrome_playlist_id,
                    sp.id,
                    sp.refresh_frequency,
                    sp.next_refresh,
                    sp.playlist_type
                FROM playlists p
                LEFT JOIN scheduled_playlists sp ON p.navidrome_playlist_id = sp.navidrome_playlist_id
                WHERE p.id = ?
            """, (playlist_id,)) as cursor:
                row = await cursor.fetchone()
                
                if row:
                    return {
                        "id": row[0],
                        "artist_id": row[1],
                        "playlist_name": row[2],
                        "songs": json.loads(row[3]),
                        "reasoning": row[4],
                        "playlist_length": row[5],
                        "library_ids": json.loads(row[6]) if row[6] else [],
                        "recommended_missing": json.loads(row[7]) if row[7] else [],
                        "added_from_suggestions": row[8] or 0,
                        "curation_options": json.loads(row[9]) if row[9] else {},
                        "created_at": row[10],
                        "updated_at": row[11],
                        "navidrome_playlist_id": row[12],
                        "schedule_id": row[13],
                        "refresh_frequency": row[14],
                        "next_refresh": row[15],
                        "playlist_type": row[16]
                    }
        
        return None

    async def update_playlist_settings(self, playlist_id: int, playlist_length: int) -> bool:
        """Update editable playlist settings stored on the playlist row."""
        await self.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                UPDATE playlists
                SET playlist_length = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (playlist_length, playlist_id))
            await db.commit()
            return cursor.rowcount > 0

    async def upsert_scheduled_playlist(
        self,
        playlist_type: str,
        navidrome_playlist_id: str,
        refresh_frequency: str,
        next_refresh: datetime,
    ) -> bool:
        """Create or update a scheduled playlist by Navidrome playlist ID."""
        await self.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                UPDATE scheduled_playlists
                SET playlist_type = ?,
                    refresh_frequency = ?,
                    next_refresh = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE navidrome_playlist_id = ?
            """, (
                playlist_type,
                refresh_frequency,
                next_refresh.isoformat(),
                navidrome_playlist_id,
            ))

            if cursor.rowcount == 0:
                await db.execute("""
                    INSERT INTO scheduled_playlists (
                        playlist_type,
                        navidrome_playlist_id,
                        refresh_frequency,
                        next_refresh
                    )
                    VALUES (?, ?, ?, ?)
                """, (
                    playlist_type,
                    navidrome_playlist_id,
                    refresh_frequency,
                    next_refresh.isoformat(),
                ))

            await db.commit()
            return True
    
    async def delete_playlist(self, playlist_id: int) -> bool:
        """Delete a playlist from the database"""
        await self.init_db()
        
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                DELETE FROM playlists WHERE id = ?
            """, (playlist_id,))
            
            await db.commit()
            return cursor.rowcount > 0
    
    async def delete_scheduled_playlist_by_navidrome_id(self, navidrome_playlist_id: str) -> bool:
        """Delete a scheduled playlist by Navidrome playlist ID"""
        await self.init_db()
        
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                DELETE FROM scheduled_playlists WHERE navidrome_playlist_id = ?
            """, (navidrome_playlist_id,))
            
            await db.commit()
            return cursor.rowcount > 0
    
    async def update_playlist_songs(self, playlist_id: int, songs: List[str]) -> bool:
        """Update the songs in a playlist"""
        await self.init_db()
        
        songs_json = json.dumps(songs)
        
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                UPDATE playlists 
                SET songs = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (songs_json, playlist_id))
            
            await db.commit()
            return cursor.rowcount > 0

    async def update_playlist_suggestions(
        self,
        playlist_id: int,
        recommended_missing: List[Dict],
        added_from_suggestions: int,
    ) -> bool:
        """Persist missing recommendations and appended suggestion count."""
        await self.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                UPDATE playlists
                SET recommended_missing = ?,
                    added_from_suggestions = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                json.dumps(recommended_missing or []),
                added_from_suggestions,
                playlist_id,
            ))
            await db.commit()
            return cursor.rowcount > 0

    async def get_recommended_missing(self, playlist_id: int) -> Optional[List[Dict]]:
        """Return recommended_missing list for a playlist."""
        await self.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT recommended_missing FROM playlists WHERE id = ?",
                (playlist_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if not row or not row[0]:
                    return []
                return json.loads(row[0])

    async def update_recommended_missing_entry(
        self,
        playlist_id: int,
        index: int,
        patch: Dict,
    ) -> Optional[List[Dict]]:
        """Merge patch into recommended_missing[index] and persist."""
        entries = await self.get_recommended_missing(playlist_id) or []
        if index < 0 or index >= len(entries):
            return None

        updated_entry = dict(entries[index])
        if "lidarr" in patch:
            existing_lidarr = dict(updated_entry.get("lidarr") or {})
            existing_lidarr.update(patch["lidarr"])
            updated_entry["lidarr"] = existing_lidarr
        else:
            updated_entry.update(patch)
        entries[index] = updated_entry

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE playlists
                SET recommended_missing = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (json.dumps(entries), playlist_id),
            )
            await db.commit()
        return entries
    
    async def create_scheduled_playlist(self, playlist_type: str, navidrome_playlist_id: str,
                                      refresh_frequency: str, next_refresh: datetime) -> ScheduledPlaylist:
        """Create a new scheduled playlist"""
        await self.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                INSERT INTO scheduled_playlists (playlist_type, navidrome_playlist_id, refresh_frequency, next_refresh)
                VALUES (?, ?, ?, ?)
            """, (playlist_type, navidrome_playlist_id, refresh_frequency, next_refresh.isoformat()))

            scheduled_id = cursor.lastrowid
            await db.commit()

            # Fetch the created scheduled playlist
            async with db.execute("""
                SELECT id, playlist_type, navidrome_playlist_id, refresh_frequency, next_refresh, created_at, updated_at
                FROM scheduled_playlists WHERE id = ?
            """, (scheduled_id,)) as cursor:
                row = await cursor.fetchone()

                if row:
                    return ScheduledPlaylist(
                        id=row[0],
                        playlist_type=row[1],
                        navidrome_playlist_id=row[2],
                        refresh_frequency=row[3],
                        next_refresh=row[4],
                        created_at=row[5],
                        updated_at=row[6]
                    )

        # This should never happen, but handle it gracefully
        raise Exception("Failed to create scheduled playlist")
    
    async def get_scheduled_playlists_due(self, current_time: datetime, grace_hours: int = 168) -> List[ScheduledPlaylist]:
        """Get all scheduled playlists that are due for refresh, including overdue ones within grace period
        
        Args:
            current_time: Current timestamp to check against
            grace_hours: Hours to look back for missed refreshes (default 7 days = 168 hours)
        """
        await self.init_db()
        
        # Calculate grace period cutoff (7 days ago by default)
        from datetime import timedelta
        grace_cutoff = current_time - timedelta(hours=grace_hours)
        
        scheduled_playlists = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT id, playlist_type, navidrome_playlist_id, refresh_frequency, next_refresh, created_at, updated_at
                FROM scheduled_playlists 
                WHERE next_refresh <= ? AND next_refresh >= ?
                ORDER BY next_refresh ASC
            """, (current_time.isoformat(), grace_cutoff.isoformat())) as cursor:
                rows = await cursor.fetchall()
                
                for row in rows:
                    scheduled_playlist = ScheduledPlaylist(
                        id=row[0],
                        playlist_type=row[1],
                        navidrome_playlist_id=row[2],
                        refresh_frequency=row[3],
                        next_refresh=row[4],
                        created_at=row[5],
                        updated_at=row[6]
                    )
                    scheduled_playlists.append(scheduled_playlist)
        
        return scheduled_playlists
    
    async def update_scheduled_playlist_next_refresh(self, scheduled_id: int, next_refresh: datetime) -> bool:
        """Update the next refresh time for a scheduled playlist"""
        await self.init_db()
        
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                UPDATE scheduled_playlists 
                SET next_refresh = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (next_refresh.isoformat(), scheduled_id))
            
            await db.commit()
            return cursor.rowcount > 0
    
    async def update_playlist_last_refreshed(self, navidrome_playlist_id: str) -> bool:
        """Update the last_refreshed timestamp for a playlist"""
        await self.init_db()
        
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                UPDATE playlists 
                SET last_refreshed = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE navidrome_playlist_id = ?
            """, (navidrome_playlist_id,))
            
            await db.commit()
            return cursor.rowcount > 0
    
    async def update_playlist_content(self, navidrome_playlist_id: str, songs: List[str], reasoning: Optional[str] = None) -> bool:
        """Update the songs and reasoning for a playlist during refresh"""
        await self.init_db()
        
        songs_json = json.dumps(songs)
        
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                UPDATE playlists 
                SET songs = ?, reasoning = ?, last_refreshed = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE navidrome_playlist_id = ?
            """, (songs_json, reasoning, navidrome_playlist_id))
            
            await db.commit()
            return cursor.rowcount > 0
    
    async def get_config(self, key: str) -> Optional[str]:
        """Get a configuration value by key"""
        await self.init_db()
        
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT value FROM app_config WHERE key = ?
            """, (key,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None
    
    async def set_config(self, key: str, value: str) -> bool:
        """Set a configuration value"""
        await self.init_db()
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO app_config (key, value) VALUES (?, ?)
            """, (key, value))
            await db.commit()
            return True
    
    async def get_or_create_user_id(self) -> str:
        """Get or create a unique user ID for analytics tracking"""
        import uuid
        
        user_id = await self.get_config("user_id")
        if not user_id:
            # Generate a new unique user ID
            user_id = str(uuid.uuid4())
            await self.set_config("user_id", user_id)
        
        return user_id
    
    async def should_track_library_size(self) -> bool:
        """Check if we should track library size (90+ days since last tracking)"""
        await self.init_db()
        
        from datetime import datetime, timedelta
        
        # Get the last tracking timestamp
        last_tracked = await self.get_config("last_library_tracking")
        if not last_tracked:
            return True  # Never tracked before
        
        try:
            last_tracked_date = datetime.fromisoformat(last_tracked)
            cutoff_date = datetime.now() - timedelta(days=90)
            return last_tracked_date < cutoff_date
        except:
            return True  # Invalid timestamp, track again
    
    async def record_library_size(self, song_count: int) -> bool:
        """Record library size for analytics"""
        await self.init_db()
        
        user_id = await self.get_or_create_user_id()
        current_time = datetime.now()
        
        async with aiosqlite.connect(self.db_path) as db:
            # Insert the library analytics record
            await db.execute("""
                INSERT INTO library_analytics (user_id, song_count, recorded_at)
                VALUES (?, ?, ?)
            """, (user_id, song_count, current_time.isoformat()))
            
            # Update the last tracking timestamp
            await db.execute("""
                INSERT OR REPLACE INTO app_config (key, value) VALUES (?, ?)
            """, ("last_library_tracking", current_time.isoformat()))
            
            await db.commit()
            return True

    async def record_scoring_run(
        self,
        source_key: str,
        recipe_id: str,
        scoring_version: str,
        params: Dict[str, Any],
        scored_tracks: List[Dict[str, Any]],
    ) -> Optional[int]:
        """Persist derived metadata and score components for a curation run."""
        await self.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO library_sources (source_key, server_type, last_seen_at)
                VALUES (?, 'navidrome', CURRENT_TIMESTAMP)
                ON CONFLICT(source_key) DO UPDATE SET last_seen_at = CURRENT_TIMESTAMP
            """, (source_key,))

            async with db.execute(
                "SELECT id FROM library_sources WHERE source_key = ?",
                (source_key,),
            ) as source_cursor:
                source_row = await source_cursor.fetchone()
                if not source_row:
                    return None
                source_id = source_row[0]

            run_cursor = await db.execute("""
                INSERT INTO score_runs (
                    source_id,
                    recipe_id,
                    scoring_version,
                    params_json
                )
                VALUES (?, ?, ?, ?)
            """, (
                source_id,
                recipe_id,
                scoring_version,
                json.dumps(params, sort_keys=True),
            ))
            score_run_id = run_cursor.lastrowid

            for rank, track in enumerate(scored_tracks, start=1):
                navidrome_track_id = str(track.get("id") or "")
                if not navidrome_track_id:
                    continue
                stable_key = track.get("_stable_key") or navidrome_track_id
                await db.execute("""
                    INSERT INTO track_snapshots (
                        source_id,
                        navidrome_track_id,
                        stable_key,
                        title,
                        artist,
                        album,
                        duration,
                        genres_json,
                        play_count,
                        rating,
                        starred,
                        raw_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_id, navidrome_track_id) DO UPDATE SET
                        stable_key = excluded.stable_key,
                        title = excluded.title,
                        artist = excluded.artist,
                        album = excluded.album,
                        duration = excluded.duration,
                        genres_json = excluded.genres_json,
                        play_count = excluded.play_count,
                        rating = excluded.rating,
                        starred = excluded.starred,
                        last_seen_at = CURRENT_TIMESTAMP,
                        raw_json = excluded.raw_json
                """, (
                    source_id,
                    navidrome_track_id,
                    stable_key,
                    track.get("title"),
                    track.get("artist"),
                    track.get("album"),
                    track.get("duration"),
                    json.dumps(track.get("genres") or ([track.get("genre")] if track.get("genre") else [])),
                    int(track.get("play_count") or 0),
                    int(track.get("rating") or 0),
                    1 if track.get("local_library_likes") or track.get("starred") else 0,
                    json.dumps({k: v for k, v in track.items() if not k.startswith("_")}, sort_keys=True),
                ))

                async with db.execute("""
                    SELECT id FROM track_snapshots
                    WHERE source_id = ? AND navidrome_track_id = ?
                """, (source_id, navidrome_track_id)) as snapshot_cursor:
                    snapshot_row = await snapshot_cursor.fetchone()
                    if not snapshot_row:
                        continue
                    snapshot_id = snapshot_row[0]

                await db.execute("""
                    INSERT OR REPLACE INTO track_scores (
                        score_run_id,
                        track_snapshot_id,
                        score,
                        components_json,
                        rank
                    )
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    score_run_id,
                    snapshot_id,
                    float(track.get("_score") or 0),
                    json.dumps(track.get("_score_components") or {}, sort_keys=True),
                    rank,
                ))

            await db.commit()
            return score_run_id

    async def get_user_preference(self, user_id: str, key: str) -> Optional[str]:
        """Get a user preference value"""
        await self.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT value FROM user_preferences WHERE user_id = ? AND key = ?
            """, (user_id, key)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def set_user_preference(self, user_id: str, key: str, value: str) -> bool:
        """Set a user preference value"""
        await self.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO user_preferences (user_id, key, value, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """, (user_id, key, value))
            await db.commit()
            return True

    async def get_selected_library_id(self, user_id: str) -> Optional[str]:
        """Get the user's selected library ID"""
        return await self.get_user_preference(user_id, "selected_library_id")

    async def set_selected_library_id(self, user_id: str, library_id: str) -> bool:
        """Set the user's selected library ID"""
        return await self.set_user_preference(user_id, "selected_library_id", library_id)

    async def get_cache(self, cache_key: str) -> Optional[str]:
        """Get a cached value by key, checking expiration"""
        await self.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT cache_value FROM api_cache
                WHERE cache_key = ? AND expires_at > datetime('now')
            """, (cache_key,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def set_cache(self, cache_key: str, cache_value: str, ttl_seconds: int) -> bool:
        """Set a cached value with TTL (time to live in seconds)"""
        await self.init_db()

        from datetime import datetime, timedelta
        expires_at = datetime.now() + timedelta(seconds=ttl_seconds)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO api_cache (cache_key, cache_value, expires_at)
                VALUES (?, ?, ?)
            """, (cache_key, cache_value, expires_at.isoformat()))
            await db.commit()
            return True

    async def cleanup_expired_cache(self) -> int:
        """Clean up expired cache entries. Returns number of entries deleted."""
        await self.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                DELETE FROM api_cache WHERE expires_at <= datetime('now')
            """)
            deleted_count = cursor.rowcount
            await db.commit()
            return deleted_count

    async def get_meta_genre_snapshot(self, source_key: str) -> Optional[Dict[str, Any]]:
        """Get latest meta-genre snapshot for a source."""
        await self.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT source_key, source_hash, payload_json, raw_genre_count, model_name, generated_at, updated_at
                FROM meta_genre_snapshots
                WHERE source_key = ?
            """, (source_key,)) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                payload = json.loads(row[2]) if row[2] else {}
                return {
                    "source_key": row[0],
                    "source_hash": row[1],
                    "payload": payload,
                    "raw_genre_count": row[3] or 0,
                    "model_name": row[4],
                    "generated_at": row[5],
                    "updated_at": row[6],
                }

    async def upsert_meta_genre_snapshot(
        self,
        source_key: str,
        source_hash: str,
        payload: Dict[str, Any],
        raw_genre_count: int,
        model_name: Optional[str] = None,
        generated_at: Optional[str] = None,
    ) -> bool:
        """Insert or update a distilled meta-genre snapshot."""
        await self.init_db()

        generated_value = generated_at or datetime.now().isoformat()
        payload_json = json.dumps(payload or {}, sort_keys=True)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO meta_genre_snapshots (
                    source_key,
                    source_hash,
                    payload_json,
                    raw_genre_count,
                    model_name,
                    generated_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(source_key) DO UPDATE SET
                    source_hash = excluded.source_hash,
                    payload_json = excluded.payload_json,
                    raw_genre_count = excluded.raw_genre_count,
                    model_name = excluded.model_name,
                    generated_at = excluded.generated_at,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                source_key,
                source_hash,
                payload_json,
                raw_genre_count,
                model_name,
                generated_value,
            ))
            await db.commit()
            return True

    async def is_meta_genre_snapshot_stale(self, source_key: str, max_age_hours: int) -> bool:
        """Return True when snapshot is missing or older than max_age_hours."""
        snapshot = await self.get_meta_genre_snapshot(source_key)
        if not snapshot:
            return True

        try:
            generated_at = datetime.fromisoformat(str(snapshot["generated_at"]))
        except ValueError:
            return True

        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        return generated_at < cutoff

# Dependency for FastAPI
async def get_db() -> DatabaseManager:
    """FastAPI dependency to get database manager"""
    # Get database path from environment variable with smart defaults
    # Docker: /app/data/magiclists.db (set in docker-compose.yml)
    # Standalone: ./magiclists.db (current directory)
    default_path = "/app/data/magiclists.db" if os.path.exists("/app/data") else "./magiclists.db"
    db_path = os.getenv("DATABASE_PATH", default_path)
    return DatabaseManager(db_path)