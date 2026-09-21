"""AnimeSchedule.net API v3 — dub premieres, air times, streaming platforms.

Docs: https://animeschedule.net/api/v3/documentation
Token chahiye: account banao -> Settings -> API -> app create -> Bearer token.
Token na ho to ye source skip ho jata hai (bot AniList + dub-info se kaam chalata hai).
"""
import asyncio

import httpx

BASE = "https://animeschedule.net/api/v3"

STATUS_MAP = {"Finished": "Completed ✅ (sab episodes release ho chuke)",
              "Ongoing": "Ongoing", "Delayed": "Delayed (rukka hua)",
              "Upcoming": "Upcoming (abhi shuru nahi hua)"}


def _headers(token: str) -> dict:
    h = {"Accept": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def _get(path: str, params: dict | None, token: str):
    if not token:
        return None  # token ke bina private endpoint 401 deta hai
    last_err = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(BASE + path, params=params,
                                     headers=_headers(token), timeout=20)
                if r.status_code in (401, 403):
                    return None  # token galat — silently skip
                r.raise_for_status()
                return r.json()
        except (httpx.HTTPError, ValueError) as e:
            last_err = e
            await asyncio.sleep(2 * (attempt + 1))
    if last_err:
        print(f"[anischedule] request failed: {last_err}")
    return None


def _parse(a: dict) -> dict:
    """AnimeSchedule anime object -> unified dict."""
    websites = a.get("websites") or {}
    streams = []
    for s in websites.get("streams") or []:
        label = s.get("label") or ""
        streams.append({
            "platform": s.get("platform") or s.get("site"),
            "url": s.get("url"),
            "label": label,
            "is_dub": "dub" in label.lower(),
        })
    return {
        "slug": a.get("route"),
        "title": a.get("title"),
        "status": a.get("status"),
        "status_display": STATUS_MAP.get(a.get("status", ""), a.get("status", "?")),
        "episodes": a.get("episodes") or None,
        "jp_premier": a.get("premier"),
        "sub_premier": a.get("subPremier"),
        "dub_premier": a.get("dubPremier"),   # English dub ka
        "dub_time": a.get("dubTime"),
        "jpn_time": a.get("jpnTime"),
        "sub_time": a.get("subTime"),
        "streams": streams,
        "updated_at": a.get("updatedAt"),
    }


async def search_anime(query: str, token: str) -> list:
    data = await _get("/anime", {"q": query}, token)
    if not isinstance(data, list):
        return []
    return [_parse(a) for a in data[:8]]


async def get_anime(slug: str, token: str) -> dict | None:
    data = await _get(f"/anime/{slug}", None, token)
    if not isinstance(data, dict):
        return None
    return _parse(data)
