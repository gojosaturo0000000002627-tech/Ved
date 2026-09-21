"""Anime Dub Bot — configuration (env vars se load hota hai)."""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- Required ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# --- Optional data sources ---
# AnimeSchedule.net ka free API token (account banao -> Settings -> API -> Bearer token)
ANIMESCHEDULE_TOKEN = os.getenv("ANIMESCHEDULE_TOKEN", "")

# Self-hosted anime-dub-info instance ka base URL (Hindi dub episode counts ke liye)
# Example: https://anime-dub-info.onrender.com
DUB_INFO_API = os.getenv("DUB_INFO_API", "").rstrip("/")

# Muse India / Ani-One jaise YouTube channels — Hindi dub episodes ka live source
# Channel ID (UCxxx...) ya handle (@MuseIndia) — dono chalte hain
YOUTUBE_CHANNELS = [
    c.strip() for c in os.getenv("YOUTUBE_CHANNELS",
                                "@MuseIndia,@Ani-OneAsia,@MuseAsia").split(",")
    if c.strip()
]

# --- Bot behaviour ---
# Har itne minute mein followed anime check hoga (Render free tier pe 15+ rakho)
POLL_MINUTES = int(os.getenv("POLL_MINUTES", "20"))
# Card cache TTL seconds (Ek hi anime baar baar fetch na ho)
CACHE_TTL = int(os.getenv("CACHE_TTL", "1800"))  # 30 min — repeat search instant
# Database file
DB_PATH = os.getenv("DB_PATH", os.path.join("data", "bot.db"))
# Manual overrides file (galat data theek karne ke liye)
OVERRIDES_FILE = os.getenv("OVERRIDES_FILE", "overrides.json")

# Web service port (Render inject karta hai)
PORT = int(os.getenv("PORT", "8080"))

TIMEZONE = "Asia/Kolkata"
