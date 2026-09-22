"""
sources/anischedule.py — OPTIONAL AnimeSchedule API (token chahiye).

Sirf tab active jab ANISCHEDULE_TOKEN set ho. Iska kaam sirf itna hai:
AniList ke `nextAiringEpisode` ko corroborate karna (kabhi override nahi).

NOTE: ye optional integration hai. Agar aapka token plan endpoints ka shape
alag deta hai to _parse() silently None dega aur card AniList par hi chalega —
koi galat data nahi dikhega.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

import config

log = logging.getLogger("anischedule")


@dataclass
class ScheduleEpisode:
    number: int | None
    air_date: datetime | None
    title: str | None


class AniScheduleSource:
    def __init__(self, token: str | None = None, base_url: str | None = None, timeout: float | None = None):
        self.token = (token if token is not None else config.ANISCHEDULE_TOKEN).strip()
        self.base_url = (base_url if base_url is not None else config.ANISCHEDULE_URL).rstrip("/")
        self.timeout = timeout or config.OPTIONAL_SOURCE_TIMEOUT

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "User-Agent": config.USER_AGENT,
            "Accept": "application/json",
        }

    async def search_id(self, query: str) -> str | None:
        if not self.enabled:
            return None
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=self._headers()) as c:
                resp = await c.get(f"{self.base_url}/animes/search/{query}")
                if resp.status_code != 200:
                    return None
                data = resp.json()
        except Exception as exc:
            log.info("anischedule search fail: %s", exc)
            return None
        rows = data.get("data") if isinstance(data, dict) else data
        if isinstance(rows, list) and rows:
            return rows[0].get("id")
        return None

    async def next_episode(self, query: str) -> ScheduleEpisode | None:
        """Agla episode kab aa raha hai (UTC)."""
        if not self.enabled:
            return None
        anime_id = await self.search_id(query)
        if not anime_id:
            return None
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=self._headers()) as c:
                resp = await c.get(f"{self.base_url}/animes/{anime_id}/episodes")
                if resp.status_code != 200:
                    return None
                data = resp.json()
        except Exception as exc:
            log.info("anischedule episodes fail: %s", exc)
            return None
        rows = data.get("data") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return None
        now = datetime.now(timezone.utc)
        upcoming = []
        for ep in rows:
            if not isinstance(ep, dict):
                continue
            number = ep.get("number") or ep.get("episodeNumber")
            raw = ep.get("airDateTime") or ep.get("airDate") or ep.get("airdate")
            dt = None
            if raw:
                try:
                    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    dt = None
            if dt and dt >= now:
                upcoming.append((dt, int(number) if isinstance(number, (int, float)) else None, ep.get("title")))
        if not upcoming:
            return None
        upcoming.sort(key=lambda x: x[0])
        dt, number, title = upcoming[0]
        return ScheduleEpisode(number=number, air_date=dt, title=title)


source = AniScheduleSource()
