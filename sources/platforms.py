"""
sources/platforms.py — platform naam ka canon + India region map.

Do alag vocabularies aati hain:
  * AniList `externalLinks.site`  -> "Crunchyroll", "Amazon Prime Video", "iQ", "Bilibili TV" ...
  * AniNidhi `hindi_dubs.platform` -> "Crunchyroll", "Netflix", "Muse India", "Anime Times (Prime Video)"

Inhe ek canonical key par laate hain taaki "same platform, 2 naam" wali galti na ho.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import urllib.parse

# canonical key -> (display name, aliases)
# India me jo platforms Hindi/English anime dete hain, sab yahan hain.
PLATFORMS: dict[str, dict] = {
    "crunchyroll": {
        "display": "Crunchyroll",
        "aliases": ["crunchyroll", "crunchy roll", "cr"],
        "search": "https://www.crunchyroll.com/search?q={q}",
        "india": True,
    },
    "netflix": {
        "display": "Netflix",
        "aliases": ["netflix", "netflix india"],
        "search": "https://www.netflix.com/search?q={q}",
        "india": True,
    },
    "prime-video": {
        "display": "Amazon Prime Video",
        "aliases": [
            "amazon prime video", "prime video", "amazon prime", "primevideo",
            "amazon", "amazon mini tv", "anime times (prime video)",
            "anime times prime video", "prime",
        ],
        "search": "https://www.primevideo.com/search/ref=atv_nb_sr?phrase={q}",
        "india": True,
    },
    "anime-times": {
        "display": "Anime Times",
        "aliases": ["anime times", "animetimes", "anime times (prime video)"],
        "search": "https://www.primevideo.com/search/ref=atv_nb_sr?phrase={q}",
        "india": True,
    },
    "jiohotstar": {
        "display": "JioHotstar",
        "aliases": ["jiohotstar", "hotstar", "disney+ hotstar", "disney plus hotstar", "star india"],
        "search": "https://www.hotstar.com/in/search?q={q}",
        "india": True,
    },
    "mx-player": {
        "display": "MX Player",
        "aliases": ["mx player", "mxplayer", "mx"],
        "search": "https://www.mxplayer.in/search/web?searchKeyword={q}",
        "india": True,
    },
    "zee5": {
        "display": "ZEE5",
        "aliases": ["zee5", "zee 5", "zee"],
        "search": "https://www.zee5.com/global/search?search_keywords={q}",
        "india": True,
    },
    "muse-india": {
        "display": "Muse India",
        "aliases": ["muse india", "muse in", "muse india hindi"],
        "search": "https://www.youtube.com/@MuseIndia/search?query={q}",
        "india": True,
    },
    "muse-asia": {
        "display": "Muse Asia",
        "aliases": ["muse asia", "muse"],
        "search": "https://www.youtube.com/@MuseAsia/search?query={q}",
        "india": True,
    },
    "ani-one-india": {
        "display": "Ani-One India",
        "aliases": ["ani one india", "ani-one india", "anione india"],
        "search": "https://www.youtube.com/results?search_query={q}+ani+one+india",
        "india": True,
    },
    "ani-one-asia": {
        "display": "Ani-One Asia",
        "aliases": ["ani one asia", "ani-one asia", "ani-one", "anione"],
        "search": "https://www.youtube.com/results?search_query={q}+ani+one+asia",
        "india": True,
    },
    # ---- India me generally available nahi, par AniList inhe bhejta hai ----
    "hulu": {"display": "Hulu", "aliases": ["hulu"], "search": "https://www.hulu.com/search?q={q}", "india": False},
    "iqiyi": {"display": "iQIYI", "aliases": ["iq", "iqiyi", "iqi"], "search": "https://www.iq.com/search?keyword={q}", "india": False},
    "bilibili": {"display": "Bilibili", "aliases": ["bilibili", "bilibili tv", "bili"], "search": "https://www.bilibili.tv/en/search?keyword={q}", "india": False},
    "wetv": {"display": "WeTV", "aliases": ["wetv", "we tv"], "search": "https://wetv.vip/en/search?keyword={q}", "india": False},
    "youtube": {"display": "YouTube", "aliases": ["youtube", "yt"], "search": "https://www.youtube.com/results?search_query={q}", "india": True},
    "tubi": {"display": "Tubi", "aliases": ["tubi", "tubi tv"], "search": "https://tubitv.com/search/{q}", "india": False},
    "adult-swim": {"display": "Adult Swim", "aliases": ["adult swim"], "search": "https://www.adultswim.com/search?q={q}", "india": False},
    "hoopla": {"display": "Hoopla", "aliases": ["hoopla"], "search": "https://www.hoopladigital.com/search?q={q}", "india": False},
    "sony-crackle": {"display": "Crackle", "aliases": ["crackle"], "search": "https://www.crackle.com/search?q={q}", "india": False},
}

_ALIAS_INDEX: dict[str, str] = {}
for _key, _meta in PLATFORMS.items():
    for _alias in _meta["aliases"]:
        _ALIAS_INDEX[_alias] = _key
    _ALIAS_INDEX[_meta["display"].lower()] = _key


def canon(name: str | None) -> str | None:
    """'Anime Times (Prime Video)' -> 'anime-times' ; unknown -> None."""
    if not name:
        return None
    raw = str(name).strip().lower()
    if not raw:
        return None
    if raw in _ALIAS_INDEX:
        return _ALIAS_INDEX[raw]
    # "anime times (prime video)" jaise compound naam: sabse specific alias jeet-ta hai
    best: tuple[int, str] | None = None
    for alias, key in _ALIAS_INDEX.items():
        if alias in raw and len(alias) >= 4:
            if best is None or len(alias) > best[0]:
                best = (len(alias), key)
    if best:
        return best[1]
    # last resort: punct hata ke dobara
    squeezed = re.sub(r"[^a-z0-9]+", " ", raw).strip()
    return _ALIAS_INDEX.get(squeezed)


def display(name: str | None) -> str:
    """Display naam — canon mil gaya to uska, warna jo diya wahi (kabhi guess nahi)."""
    key = canon(name)
    if key:
        return PLATFORMS[key]["display"]
    return (name or "Unknown").strip()


def is_india_available(name: str | None) -> bool:
    """Ye platform India me chalta hai ya nahi (card ke 'India' tag ke liye)."""
    key = canon(name)
    if not key:
        return False
    return bool(PLATFORMS[key].get("india"))


@dataclass
class PlatformLink:
    key: str
    display_name: str
    url: str | None
    india: bool


def build_link(name: str | None, query: str, known_url: str | None = None) -> PlatformLink:
    """Platform ka watch link. Real URL (AniList) mile to wahi, warna official search URL."""
    key = canon(name) or ""
    disp = display(name)
    url = known_url
    if not url:
        meta = PLATFORMS.get(key)
        if meta:
            url = meta["search"].format(q=urllib.parse.quote_plus(query or disp))
    return PlatformLink(key=key, display_name=disp, url=url, india=is_india_available(name))


def dedupe_preserve(names: list[str]) -> list[str]:
    """Canonical level par duplicate hatao, order wahi rakho."""
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if not n:
            continue
        key = canon(n) or n.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(display(n))
    return out
