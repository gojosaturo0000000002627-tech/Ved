"""anime-dub-info API (self-hosted) — Hindi/Indian dub episode counts.

Ye ek open-source project hai (github.com/sama511/anime-dub-info) jo
Crunchyroll, Netflix, Prime Video, Anime Times, JioHotstar, ZEE5, MX Player,
Muse India, Ani-One — sab par dub episode counts track karta hai.

Public instance kabhi up kabhi down rehti hai, isliye DUB_INFO_API env
mein apni instance ka URL daalo (README mein deploy guide hai).
Response shape tolerant parse ki jati hai — alag forks thode different hain.
"""
import asyncio

import httpx

from .platforms import canon_platform, platform_region


async def _get_json(client: httpx.AsyncClient, url: str):
    r = await client.get(url, timeout=20)
    if r.status_code >= 400:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def _parse_languages(lang_list) -> dict:
    """[{language: "Hindi", total_released_episodes: 3, latest_episode_added: ...}]
    -> {"hi": {"released": 3, "latest": iso-date}}"""
    out = {}
    if not isinstance(lang_list, list):
        return out
    for l in lang_list:
        if not isinstance(l, dict):
            continue
        name = (l.get("language") or l.get("lang") or "").lower()
        if "hindi" in name:
            code = "hi"
        elif "english" in name:
            code = "en"
        elif "japanese" in name or "raw" in name:
            code = "jp"
        else:
            continue
        released = (l.get("total_released_episodes")
                   or l.get("totalReleasedEpisodes")
                   or l.get("episodes")
                   or 0)
        try:
            released = int(released)
        except (TypeError, ValueError):
            released = 0
        out[code] = {
            "released": released,
            "latest": (l.get("latest_episode_added")
                       or l.get("latestEpisodeAdded")),
        }
    return out


async def get_dub_info(query: str, base_url: str) -> dict | None:
    """Anime ke dub/platform details. None = source available nahi / nahi mila."""
    if not base_url:
        return None
    for search_path in ("/anime/search", "/search"):
        try:
            async with httpx.AsyncClient() as client:
                res = await _try_search(client, base_url, search_path, query)
            if res:
                return res
        except Exception as e:
            print(f"[dubinfo] {search_path} failed: {e}")
            await asyncio.sleep(1)
    return None


async def _try_search(client: httpx.AsyncClient, base_url: str,
                      search_path: str, query: str) -> dict | None:
    r = await client.get(f"{base_url}{search_path}",
                         params={"q": query}, timeout=20)
    if r.status_code >= 400:
        return None
    try:
        results = r.json()
    except ValueError:
        return None
    if isinstance(results, dict):
        results = results.get("results") or results.get("data") or []
    if not isinstance(results, list) or not results:
        return None
    # Best title match
    q = query.lower()
    best = None
    for item in results:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or item.get("title") or "").lower()
        if name and (q in name or name in q):
            best = item
            break
    best = best or results[0]
    route = best.get("route") or best.get("id") or best.get("slug")
    if route is None:
        return None
    detail = await _get_json(client, f"{base_url}/anime/{route}")
    if not detail:
        return None
    return _parse_detail(detail, best)


def _parse_detail(detail: dict, search_item: dict) -> dict:
    name = (detail.get("name") or detail.get("title")
            or search_item.get("name") or search_item.get("title"))
    platforms = []
    langs_global = {}
    dubs = detail.get("dubs") or detail.get("platforms") or []
    if isinstance(dubs, dict):
        dubs = list(dubs.values())
    for d in dubs:
        if not isinstance(d, dict):
            continue
        plat_name = canon_platform(d.get("name") or d.get("platform") or "")
        if not plat_name:
            continue
        langs = _parse_languages(d.get("languages") or d.get("dubs") or [])
        for code, info in langs.items():
            prev = langs_global.get(code) or {"released": 0}
            langs_global[code] = {
                "released": max(prev["released"], info["released"]),
                "latest": info.get("latest") or prev.get("latest"),
            }
        platforms.append({
            "name": plat_name,
            "url": d.get("url") or d.get("link"),
            "region": "India",  # anime-dub-info Indian platforms track karta hai
            "langs": sorted(langs.keys()),
        })
    return {
        "title": name,
        "platforms": platforms,
        "langs": langs_global,   # {"hi": {"released": 3, "latest": "..."}, ...}
    }
