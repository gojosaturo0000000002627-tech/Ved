"""
sources/anilist.py — AniList GraphQL client (no API key, 90 req/min).

ZAROORI NOTE (real world me verify kiya gaya):
    AniList bina `User-Agent` header ke **403 Forbidden** deta hai.
    Isliye har request me UA bhejte hain.

Features:
    * 15-min in-memory entry cache (speed promise)
    * Page(id_in: [...]) batch query -> level-wise BFS me 2-3 request
    * 429 rate-limit par retry (X-RateLimit-Remaining / Retry-After dekhte hue)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

import config

log = logging.getLogger("anilist")

# --- GraphQL: search / get / many sab ek hi shape me, cache key alag ---------
SEARCH_QUERY = """
query ($search: String, $perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    media(search: $search, type: ANIME, sort: POPULARITY_DESC) { ...MediaFields }
  }
}
"""

MANY_QUERY = """
query ($ids: [Int], $perPage: Int) {
  Page(page: 1, perPage: $perPage) {
    media(id_in: $ids, type: ANIME) { ...MediaFields }
  }
}
"""

FRAGMENT = """
fragment MediaFields on Media {
  id
  format
  status
  episodes
  duration
  season
  seasonYear
  isAdult
  countryOfOrigin
  startDate { year month day }
  endDate { year month day }
  title { english romaji native }
  synonyms
  nextAiringEpisode { episode airingAt timeUntilAiring }
  externalLinks { url site type language }
  relations {
    edges {
      relationType(version: 2)
      node {
        ... on Media {
          id
          format
          status
          episodes
          seasonYear
          title { english romaji native }
        }
      }
    }
  }
}
"""


@dataclass
class MediaEntry:
    """AniList media ka normalized form."""

    id: int
    format: str | None = None            # TV / MOVIE / SPECIAL / OVA / ONA / TV_SHORT
    status: str | None = None            # RELEASING / FINISHED / NOT_YET_RELEASED / CANCELLED / HIATUS
    episodes: int | None = None
    duration: int | None = None          # minutes per episode
    season: str | None = None
    season_year: int | None = None
    start_date: dict = field(default_factory=dict)   # {year, month, day}
    end_date: dict = field(default_factory=dict)
    english: str | None = None
    romaji: str | None = None
    native: str | None = None
    synonyms: list[str] = field(default_factory=list)
    next_airing: dict | None = None      # {episode, airingAt, timeUntilAiring}
    streaming: list[dict] = field(default_factory=list)   # [{site, url}]
    relations: list[dict] = field(default_factory=list)   # [{type, id, format, status, episodes, romaji}]
    fetched_at: float = 0.0

    # -- helpers -------------------------------------------------------------
    @property
    def best_title(self) -> str:
        return self.english or self.romaji or self.native or f"#{self.id}"

    @property
    def year(self) -> int | None:
        return (self.start_date or {}).get("year")

    def titles(self) -> list[str]:
        """Sab title variants (order matter karta hai: English -> Romaji -> Native)."""
        out: list[str] = []
        for t in (self.english, self.romaji, self.native, *self.synonyms):
            if t and t.strip() and t not in out:
                out.append(t.strip())
        return out


def _parse_media(raw: dict[str, Any]) -> MediaEntry:
    """GraphQL JSON -> MediaEntry."""
    t = raw.get("title") or {}
    streaming = [
        {"site": l.get("site"), "url": l.get("url"), "language": l.get("language")}
        for l in (raw.get("externalLinks") or [])
        if l.get("type") == "STREAMING" and l.get("site")
    ]
    relations = []
    for edge in ((raw.get("relations") or {}).get("edges") or []):
        node = edge.get("node") or {}
        if not node.get("id"):
            continue
        relations.append(
            {
                "type": edge.get("relationType"),
                "id": node["id"],
                "format": node.get("format"),
                "status": node.get("status"),
                "episodes": node.get("episodes"),
                "season_year": node.get("seasonYear"),
                "romaji": ((node.get("title") or {}).get("romaji")) or "",
                "english": ((node.get("title") or {}).get("english")) or "",
            }
        )
    return MediaEntry(
        id=raw["id"],
        format=raw.get("format"),
        status=raw.get("status"),
        episodes=raw.get("episodes"),
        duration=raw.get("duration"),
        season=raw.get("season"),
        season_year=raw.get("seasonYear"),
        start_date=raw.get("startDate") or {},
        end_date=raw.get("endDate") or {},
        english=t.get("english"),
        romaji=t.get("romaji"),
        native=t.get("native"),
        synonyms=[s for s in (raw.get("synonyms") or []) if s],
        next_airing=raw.get("nextAiringEpisode"),
        streaming=streaming,
        relations=relations,
        fetched_at=time.time(),
    )


class AniListClient:
    """Chhota async GraphQL client + cache."""

    def __init__(self, url: str | None = None, timeout: float | None = None):
        self.url = url or config.ANILIST_URL
        self.timeout = timeout or config.ANILIST_TIMEOUT
        self._entry_cache: dict[int, MediaEntry] = {}     # id -> entry (15 min)
        self._search_cache: dict[str, tuple[float, list[MediaEntry]]] = {}
        self._client: httpx.AsyncClient | None = None
        self.requests_made = 0

    # -- lifecycle -----------------------------------------------------------
    async def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    # Bina UA ke AniList 403 deta hai — ye line zaroori hai.
                    "User-Agent": config.USER_AGENT,
                },
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    def clear_cache(self) -> None:
        self._entry_cache.clear()
        self._search_cache.clear()

    # -- core POST -----------------------------------------------------------
    async def _post(self, query: str, variables: dict, retries: int = 3) -> dict:
        full_query = query + FRAGMENT
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                c = await self.client()
                self.requests_made += 1
                resp = await c.post(self.url, json={"query": full_query, "variables": variables})
                if resp.status_code == 429:
                    # rate limit: Retry-After ya default 65s (AniList 90 req/min)
                    wait = float(resp.headers.get("Retry-After", "65") or 65)
                    remaining = resp.headers.get("X-RateLimit-Remaining")
                    log.warning("AniList 429 (remaining=%s), %.1fs ruk rahe hain", remaining, wait)
                    await asyncio.sleep(min(wait, 70))
                    continue
                resp.raise_for_status()
                payload = resp.json()
                if payload.get("errors"):
                    raise RuntimeError(f"AniList GraphQL error: {payload['errors'][:1]}")
                return payload.get("data") or {}
            except (httpx.HTTPError, asyncio.TimeoutError, RuntimeError) as exc:
                last_err = exc
                log.warning("AniList attempt %d/%d fail: %s", attempt + 1, retries, exc)
                await asyncio.sleep(0.6 * (attempt + 1))
        raise ConnectionError(f"AniList se data nahi mil paya: {last_err}")

    # -- public API ----------------------------------------------------------
    def cached_entry(self, anime_id: int) -> MediaEntry | None:
        e = self._entry_cache.get(anime_id)
        if e and (time.time() - e.fetched_at) < config.ENTRY_CACHE_TTL:
            return e
        return None

    async def search(self, query: str, per_page: int = 12) -> list[MediaEntry]:
        """Title search (POPULARITY_DESC). 15 min cache."""
        key = query.strip().lower()
        hit = self._search_cache.get(key)
        if hit and (time.time() - hit[0]) < config.ENTRY_CACHE_TTL:
            return hit[1]
        data = await self._post(SEARCH_QUERY, {"search": query.strip(), "perPage": per_page})
        rows = ((data.get("Page") or {}).get("media")) or []
        entries = [_parse_media(r) for r in rows if r.get("id")]
        for e in entries:
            self._entry_cache[e.id] = e
        self._search_cache[key] = (time.time(), entries)
        return entries

    async def many(self, ids: list[int], force: bool = False) -> list[MediaEntry]:
        """BATCH: Page(id_in: [...]) — ek hi request me multiple anime. force=True par cache bypass."""
        wanted = [i for i in dict.fromkeys(ids) if i]
        if not wanted:
            return []
        fresh = [] if force else [self._entry_cache[i] for i in wanted if self.cached_entry(i)]
        missing = wanted if force else [i for i in wanted if not self.cached_entry(i)]
        if not missing:
            return [self._entry_cache[i] for i in wanted if i in self._entry_cache]
        out: list[MediaEntry] = []
        # AniList perPage max 50; chunks me tod dete hain
        for i in range(0, len(missing), 50):
            chunk = missing[i : i + 50]
            data = await self._post(MANY_QUERY, {"ids": chunk, "perPage": 50})
            rows = ((data.get("Page") or {}).get("media")) or []
            out.extend(_parse_media(r) for r in rows if r.get("id"))
        for e in out:
            self._entry_cache[e.id] = e
        merged = {e.id: e for e in fresh + out}
        return [merged[i] for i in wanted if i in merged]

    async def get(self, anime_id: int, force: bool = False) -> MediaEntry | None:
        if not force:
            cached = self.cached_entry(anime_id)
            if cached:
                return cached
        got = await self.many([anime_id], force=True)
        return got[0] if got else None


# module-level singleton (poore bot me ek hi client + ek hi cache)
client = AniListClient()
