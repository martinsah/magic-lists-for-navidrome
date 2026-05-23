# MagicLists for Navidrome

**AI-assisted playlists for your own music library.**

MagicLists adds curated, evolving playlists similar to major streaming services, but runs entirely against your self-hosted Navidrome server. No subscriptions and no renting your music back—just smart mixes generated from the library you already own.

## What it does

- **This Is (Artist)** — Builds a definitive playlist for any artist in your library, combining hits, deep cuts, and featured appearances without duplicates.
- **Genre Mix** — Creates curated playlists from genre collections using AI, with smart pre-filtering so large genres stay fast and reliable.
- **Re-Discover** — Surfaces tracks you have not played recently, including a v2 flow with temporal analysis and two-phase AI curation.
- **Recommended but missing** (optional) — After a playlist is created, the AI can suggest tracks not in the candidate pool. Matches found in your library are appended to the Navidrome playlist; others are listed in **Manage → Playlists** for acquisition.
- **Auto-refresh** — Keep playlists fresh with daily, weekly, or monthly updates.
- **Manage playlists** — View, delete, and inspect AI reasoning and missing-track suggestions from the web UI.
- **Multi-library support** — Filter artists, genres, and curation by Navidrome music folder.

## Current status

This branch includes recent production-focused improvements:

| Area | Status |
|------|--------|
| Playlist creation (This Is, Genre Mix, Re-Discover v2) | Stable with structured AI JSON and fallbacks |
| Genre Mix at scale | Pre-filters large libraries before LLM; handles timeouts and partial responses |
| Google Gemini 2.5 Flash | Thinking budget disabled for structured JSON to avoid truncated responses |
| Missing-track recommendations | Opt-in via `ENABLE_MISSING_RECOMMENDATIONS`; library matches append automatically |
| Genre picker | Filter genres by minimum track count (1+, 25+, 100+, 250+) |
| System check / UI | Startup health checks; template rendering compatible with current Starlette |
| Docker | Build from source or extend an existing compose stack; credentials via environment variables only |

Playlist curation still selects exactly **N** tracks from your library for the main mix. Optional suggestions may add extra tracks when they already exist in Navidrome.

## Why it matters

Navidrome users already own their music. MagicLists brings modern curation tools into that world so playlists feel alive rather than static, and your collection keeps surprising you.

## Who is behind it

MagicLists was created by Ricky (Synnot Studio), a product designer with long experience in tech and open-source, privacy-friendly music tools. This repository is an actively maintained fork with additional reliability and feature work on top of that foundation.

## What is next

Planned or experimental directions include:

- Multi-artist radio blends
- Decade- and discovery-focused lists
- Creative journeys such as track-to-track sonic paths and genre archaeology

Feedback and contributions are welcome as the project evolves.

## Screenshots

![Artist Radio UI](assets/images/artist-playlist.png)

_Caption: Creating a "This Is (Artist)" playlist_

## Installation

### Recommended: Add to your existing Docker Compose

Running MagicLists on the same Docker network as Navidrome keeps connections simple and reliable.

**Option A — Build from this repository (recommended for latest changes):**

```yaml
services:
  navidrome:
    # ... your existing Navidrome config ...

  magiclists:
    build:
      context: ./magic-lists-for-navidrome
      dockerfile: Dockerfile
    container_name: magiclists
    ports:
      - "4545:8000"
    environment:
      - NAVIDROME_URL=http://navidrome:4533
      - NAVIDROME_USERNAME=${NAVIDROME_USERNAME}
      - NAVIDROME_PASSWORD=${NAVIDROME_PASSWORD}
      - DATABASE_PATH=/app/data/magiclists.db
      - AI_PROVIDER=${AI_PROVIDER:-google}
      - AI_API_KEY=${AI_API_KEY}
      - AI_MODEL=${AI_MODEL:-gemini-2.5-flash}
      - ENABLE_MISSING_RECOMMENDATIONS=${ENABLE_MISSING_RECOMMENDATIONS:-false}
      - APPEND_LIBRARY_MATCHES=${APPEND_LIBRARY_MATCHES:-true}
      - MAX_SUGGESTED_MISSING=${MAX_SUGGESTED_MISSING:-10}
    volumes:
      - ./magiclists-data:/app/data
    restart: unless-stopped
```

**Option B — Compose extends** (child `docker-compose.yml` in this repo):

