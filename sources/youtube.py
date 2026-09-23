"""
sources/youtube.py — YouTube RSS scanner (no API key).

Do kaam:
  1) @handle -> channel_id resolve (channel page scrape)
  2) RSS feed se episode numbers nikaalna ("BLACK TORCH - Episode 08 [EN Sub]" etc.)

ZAROORI (real world me verify kiya):
    Channel page par sabse pehla `"channelId":"UC..."` **related channel** hota hai,
    khud ka nahi. Isliye hum `externalId` / <link rel=canonical> se nikalte hain.
    (Galat tareeke se @AniOneAsia ka Traditional-Chinese channel mil jaata hai.)

Speed:
    * success -> permanently cache
    * failure -> 1 ghante ke liye negative cache (warna har card ~45s waste karega)
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import httpx

import config

log = logging.getLogger("youtube")

_NS = {"atom": "http://www.w3.org/2005/Atom"}

_RE_EXTERNAL_ID = re.compile(r'"externalId"\s*:\s*"(UC[\w-]{22})"')
_RE_CANONICAL = re.compile(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"')
_RE_OG_CHANNEL = re.compile(r'youtube\.com/channel/(UC[\w-]{22})')
_RE_EPISODE = re.compile(r"\bepisode\s*(\d{1,4})", re.I)
_RE_SEASON_EP = re.compile(r"\b\(?\bs?(\d{1,2})\s*e\s*(\d{1,4})\)?", re.I)
_RE_SEASON_ONLY = re.compile(r"\(\s*s(\d{1,2})\s*\)", re.I)
_RE_HASH_EP = re.compile(r"(?:^|\s)#(\d{1,3})(?:\s|$)")
_RE_HINDI = re.compile(r"hindi\s*dub|\[hindi|\(hindi|hindi\b", re.I)
_RE_LANGUAGE_TAG = re.compile(r"\[([^\]]{2,30})\]", re.I)


@dataclass
class YTVideo:
    video_id: str
    title: str
    published: str
    link: str
    channel: str = ""

    @property
    def is_hindi(self) -> bool:
        return bool(_RE_HINDI.search(self.title))

    def episode(self) -> tuple[int | None, int | None]:
        """(episode_number, season_number) — title se nikaala, na mile to (None, None)."""
        season: int | None = None
        ep: int | None = None
        sm = _RE_SEASON_EP.search(self.title)
        if sm:
            season, ep = int(sm.group(1)), int(sm.group(2))
        else:
            # "(S3)" jaisa standalone season marker
            so = _RE_SEASON_ONLY.search(self.title)
            if so:
                season = int(so.group(1))
        em = _RE_EPISODE.search(self.title)
        if em:
            # "Episode 22 (S2E10)": absolute number zyada reliable hai jab season match na ho
            ep_abs = int(em.group(1))
            ep = ep_abs if ep is None else ep
        if ep is None:
            hm = _RE_HASH_EP.search(self.title)
            if hm:
                ep = int(hm.group(1))
        return ep, season


@dataclass
class YTChannel:
    name: str
    handle: str | None
    channel_id: str | None


class YouTubeSource:
    def __init__(self, store=None, timeout: float | None = None):
        """
        store: optional persistent cache (get/set) — successes permanent, failures 1h.
        """
        self.store = store
        self.timeout = timeout or config.YOUTUBE_TIMEOUT
        self._client: httpx.AsyncClient | None = None
        self._feed_mem: dict[str, tuple[float, list[YTVideo]]] = {}
        self._res_mem: dict[str, tuple[float, str | None]] = {}

    async def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": config.USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def __aenter__(self) -> "YouTubeSource":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    # -- cache helpers -------------------------------------------------------
    def _cache_get(self, key: str) -> str | None:
        hit = self._res_mem.get(key)
        if hit:
            ts, val = hit
            # value '@' se start = negative entry
            if val is None:
                if (time.time() - ts) < config.YT_NEGATIVE_TTL:
                    return None
                return "__MISS__"
            return val
        if self.store is not None:
            try:
                v = self.store.get(key)
            except Exception:
                v = None
            if v is not None:
                self._res_mem[key] = (time.time(), v)
                return v
            if self.store.expired_negative(key):
                self._res_mem[key] = (time.time(), None)
                return None
        return "__MISS__"

    def _cache_set(self, key: str, value: str | None) -> None:
        self._res_mem[key] = (time.time(), value)
        if self.store is not None:
            try:
                self.store.set(key, value, None if value else config.YT_NEGATIVE_TTL)
            except Exception:
                pass

    # -- handle resolution ---------------------------------------------------
    async def channel_id_for(self, channel: dict) -> str | None:
        """Config me channel_id diya hai to wahi, warna @handle resolve karo."""
        cid = (channel.get("channel_id") or "").strip()
        if cid.startswith("UC"):
            return cid
        handle = (channel.get("handle") or "").strip().lstrip("@")
        if not handle:
            return None
        key = f"yt:handle:{handle.lower()}"
        cached = self._cache_get(key)
        if cached and cached != "__MISS__":
            return cached
        if cached is None:
            # negative cache active — 1 ghanta skip (har card 45s waste na ho)
            log.debug("YT handle %s negative-cached, skip", handle)
            return None
        try:
            cid = await self._resolve_handle(handle)
        except Exception as exc:
            log.info("YT handle resolve fail @%s: %s", handle, exc)
            self._cache_set(key, None)
            return None
        if cid:
            self._cache_set(key, cid)  # success -> permanent
        else:
            self._cache_set(key, None)  # failure -> 1 ghanta
        return cid

    async def _resolve_handle(self, handle: str) -> str | None:
        url = f"https://www.youtube.com/@{urllib.parse.quote(handle)}"
        c = await self.client()
        resp = await c.get(url)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        html = resp.text
        for rx in (_RE_EXTERNAL_ID, _RE_CANONICAL, _RE_OG_CHANNEL):
            m = rx.search(html)
            if m:
                return m.group(1)
        return None

    # -- RSS -----------------------------------------------------------------
    async def feed(self, channel_id: str) -> list[YTVideo]:
        if not channel_id:
            return []
        hit = self._feed_mem.get(channel_id)
        if hit and (time.time() - hit[0]) < config.YT_FEED_TTL:
            return hit[1]
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        c = await self.client()
        resp = await c.get(url)
        resp.raise_for_status()
        videos = self._parse_feed(resp.text, channel_id)
        self._feed_mem[channel_id] = (time.time(), videos)
        return videos

    def _parse_feed(self, xml_text: str, channel_id: str) -> list[YTVideo]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            log.warning("YT feed parse fail: %s", exc)
            return []
        channel_title = (root.findtext("atom:title", default="", namespaces=_NS) or "").strip()
        out: list[YTVideo] = []
        for entry in root.findall("atom:entry", _NS):
            vid = entry.findtext("yt:videoId", default="", namespaces={"yt": "http://www.youtube.com/xml/schemas/2015"}) or ""
            title = (entry.findtext("atom:title", default="", namespaces=_NS) or "").strip()
            published = (entry.findtext("atom:published", default="", namespaces=_NS) or "").strip()
            link_el = entry.find("atom:link", _NS)
            link = link_el.get("href") if link_el is not None else ""
            if title:
                out.append(YTVideo(video_id=vid, title=title, published=published, link=link, channel=channel_title or channel_id))
        return out

    # -- scan ----------------------------------------------------------------
    async def scan(self, titles: list[str], season: int | None = None, hindi_only: bool = True) -> list[dict]:
        """
        Config wale saare channels scan karke anime ke Hindi dub episodes dhundho.
        Har source par timeout — ek slow channel pura card block na kare.
        """
        channels = config.youtube_channels()
        tasks = [asyncio.create_task(self._scan_channel(ch, titles, season, hindi_only)) for ch in channels]
        results: list[dict] = []
        for t in tasks:
            try:
                results.extend(await asyncio.wait_for(t, timeout=self.timeout + 5))
            except asyncio.TimeoutError:
                log.info("YT channel scan timeout, skip")
            except Exception as exc:
                log.info("YT channel scan fail: %s", exc)
        return results

    async def _scan_channel(self, channel: dict, titles: list[str], season: int | None, hindi_only: bool) -> list[dict]:
        cid = await self.channel_id_for(channel)
        if not cid:
            return []
        videos = await self.feed(cid)
        needles = [self._norm(t) for t in titles if t]
        needles = [n for n in needles if len(n) >= 4]
        out: list[dict] = []
        for v in videos:
            if hindi_only and not v.is_hindi:
                continue
            vnorm = self._norm(v.title)
            if not any(n in vnorm for n in needles):
                continue
            ep, ep_season = v.episode()
            if season is not None and ep_season is not None and ep_season != season:
                continue  # doosre season ka episode, ignore
            if ep is None:
                continue
            out.append(
                {
                    "channel": channel.get("name") or v.channel,
                    "channel_id": cid,
                    "title": v.title,
                    "episode": ep,
                    "season": ep_season,
                    "published": v.published,
                    "url": v.link,
                    "is_hindi": v.is_hindi,
                }
            )
        return out

    @staticmethod
    def _norm(text: str) -> str:
        return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).strip()


# module-level singleton
source = YouTubeSource()
