"""YouTube channel RSS scanner — Muse India / Ani-One jaise Hindi dub channels.

YouTube ka public RSS feed use karta hai (koi API key nahi chahiye):
  https://www.youtube.com/feeds/videos.xml?channel_id=UCxxxxx

Config (YOUTUBE_CHANNELS env) mein channel ID ya handle dono chalte hain:
  "@MuseIndia" | "UCabc..." | "https://youtube.com/@Ani-OneAsia"

Handle se channel ID runtime pe resolve hota hai (channel page scrape karke).
"""
import asyncio
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import httpx

import config

# In-memory cache: handle -> channel_id (+ fail hone wale 1 ghante tak skip)
_resolved: dict[str, str] = {}
_failed: dict[str, float] = {}
_FAIL_RETRY = 3600.0  # 1 ghante baad dobara try karenge


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9\s]", "", (s or "").lower()).strip()


def _significant_words(s: str) -> list:
    return [w for w in _norm(s).split() if len(w) > 2]


def extract_episode(title: str) -> int | None:
    """'Black Torch Episode 4 Hindi Dub' / 'EP 4' / 'Ep.4' -> 4"""
    m = re.search(r"(?:episode|ep\.?|part)[\s:#·-]*(\d+)", title, re.IGNORECASE)
    if m:
        n = int(m.group(1))
        if 0 < n <= 3000:
            return n
    return None


def parse_feed(xml_text: str) -> list:
    """RSS feed -> [{title, published, url}]"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for entry in root.findall("a:entry", ns):
        title_el = entry.find("a:title", ns)
        pub_el = entry.find("a:published", ns)
        link_el = entry.find("a:link", ns)
        out.append({
            "title": (title_el.text or "").strip() if title_el is not None else "",
            "published": (pub_el.text or "").strip() if pub_el is not None else None,
            "url": (link_el.get("href") or "").strip() if link_el is not None else "",
        })
    return out


async def _resolve_handle(client: httpx.AsyncClient, entry: str) -> str | None:
    """@handle / URL -> UCxxx channel id (page scrape karke)."""
    entry = entry.strip()
    if entry.startswith("UC"):
        return entry
    if entry.startswith("@"):
        url = f"https://www.youtube.com/{entry}"
    elif entry.startswith("http"):
        url = entry
    else:
        url = f"https://www.youtube.com/@{entry}"
    try:
        r = await client.get(url, timeout=15, follow_redirects=True)
        m = re.search(r'"channelId":"(UC[\w-]{22})"', r.text)
        if m:
            _resolved[entry] = m.group(1)
            return m.group(1)
    except httpx.HTTPError:
        pass
    # Fail negative-cache — har card pe 15s waste na ho
    _failed[entry] = time.time()
    return None


async def _fetch_feed(client: httpx.AsyncClient, channel_id: str) -> list:
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        r = await client.get(url, timeout=15)
        if r.status_code == 200:
            return parse_feed(r.text)
    except httpx.HTTPError:
        pass
    return []


def _matches(query: str, video_title: str) -> bool:
    """Video title anime ke naam se match karta hai?"""
    nq, nt = _norm(query), _norm(video_title)
    if nq and nq in nt:
        return True
    words = _significant_words(query)
    return bool(words) and all(w in nt for w in words)


async def find_episodes(query: str) -> dict | None:
    """Sab configured channels me ye anime dhundo.

    Return: {"channel": "Muse India", "ep": 4, "published": iso,
             "url": str, "video_title": str} | None
    """
    channels = getattr(config, "YOUTUBE_CHANNELS", [])
    if not channels or not query:
        return None
    best = None
    async with httpx.AsyncClient() as client:
        for entry in channels:
            if entry in _resolved:
                cid = _resolved[entry]
            elif _failed.get(entry, 0) and time.time() - _failed[entry] < _FAIL_RETRY:
                continue  # recently fail hua tha — skip, 15s bachao
            else:
                cid = await _resolve_handle(client, entry)
                if not cid:
                    print(f"[youtube] resolve fail: {entry}")
                    continue
            feed = await _fetch_feed(client, cid)
            for vid in feed:
                if not _matches(query, vid["title"]):
                    continue
                ep = extract_episode(vid["title"])
                if ep is None:
                    continue
                # Sirf dubbed/Hindi content hi gino (na ki news/shorts)
                if not re.search(r"hindi|dub|dubbed", vid["title"], re.IGNORECASE):
                    continue
                if best is None or ep > best["ep"]:
                    channel_name = (entry.lstrip("@").split("/")[-1]
                                    or "YouTube")
                    best = {
                        "channel": channel_name,
                        "channel_entry": entry,
                        "ep": ep,
                        "published": vid["published"],
                        "url": vid["url"],
                        "video_title": vid["title"],
                    }
    return best


def channel_to_platform(entry: str) -> str:
    """Channel entry (@handle / UC id / URL) -> display platform naam."""
    from .platforms import canon_platform, PLATFORM_ALIASES
    n = entry.strip().rstrip("/").split("/")[-1].lstrip("@")
    n = n.replace("_", " ").strip().lower()
    # bina space / hyphen wale naam bhi try karo (museindia, ani-one-asia)
    cands = {n, n.replace(" ", ""), n.replace("-", "").replace(" ", "")}
    for c in cands:
        mapped = canon_platform(c)
        if mapped != c or c in PLATFORM_ALIASES:
            return mapped
    return canon_platform(n)