```yaml
  magiclists:
    extends:
      file: ./magic-lists-for-navidrome/docker-compose.yml
      service: magiclists
    container_name: magiclists
    environment:
      - NAVIDROME_URL=http://navidrome:4533
      - NAVIDROME_USERNAME=${NAVIDROME_USERNAME}
      - NAVIDROME_PASSWORD=${NAVIDROME_PASSWORD}
      - DATABASE_PATH=/app/data/magiclists.db
      - AI_PROVIDER=${AI_PROVIDER:-google}
      - AI_API_KEY=${AI_API_KEY}
      - AI_MODEL=${AI_MODEL:-gemini-2.5-flash}
    volumes:
      - ./magiclists-data:/app/data
```

Set credentials in a `.env` file or your host environment—do not commit secrets to git.

Start the stack:

```bash
docker compose up -d --build magiclists
```

Access MagicLists at http://localhost:4545

Use the Navidrome **service name** as the hostname in `NAVIDROME_URL` (for example `http://navidrome:4533`). If your service is named differently in compose, adjust accordingly.

### Alternative: Pre-built image

```yaml
  magiclists:
    image: rickysynnot/magic-lists-for-navidrome:latest
    container_name: magiclists
    ports:
      - "4545:8000"
    environment:
      - NAVIDROME_URL=http://navidrome:4533
      - NAVIDROME_USERNAME=${NAVIDROME_USERNAME}
      - NAVIDROME_PASSWORD=${NAVIDROME_PASSWORD}
      - DATABASE_PATH=/app/data/magiclists.db
      - AI_PROVIDER=${AI_PROVIDER:-openrouter}
      - AI_API_KEY=${AI_API_KEY}
      - AI_MODEL=${AI_MODEL:-meta-llama/llama-3.3-70b-instruct}
    volumes:
      - ./magiclists-data:/app/data
    restart: unless-stopped
```

### Alternative: Standalone Docker container

Use this if you cannot modify an existing compose file.

**Navidrome on the public internet:**

```bash
docker run -d \
  --name magiclists \
  -p 4545:8000 \
  -e NAVIDROME_URL=https://music.yourdomain.com \
  -e NAVIDROME_USERNAME=your_username \
  -e NAVIDROME_PASSWORD=your_password \
  -e DATABASE_PATH=/app/data/magiclists.db \
  -e AI_PROVIDER=google \
  -e AI_API_KEY=your_api_key \
  -e AI_MODEL=gemini-2.5-flash \
  -v ./magiclists-data:/app/data \
  rickysynnot/magic-lists-for-navidrome:latest
```

**Navidrome on the same host:**

```bash
docker run -d \
  --name magiclists \
  -p 4545:8000 \
  -e NAVIDROME_URL=http://host.docker.internal:4533 \
  -e NAVIDROME_USERNAME=your_username \
  -e NAVIDROME_PASSWORD=your_password \
  -e DATABASE_PATH=/app/data/magiclists.db \
  -v ./magiclists-data:/app/data \
  rickysynnot/magic-lists-for-navidrome:latest
```

## Running without Docker

1. Clone the repository:

```bash
git clone https://github.com/martinsah/magic-lists-for-navidrome.git
cd magic-lists-for-navidrome
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Configure environment:

```bash
cp .env.example .env
# Edit .env with Navidrome URL, credentials, and optional AI settings
```

4. Run the application:

```bash
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

5. Open http://localhost:8000 (or map port 4545 as you prefer).

## Optional: Missing-track recommendations

When enabled, the AI may return `suggested_tracks` in addition to the main playlist selection.

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_MISSING_RECOMMENDATIONS` | `false` | Turn on post-curation suggestions |
| `APPEND_LIBRARY_MATCHES` | `true` | Append suggestions that exist in Navidrome |
| `MAX_SUGGESTED_MISSING` | `10` | Maximum suggestions to request |

Navidrome playlists may end up **longer than the requested track count** when library matches are appended. Tracks that are not in the library appear under **Manage → Playlists**.

Scheduled playlist refresh does not re-run the missing-track pass; it applies on create only.

## Troubleshooting

### Database write errors (500 on create)

Ensure `DATABASE_PATH` is set and the data directory is writable:

- **Docker:** `DATABASE_PATH=/app/data/magiclists.db` with a volume on `/app/data`
- **Standalone:** `DATABASE_PATH=./magiclists.db`

### Connection issues

Common `NAVIDROME_URL` values:

- Same Docker network: `http://navidrome:4533` (use your service name)
- Same host: `http://host.docker.internal:4533` (Docker Desktop) or the Docker bridge IP on Linux
- LAN: `http://192.168.x.x:4533`
- Public: `https://music.yourdomain.com`

