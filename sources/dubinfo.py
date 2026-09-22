"""
sources/dubinfo.py — OPTIONAL self-hosted anime-dub-info API.

Sirf tab active jab DUBINFO_URL set ho. Warna `enabled` False aur sab kuch khaali,
taki card bina kisi extra latency ke ban jaaye.

Expected response (defensively parse hota hai — koi bhi field missing ho sakta hai):
    GET {DUBINFO_URL}/anime/{query}
    {
      "results": [
        {"title": "Black Torch",
         "dubs": [{"language": "Hindi", "episodes": 4, "platform": "Crunchyroll",
                   "next_episode": "2026-09-26", "url": "..."}]}
      ]
    }
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import httpx

import config
from sources import platforms

log = logging.getLogger("dubinfo")

_LANGS = ("hindi", "english", "japanese")


@dataclass
class DubInfoResult:
    language: str                    # 'hindi' | 'english' | 'japanese'
    episodes: int | None
    platform: str | None
    url: str | None
    next_episode: date | None


class DubInfoSource:
    """Self-hosted dub API ka patla wrapper. Unconfigured = no-op."""

    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        self.base_url = (base_url if base_url is not None else config.DUBINFO_URL).rstrip("/")
        self.timeout = timeout or config.OPTIONAL_SOURCE_TIMEOUT

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    async def lookup(self, titles: list[str]) -> list[DubInfoResult]:
        """English -> Romaji -> Native variants try karo. Fail = khaali list."""
        if not self.enabled:
            return []
        for title in titles[:3]:
            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout, headers={"User-Agent": config.USER_AGENT}
                ) as c:
                    resp = await c.get(f"{self.base_url}/anime/{title}")
                    if resp.status_code != 200:
                        continue
                    parsed = self._parse(resp.json())
            except Exception as exc:
                log.info("dubinfo fail (%s): %s", title, exc)
                continue
            if parsed:
                return parsed
        return []

    @staticmethod
    def _parse(payload) -> list[DubInfoResult]:
        """JSON -> DubInfoResult list. Unknown shape = khaali list (kabhi guess nahi)."""
        if isinstance(payload, dict):
            results = payload.get("results")
            if not isinstance(results, list):
                results = [payload] if payload.get("dubs") else []
        else:
            return []

        out: list[DubInfoResult] = []
        for row in results:
            if not isinstance(row, dict):
                continue
            for dub in row.get("dubs") or []:
                if not isinstance(dub, dict):
                    continue
                lang = str(dub.get("language") or "").strip().lower()
                if lang not in _LANGS:
                    continue
                eps = dub.get("episodes")
                raw_next = dub.get("next_episode") or dub.get("next")
                try:
                    next_date = date.fromisoformat(str(raw_next)[:10]) if raw_next else None
                except (ValueError, TypeError):
                    next_date = None
                out.append(
                    DubInfoResult(
                        language=lang,
                        episodes=int(eps) if isinstance(eps, (int, float)) else None,
                        platform=platforms.display(dub.get("platform")) if dub.get("platform") else None,
                        url=dub.get("url"),
                        next_episode=next_date,
                    )
                )
        return out


source = DubInfoSource()
