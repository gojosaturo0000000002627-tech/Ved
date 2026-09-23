"""
config.py — saare env vars + VERSION + time helpers.

Yahan sirf configuration hai, koi business logic nahi.
Sab kuch env se aata hai taaki Render par bina code change ke tweak ho sake.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# VERSION — har card ke footer me aur /version par yahi dikhega.
# Deploy ke baad turant verify karne ka sabse aasaan tareeka.
# ---------------------------------------------------------------------------
VERSION = "v1.2"
VERSION_LONG = "1.2.0"

# ---------------------------------------------------------------------------
# Time — India ke liye IST fix hai (UTC+5:30, koi DST nahi).
# ---------------------------------------------------------------------------
IST = timezone(timedelta(hours=5, minutes=30))


def now_utc() -> datetime:
    """Abhi ka time (UTC). Tests me BOT_FAKE_NOW set karke freeze kiya ja sakta hai."""
    fake = os.environ.get("BOT_FAKE_NOW", "").strip()
    if fake:
        # Test determinism: '2026-09-22T00:00:00+00:00' ya '2026-09-22'
        try:
            dt = datetime.fromisoformat(fake)
        except ValueError:
            dt = datetime.strptime(fake, "%Y-%m-%d")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return datetime.now(timezone.utc)


def now_ist() -> datetime:
    """Abhi ka time IST me."""
    return now_utc().astimezone(IST)


def ts_ist(dt: datetime | None) -> str:
    """'21 Sep 2026, 05:55 PM IST' — card/notification ka fixed timestamp format."""
    if dt is None:
        return "Unknown"
    return dt.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")


def date_ist_short(dt) -> str:
    """'27 Sep 2026' — dub estimate wale lines ke liye."""
    if dt is None:
        return "Unknown"
    return dt.strftime("%d %b %Y")


# ---------------------------------------------------------------------------
# Secrets / core
# ---------------------------------------------------------------------------
BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_IDS: list[int] = [
    int(x) for x in os.environ.get("ADMIN_IDS", "").replace(";", ",").split(",") if x.strip().isdigit()
]

BASE_DIR = Path(__file__).resolve().parent
DB_PATH: str = os.environ.get("DB_PATH", str(BASE_DIR / "data" / "bot.db"))
OVERRIDES_PATH: str = os.environ.get("OVERRIDES_PATH", str(BASE_DIR / "overrides.json"))

# ---------------------------------------------------------------------------
# Polling / notification engine
# ---------------------------------------------------------------------------
POLL_MINUTES: int = max(5, int(os.environ.get("POLL_MINUTES", "20")))

# ---------------------------------------------------------------------------
# Network timeouts — ek slow source pura card block na kare.
# ---------------------------------------------------------------------------
HTTP_TIMEOUT: float = float(os.environ.get("HTTP_TIMEOUT", "15"))
ANILIST_TIMEOUT: float = float(os.environ.get("ANILIST_TIMEOUT", "12"))
# Render free tier par outbound IP SHARED hota hai — AniList 90 req/min poori
# IP family par lagti hai. Isliye hum throttle karte hain aur 429 par short wait.
ANILIST_MIN_INTERVAL: float = float(os.environ.get("ANILIST_MIN_INTERVAL", "0.8"))
ANILIST_RATELIMIT_MAX_WAIT: float = float(os.environ.get("ANILIST_RATELIMIT_MAX_WAIT", "35"))
DUB_LOOKUP_TIMEOUT: float = float(os.environ.get("DUB_LOOKUP_TIMEOUT", "30"))  # 25-40s cap
YOUTUBE_TIMEOUT: float = float(os.environ.get("YOUTUBE_TIMEOUT", "15"))
OPTIONAL_SOURCE_TIMEOUT: float = float(os.environ.get("OPTIONAL_SOURCE_TIMEOUT", "15"))
CARD_BUILD_TIMEOUT: float = float(os.environ.get("CARD_BUILD_TIMEOUT", "40"))

# ---------------------------------------------------------------------------
# Cache TTLs
# ---------------------------------------------------------------------------
ENTRY_CACHE_TTL: int = int(os.environ.get("ENTRY_CACHE_TTL", str(15 * 60)))      # AniList entry: 15 min
CARD_CACHE_TTL: int = int(os.environ.get("CARD_CACHE_TTL", str(30 * 60)))        # card: 30 min (SQLite)
ANINIDHI_TTL: int = int(os.environ.get("ANINIDHI_TTL", str(6 * 3600)))           # list_all: 6 ghante
YT_NEGATIVE_TTL: int = int(os.environ.get("YT_NEGATIVE_TTL", "3600"))            # fail handle: 1 ghanta skip
YT_FEED_TTL: int = int(os.environ.get("YT_FEED_TTL", str(20 * 60)))              # RSS feed: 20 min

USER_AGENT: str = os.environ.get(
    "USER_AGENT",
    "AnimeDubBot/1.0 (+https://github.com/yourname/anime-dub-bot) python-telegram-bot",
)

# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------
ANILIST_URL: str = os.environ.get("ANILIST_URL", "https://graphql.anilist.co")

# Optional: self-hosted anime-dub-info API. Khali = disabled.
DUBINFO_URL: str = os.environ.get("DUBINFO_URL", "").strip().rstrip("/")
# Optional: AnimeSchedule API (token chahiye). Khali = disabled.
ANISCHEDULE_TOKEN: str = os.environ.get("ANISCHEDULE_TOKEN", "").strip()
ANISCHEDULE_URL: str = os.environ.get("ANISCHEDULE_URL", "https://api.anime-schedule.net/v4")

# YouTube channels — handle ya direct channel_id dono chalega.
# NOTE: @MuseIndia 404 deta hai (galat handle) — isliye negative cache zaroori hai.
_DEFAULT_YT = [
    {"name": "Muse India", "handle": "MuseIndia", "channel_id": "UCYYhAzgWuxPauRXdPpLAX3Q"},
    {"name": "Muse Asia", "handle": "MuseAsia"},
    {"name": "Ani-One Asia", "handle": "AniOneAsia"},
    {"name": "Ani-One India", "handle": "AniOneIndia"},
]


def youtube_channels() -> list[dict]:
    """Env YOUTUBE_CHANNELS (JSON list) ya default list."""
    raw = os.environ.get("YOUTUBE_CHANNELS", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [c for c in parsed if isinstance(c, dict)]
        except json.JSONDecodeError:
            pass
    return list(_DEFAULT_YT)


# ---------------------------------------------------------------------------
# Search / season behaviour
# ---------------------------------------------------------------------------
MAX_SEASON_CHAIN: int = int(os.environ.get("MAX_SEASON_CHAIN", "5"))   # BFS depth cap
MAX_PICK_LIST: int = int(os.environ.get("MAX_PICK_LIST", "8"))         # pick list size
MAX_EXTRA_LIST: int = int(os.environ.get("MAX_EXTRA_LIST", "5"))       # movies/specials dikhane ki limit
FUZZY_CUTOFF: float = float(os.environ.get("FUZZY_CUTOFF", "0.80"))    # ~80% word similarity

# ---------------------------------------------------------------------------
# Web service (Render free tier)
# ---------------------------------------------------------------------------
PORT: int = int(os.environ.get("PORT", "8080"))
HOST: str = os.environ.get("HOST", "0.0.0.0")
# UptimeRobot ko yaad dilane ke liye — Render 15 min baad service sula deta hai.
HEALTH_PATH: str = "/healthz"


def public_stats() -> dict:
    """/stats endpoint ka default payload (db numbers handlers/main me bharte hain)."""
    return {
        "status": "ok",
        "version": VERSION,
        "version_long": VERSION_LONG,
        "poll_minutes": POLL_MINUTES,
        "ist_now": ts_ist(now_ist()),
    }