Verify both containers share a network:

```bash
docker network ls
docker network inspect your_network_name
docker ps --format "table {{.Names}}\t{{.Networks}}"
```

### Genre Mix slow or incomplete

Large genres are pre-filtered before the LLM call. For Google Gemini 2.5 Flash, structured responses disable internal thinking so JSON is not truncated. If problems persist, increase `GOOGLE_AI_TIMEOUT` (default 120) or reduce playlist length.

### No artists or genres

- Run a library scan in Navidrome
- Confirm credentials in System Check
- For multiple libraries, select folders in the UI before creating playlists

Use the in-app **System Check** page (`/system-check`) for guided diagnostics.

## System check page

On startup, MagicLists validates:

- Required environment variables
- Navidrome reachability and authentication
- Artists API access
- AI provider configuration
- Multi-library setup

Failed checks show actionable suggestions. You can re-run checks anytime from the UI.

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Web interface |
| GET | `/api/artists` | List artists |
| GET | `/api/genres?min_song_count=N` | List genres with optional minimum track filter |
| GET | `/api/music-folders` | List Navidrome libraries |
| POST | `/api/create_playlist` | Create a This Is playlist |
| POST | `/api/create_playlist_with_reasoning` | Create This Is playlist with reasoning |
| POST | `/api/create_genre_playlist` | Create a Genre Mix playlist |
| GET | `/api/rediscover-weekly` | Re-Discover recommendations (v1) |
| GET | `/api/rediscover-weekly-v2` | Re-Discover v2 preview |
| POST | `/api/create-rediscover-playlist` | Create Re-Discover v1 playlist |
| POST | `/api/create-rediscover-playlist-v2` | Create Re-Discover v2 playlist |
| GET | `/api/playlists` | List managed playlists (includes missing suggestions metadata) |
| DELETE | `/api/playlists/{id}` | Delete local record and Navidrome playlist |
| GET | `/api/recipes` | List recipe versions |
| GET | `/api/scheduler/status` | Scheduler status |
| POST | `/api/scheduler/trigger` | Trigger refresh manually |

## AI configuration (optional)

Without AI, playlists fall back to play-count and metadata sorting.

Supported providers:

1. **Fallback** — Play count and metadata (no API key)
2. **Ollama** — Local models
3. **OpenRouter** — Cloud models
4. **Google AI** — Gemini models (recommended for Genre Mix at scale)
5. **Groq** — Fast cloud models

### Ollama

```bash
AI_PROVIDER=ollama
AI_MODEL=llama3.2
OLLAMA_BASE_URL=http://localhost:11434/v1/chat/completions
# Docker on host: OLLAMA_BASE_URL=http://host.docker.internal:11434/v1/chat/completions
```

### OpenRouter

```bash
AI_PROVIDER=openrouter
AI_API_KEY=sk-or-v1-your-key-here
AI_MODEL=deepseek/deepseek-chat
```

### Google AI

```bash
AI_PROVIDER=google
AI_API_KEY=your-google-api-key
AI_MODEL=gemini-2.5-flash
GOOGLE_AI_TIMEOUT=120
GOOGLE_AI_MAX_RETRIES=4
```

### Groq

```bash
AI_PROVIDER=groq
AI_API_KEY=gsk_your-groq-key-here
AI_MODEL=llama-3.1-8b-instant
```

## Multiple Navidrome libraries

- MagicLists detects multiple music folders by default
- Select libraries in the UI before creating playlists
- Optional: `NAVIDROME_LIBRARY_ID` to target one library in standalone setups

## License

MIT License — see LICENSE file for details.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## Usage analytics

This project may use [Umami Analytics](https://umami.is/) to measure anonymous feature usage (no cookies, no personal data stored).

Public dashboard: [magic-lists analytics](https://umami.itsricky.com/share/kg0XvYPeMM3UsqhO/magic-lists.local)

## Support

- Use the troubleshooting section and System Check page
- Review [Navidrome documentation](https://www.navidrome.org/docs/)
- Open an issue in the repository

## Legal disclaimer

**No warranty:** This software is provided "as is" without warranty of any kind.

**User responsibility:** You are responsible for rights to your music, data sent to third-party AI services, backups, and any playlist or library changes.

**Limitation of liability:** Developers are not liable for data loss, library corruption, or other damages from use of this software.

**Third-party services:** AI integrations are subject to each provider's terms of service.

By using this software, you acknowledge these terms.

---

© 2025 Made by [Synnot Studio](https://synnotstudio.com) — Licensed under the MIT License.
