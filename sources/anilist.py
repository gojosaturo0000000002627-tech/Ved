"""AniList GraphQL API — metadata, status, next episode, streaming links.

Docs: https://docs.anilist.co  (no API key needed, 90 req/min limit)
"""
import asyncio
import time

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
    duration
    startDate { year month day }
    season
    seasonYear
    nextAiringEpisode { episode airingAt }
    externalLinks { site url type language }
    streamingEpisodes { site title }
    relations {
      edges {
        relationType(version: 2)
        node {
          id
          type
          format
          title { romaji english }
          status
          episodes
          seasonYear
          nextAiringEpisode { episode airingAt }
        }
      }
    }
  }
}
"""

MANY_QUERY = """
query ($ids: [Int]) {
  Page(perPage: 50) {
    media(id_in: $ids, type: ANIME) {
      id
      idMal
      title { romaji english native }
      status
      episodes
      format
      duration
      startDate { year month day }
      season
      seasonYear
      nextAiringEpisode { episode airingAt }
      externalLinks { site url type language }
      streamingEpisodes { site title }
      relations {
        edges {
          relationType(version: 2)
          node {
            id
            type
            format
            title { romaji english }
            status
            episodes
            seasonYear
            nextAiringEpisode { episode airingAt }
          }
        }
      }
    }
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


def _parse_start_date(sd: dict | None) -> str | None:
    """AniList startDate -> 'YYYY-MM-DD' / 'YYYY-MM' / 'YYYY'."""
    sd = sd or {}
    if not sd.get("year"):
        return None
    if sd.get("month") and sd.get("day"):
        return f"{sd['year']:04d}-{sd['month']:02d}-{sd['day']:02d}"
    if sd.get("month"):
        return f"{sd['year']:04d}-{sd['month']:02d}"
    return f"{sd['year']:04d}"


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


def _parse_media(m: dict) -> dict:
    """AniList Media node -> unified entry (get_anime / get_anime_many dono)."""
    ext = m.get("externalLinks") or []
    streaming = [l for l in ext if (l.get("type") or "").upper() == "STREAMING"]
    next_airing = m.get("nextAiringEpisode") or {}
    relations = []
    for e in ((m.get("relations") or {}).get("edges") or []):
        node = e.get("node") or {}
        if node.get("type") != "ANIME":
            continue
        t = node.get("title") or {}
        relations.append({
            "relation": e.get("relationType"),
            "anilist_id": node.get("id"),
            "title": t.get("english") or t.get("romaji"),
            "format": node.get("format"),
            "status": node.get("status"),
            "episodes": node.get("episodes"),
            "year": node.get("seasonYear"),
            "next_episode": (node.get("nextAiringEpisode") or {}).get("episode"),
            "next_airing_at": (node.get("nextAiringEpisode") or {}).get("airingAt"),
        })
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
        "duration": m.get("duration"),
        "release_date": _parse_start_date(m.get("startDate")),
        "season": m.get("season"),
        "year": m.get("seasonYear"),
        "next_episode": next_airing.get("episode"),
        "next_airing_at": next_airing.get("airingAt"),  # unix epoch
        "links": [
            {"site": l.get("site"), "url": l.get("url")}
            for l in streaming
        ],
        "relations": relations,
    }


# In-memory TTL cache — same anime baar-baar network se nahi aayega
_entry_cache: dict[int, tuple[float, dict]] = {}
_ENTRY_TTL = 900.0  # 15 min


async def get_anime(anilist_id: int) -> dict:
    """Ek anime ki full detail — 15 min cache ke saath."""
    hit = _entry_cache.get(anilist_id)
    if hit and (time.time() - hit[0]) < _ENTRY_TTL:
        return dict(hit[1])
    async with httpx.AsyncClient() as client:
        data = await _gql(client, DETAIL_QUERY, {"id": anilist_id})
    entry = _parse_media(data["Media"])
    _entry_cache[anilist_id] = (time.time(), entry)
    return dict(entry)


async def get_anime_many(ids: list) -> list:
    """Ek hi request me multiple anime ki detail — Page(id_in) query.

    Season chain banana 5-8 sequential calls ki jagah 2-3 calls me ho
    jata hai. Data bilkul wahi rehta hai — sirf network roundtrip kam.
    """
    ids = [i for i in dict.fromkeys(ids) if i][:50]
    if not ids:
        return []
    need = [i for i in ids
            if i not in _entry_cache
            or (time.time() - _entry_cache[i][0]) >= _ENTRY_TTL]
    if need:
        try:
            async with httpx.AsyncClient() as client:
                data = await _gql(client, MANY_QUERY, {"ids": need})
            for m in (data.get("Page") or {}).get("media") or []:
                entry = _parse_media(m)
                _entry_cache[entry["anilist_id"]] = (time.time(), entry)
        except Exception as e:
            print(f"[anilist] batch fetch fail: {e}")
    out = []
    for i in ids:
        hit = _entry_cache.get(i)
        if hit and (time.time() - hit[0]) < _ENTRY_TTL:
            out.append(dict(hit[1]))
    return out
