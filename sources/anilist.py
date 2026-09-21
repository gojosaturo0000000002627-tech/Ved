"""AniList GraphQL API — metadata, status, next episode, streaming links.

Docs: https://docs.anilist.co  (no API key needed, 90 req/min limit)
"""
import asyncio

import httpx

GRAPHQL_URL = "https://graphql.anilist.co"

SEARCH_QUERY = """
query ($s: String) {
  Page(perPage: 8) {
    media(search: $s, type: ANIME, sort: SEARCH_MATCH, isAdult: false) {
      id
      title { romaji english native }
      status
      episodes
      format
      seasonYear
      nextAiringEpisode { episode airingAt }
    }
  }
}
"""

DETAIL_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    idMal
    title { romaji english native }
    status
    episodes
    format
    season
    seasonYear
    nextAiringEpisode { episode airingAt }
    externalLinks { site url type language }
    streamingEpisodes { site title }
  }
}
"""

STATUS_MAP = {
    "RELEASING": "Ongoing",
    "FINISHED": "Completed ✅ (sab episodes release ho chuke)",
    "NOT_YET_RELEASED": "Upcoming (abhi shuru nahi hua)",
    "CANCELLED": "Cancelled",
    "HIATUS": "On Hiatus (rukka hua)",
}


async def _gql(client: httpx.AsyncClient, query: str, variables: dict):
    last_err = None
    for attempt in range(3):
        try:
            r = await client.post(
                GRAPHQL_URL,
                json={"query": query, "variables": variables},
                timeout=20,
            )
            if r.status_code == 429:  # rate limited -> wait
                await asyncio.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            if data.get("errors") and not data.get("data"):
                raise RuntimeError(f"AniList error: {data['errors']}")
            return data["data"]
        except (httpx.HTTPError, RuntimeError) as e:
            last_err = e
            await asyncio.sleep(2 * (attempt + 1))
    raise RuntimeError(f"AniList request failed: {last_err}")


async def search_anime(query: str) -> list:
    """AniList search — [{anilist_id, title, romaji, native, status, episodes, format, year, next_episode, next_airing_at}]"""
    async with httpx.AsyncClient() as client:
        data = await _gql(client, SEARCH_QUERY, {"s": query})
    out = []
    for m in (data.get("Page") or {}).get("media") or []:
        out.append({
            "anilist_id": m["id"],
            "title": m["title"].get("english") or m["title"].get("romaji"),
            "romaji": m["title"].get("romaji"),
            "native": m["title"].get("native"),
            "status": m.get("status"),
            "episodes": m.get("episodes"),
            "format": m.get("format"),
            "year": m.get("seasonYear"),
            "next_episode": (m.get("nextAiringEpisode") or {}).get("episode"),
            "next_airing_at": (m.get("nextAiringEpisode") or {}).get("airingAt"),
        })
    return out


async def get_anime(anilist_id: int) -> dict:
    """Ek anime ki full detail — unified dict."""
    async with httpx.AsyncClient() as client:
        data = await _gql(client, DETAIL_QUERY, {"id": anilist_id})
    m = data["Media"]
    ext = m.get("externalLinks") or []
    streaming = [l for l in ext if (l.get("type") or "").upper() == "STREAMING"]
    next_airing = m.get("nextAiringEpisode") or {}
    return {
        "anilist_id": m["id"],
        "mal_id": m.get("idMal"),
        "title": m["title"].get("english") or m["title"].get("romaji"),
        "romaji": m["title"].get("romaji"),
        "native": m["title"].get("native"),
        "status": m.get("status"),
        "status_display": STATUS_MAP.get(m.get("status", ""), m.get("status", "?")),
        "episodes": m.get("episodes"),
        "format": m.get("format"),
        "season": m.get("season"),
        "year": m.get("seasonYear"),
        "next_episode": next_airing.get("episode"),
        "next_airing_at": next_airing.get("airingAt"),  # unix epoch
        "links": [
            {"site": l.get("site"), "url": l.get("url")}
            for l in streaming
        ],
    }
