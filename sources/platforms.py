"""Platform naam normalize karna — user ki list ke hisaab se."""

PLATFORM_ALIASES = {
    "crunchyroll": "Crunchyroll",
    "crunchyroll channel": "Crunchyroll Channel",
    "crunchyroll-nexus": "Crunchyroll",
    "netflix": "Netflix",
    "amazon": "Amazon Prime Video",
    "amazon prime video": "Amazon Prime Video",
    "prime video": "Amazon Prime Video",
    "amazon prime": "Amazon Prime Video",
    "anime times": "Anime Times (Prime Video Channel)",
    "anime-times": "Anime Times (Prime Video Channel)",
    "hotstar": "JioHotstar",
    "disney": "JioHotstar",
    "disney+": "JioHotstar",
    "disney plus": "JioHotstar",
    "jiohotstar": "JioHotstar",
    "jiocinema": "JioHotstar",
    "mx player": "MX Player / MX Player by Prime Video",
    "mxplayer": "MX Player / MX Player by Prime Video",
    "mx": "MX Player / MX Player by Prime Video",
    "zee5": "ZEE5",
    "zee5.com": "ZEE5",
    "muse india": "Muse India (YouTube)",
    "muse-india": "Muse India (YouTube)",
    "muse asia": "Muse Asia (YouTube)",
    "muse-asia": "Muse Asia (YouTube)",
    "museasia": "Muse Asia (YouTube)",
    "ani-one": "Ani-One India (YouTube)",
    "anione": "Ani-One India (YouTube)",
    "ani-one asia": "Ani-One India (YouTube)",
    "youtube": "YouTube",
    "hidive": "HIDIVE",
    "hulu": "Hulu",
    "bilibili": "Bilibili TV",
    "bilibili tv": "Bilibili TV",
    "iqiyi": "iQIYI",
    "voot": "Voot",
    "sonyliv": "SonyLIV",
    "aot india": "AOT India (YouTube)",
    "muse": "Muse Asia (YouTube)",
    "museindia": "Muse India (YouTube)",
    "anioneasia": "Ani-One India (YouTube)",
    "ani-one-asia": "Ani-One India (YouTube)",
    "aotindia": "AOT India (YouTube)",
    "aot india": "AOT India (YouTube)",
}

# Ye platforms India-focused hain (region India dikhayenge)
INDIA_PLATFORMS = {
    "JioHotstar", "ZEE5", "MX Player / MX Player by Prime Video",
    "Anime Times (Prime Video Channel)", "Muse India (YouTube)",
    "Ani-One India (YouTube)", "AOT India (YouTube)",
    "Muse Asia (YouTube)",
}


def canon_platform(name: str) -> str:
    """Kisi bhi source ka platform naam canonical display naam mein badlo."""
    if not name:
        return ""
    key = name.strip().lower()
    return PLATFORM_ALIASES.get(key, name.strip())


def platform_region(display_name: str) -> str:
    """India-focused platform ke liye 'India', warna 'Global'."""
    return "India" if display_name in INDIA_PLATFORMS else "Global"
