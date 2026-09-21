"""Aggregator — sab data sources milakar ek unified AnimeInfo banata hai.

Sources (priority order):
1. AniList            — metadata, status, JP next episode, streaming links (no key)
2. anime-dub-info     — Hindi/English dub episode counts per platform (self-hosted, optional)
3. YouTube RSS        — Muse India/Ani-One ke Hindi dub uploads (live, no key)
4. AnimeSchedule      — dub air times, platforms (free token, optional)
5. /setep fixes       — users ka manual correction (sabse zyada priority)
6. overrides.json     — server-side manual corrections
"""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import config
from . import anilist, anischedule, dubinfo, youtube, aninidhi_src
from .platforms import canon_platform, platform_region

tz = timezone(timedelta(hours=5, minutes=30))  # IST


def _load_overrides() -> dict:
    try:
        if os.path.exists(config.OVERRIDES_FILE):
            with open(config.OVERRIDES_FILE, encoding="utf-8") as f:
                return json.load(f)
    except (OSError, ValueError):
        pass
    return {}


def _merge_overrides(info: dict):
    ov = _load_overrides()
    key = str(info.get("anilist_id"))
    patch = ov.get(key) or (ov.get(info.get("title", "").lower()) if info.get("title") else None)
    if not isinstance(patch, dict):
        return
    for k, v in patch.items():
        if k == "platforms":
            info["platforms"] = [
                {"name": canon_platform(p.get("name", "")),
                 "url": p.get("url"),
                 "region": p.get("region", "India"),
                 "langs": p.get("langs", [])}
                for p in v if isinstance(p, dict)
            ]
        elif k in ("jp_aired", "en_aired", "hi_aired"):
            info[k] = v
        elif k == "next_by_lang":
            info["next_by_lang"].update({k2: v2 for k2, v2 in v.items()})


async def search(query: str) -> list:
    """AniList search — card dikhane ke liye list."""
    return await anilist.search_anime(query)


def _airing_from_next(next_episode: Optional[int]) -> Optional[int]:
    """Agla episode 12 hai -> 11 release ho chuke (JP)."""
    if next_episode and next_episode > 1:
        return next_episode - 1
    return None


def _parse_dt(v) -> Optional[datetime]:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _est_next(latest: Optional[datetime]) -> Optional[str]:
    """Weekly release assumption: last release + 7 din.
    Past date ho to aage badhao (stale source se galat purani date na dikhe)."""
    if not latest:
        return None
    nxt = latest + timedelta(days=7)
    now = datetime.now(timezone.utc)
    steps = 0
    while nxt <= now and steps < 8:
        nxt += timedelta(days=7)
        steps += 1
    if nxt <= now:
        return None
    return nxt.astimezone(tz).strftime("%d %b %Y, %I:%M %p IST (estimated)")


async def get_anime_info(anilist_id: int, title_hint: str = "",
                          force: bool = False, db=None) -> dict:
    """Unified info. Cache + multi-source merge + overrides."""
    cache_key = f"anime:{anilist_id}"
    if not force and db is not None:
        cached = db.cache_get(cache_key, config.CACHE_TTL)
        if cached:
            return cached

    base = await anilist.get_anime(anilist_id)
    title = base["title"] or title_hint or base.get("romaji") or "?"
    ongoing = base.get("status") == "RELEASING"

    # Parallel mein sab optional sources
    tasks = [
        dubinfo.get_dub_info(title, config.DUB_INFO_API),
        youtube.find_episodes(title),
        anischedule.search_anime(title, config.ANIMESCHEDULE_TOKEN),
        asyncio.to_thread(aninidhi_src.hindi_dub_status, title,
                          base.get("episodes")),
    ]
    try:
        dub_data, yt_data, as_results, anidhi = await asyncio.gather(*tasks)
    except Exception as e:
        print(f"[aggregator] optional sources failed: {e}")
        dub_data, yt_data, as_results, anidhi = None, None, [], None

    as_data = None
    for a in as_results:
        if (a.get("slug") and a.get("title")
                and (a["title"].lower() in title.lower()
                     or title.lower() in a["title"].lower())):
            as_data = a
            break
    if as_data and as_data.get("slug"):
        as_data = await anischedule.get_anime(as_data["slug"],
                                              config.ANIMESCHEDULE_TOKEN) or as_data

    # ---- Episode counts per language ----
    jp_aired = None
    if base.get("status") == "FINISHED":
        jp_aired = base.get("episodes")
    else:
        jp_aired = _airing_from_next(base.get("next_episode"))
        if jp_aired is None and dub_data:
            jp_aired = dub_data.get("langs", {}).get("jp", {}).get("released") or None

    en_aired = None
    hi_aired = None
    hi_source = None        # "youtube" | "dubinfo" | None — card me dikhane ke liye
    hi_latest_dt = None     # Hindi dub ka last release (next episode estimate)
    en_latest_dt = None
    if dub_data:
        en_aired = dub_data.get("langs", {}).get("en", {}).get("released") or None
        hi_aired = dub_data.get("langs", {}).get("hi", {}).get("released") or None
        hi_latest_dt = _parse_dt(dub_data.get("langs", {}).get("hi", {}).get("latest"))
        en_latest_dt = _parse_dt(dub_data.get("langs", {}).get("en", {}).get("latest"))
        if hi_aired:
            hi_source = "dubinfo"
    # AniNidhi — official Hindi dub tracker (platform + start date + status)
    anidhi_next_date = None
    anidhi_finished = False
    anidhi_upcoming = None
    anidhi_announced = False
    anidhi_found = bool(anidhi) and anidhi.get("found")
    if anidhi_found:
        if (anidhi.get("eps") or 0) > (hi_aired or 0):
            hi_aired = anidhi["eps"]
            hi_source = "aninidhi"
        elif hi_aired and (anidhi.get("eps") or 0) == hi_aired:
            hi_source = hi_source or "aninidhi"
        anidhi_next_date = anidhi.get("next")
        anidhi_finished = bool(anidhi.get("finished"))
        anidhi_upcoming = anidhi.get("upcoming")
        anidhi_announced = bool(anidhi.get("announced"))
    # YouTube se Hindi dub — live count (Muse India / Ani-One uploads)
    if yt_data and yt_data.get("ep"):
        if (yt_data["ep"] or 0) > (hi_aired or 0):
            hi_aired = yt_data["ep"]
            hi_source = "youtube"
        ydt = _parse_dt(yt_data.get("published"))
        if ydt and (hi_latest_dt is None or ydt > hi_latest_dt):
            hi_latest_dt = ydt
    if en_aired is None and as_data and as_data.get("dub_premier"):
        en_aired = 1

    # Release tracking (bot khud dekhta raha hai kab kya aaya) — estimate behtar
    lang_state = {}
    if db is not None:
        try:
            lang_states = db.get_lang_states(anilist_id)
            for lang, st in lang_states.items():
                lang_state[lang] = st
                seen = _parse_dt(st.get("last_seen_at"))
                if lang == "hi" and seen and (hi_latest_dt is None or seen > hi_latest_dt):
                    hi_latest_dt = seen
                if lang == "en" and seen and (en_latest_dt is None or seen > en_latest_dt):
                    en_latest_dt = seen
        except Exception:
            pass

    # Manual fixes — sabse zyada priority (users ka /setep)
    manual = {}
    if db is not None:
        try:
            manual = db.get_manual_fixes(anilist_id)
            if manual.get("jp") is not None:
                jp_aired = manual["jp"]
            if manual.get("en") is not None:
                en_aired = manual["en"]
            if manual.get("hi") is not None:
                hi_aired = manual["hi"]
                hi_source = "manual"
        except Exception:
            pass

    total = base.get("episodes") or (as_data or {}).get("episodes")

    # ---- Platforms ----
    platforms = {}  # name -> entry
    if dub_data:
        for p in dub_data.get("platforms", []):
            platforms[p["name"]] = p
    for l in base.get("links", []):
        name = canon_platform(l.get("site") or "")
        if name and name not in platforms:
            platforms[name] = {
                "name": name, "url": l.get("url"),
                "region": platform_region(name), "langs": [],
            }
    if as_data:
        for s in as_data.get("streams", []):
            name = canon_platform(s.get("platform") or "")
            if name and name not in platforms:
                platforms[name] = {
                    "name": name, "url": s.get("url"),
                    "region": platform_region(name),
                    "langs": ["en"] if s.get("is_dub") else ["jp"],
                }
    # AniNidhi ke official Hindi dub platforms bhi jodo (region India)
    if anidhi_found:
        for p in anidhi.get("platforms", []):
            name = canon_platform(p.get("platform") or "")
            if name and name not in platforms:
                platforms[name] = {
                    "name": name, "url": "",
                    "region": "India", "langs": ["hi"],
                }
    # YouTube channel bhi platform hai (Hindi dub wahan mila)
    if yt_data and yt_data.get("ep"):
        yt_name = youtube.channel_to_platform(yt_data.get("channel_entry", ""))
        if yt_name and yt_name not in platforms:
            platforms[yt_name] = {
                "name": yt_name, "url": "",
                "region": "India", "langs": ["hi"],
            }
    platforms = sorted(platforms.values(), key=lambda p: p["name"])

    # ---- Next episode ----
    next_by_lang: dict[str, Optional[str]] = {"jp": None, "en": None, "hi": None}
    if base.get("next_airing_at"):
        dt = datetime.fromtimestamp(base["next_airing_at"], tz=tz)
        next_by_lang["jp"] = dt.strftime("%d %b %Y, %I:%M %p IST")

    def _set_est(lang: str, latest: Optional[datetime], done: Optional[int]):
        # Anime abhi start hi nahi hua -> koi estimate nahi.
        # NOTE: JP season finish ho chuka ho tab bhi dub chal sakta hai!
        if base.get("status") == "NOT_YET_RELEASED" or next_by_lang.get(lang):
            return
        if total and (done or 0) >= total:
            next_by_lang[lang] = "All episodes released"
        elif latest:
            next_by_lang[lang] = _est_next(latest)

    _set_est("en", en_latest_dt, en_aired)
    # AniNidhi ki structured next-date sabse pehle (weekly math se)
    # (JP complete hone ke baad bhi Hindi dub chalta rehta hai)
    if base.get("status") != "NOT_YET_RELEASED" and not next_by_lang.get("hi"):
        if anidhi_upcoming:
            next_by_lang["hi"] = f"Starts {anidhi_upcoming.strftime('%d %b %Y')} (announced)"
        elif anidhi_announced:
            next_by_lang["hi"] = "Announced — date TBA"
        elif anidhi_finished or (total and (hi_aired or 0) >= total):
            next_by_lang["hi"] = "All episodes released"
        elif anidhi_next_date:
            next_by_lang["hi"] = anidhi_next_date.strftime("%d %b %Y (estimated)")
    _set_est("hi", hi_latest_dt, hi_aired)

    # Hindi dub note — jab count pata hi nahi
    hi_note = None
    if hi_aired is None and not anidhi_found and not dub_data and not yt_data:
        hi_note = "No official Hindi dub found"

    status_display = base.get("status_display")
    if as_data and status_display in ("Ongoing", None):
        status_display = as_data.get("status_display") or status_display

    info = {
        "anilist_id": anilist_id,
        "title": title,
        "romaji": base.get("romaji"),
        "native": base.get("native"),
        "status": base.get("status"),
        "status_display": status_display or "?",
        "total_episodes": total,
        "format": base.get("format"),
        "season": base.get("season"),
        "year": base.get("year"),
        "next_episode": base.get("next_episode"),
        "jp_aired": jp_aired,
        "en_aired": en_aired,
        "hi_aired": hi_aired,
        "hi_source": hi_source,
        "hi_note": hi_note,
        "hi_last_release": hi_latest_dt.isoformat() if hi_latest_dt else None,
        "platforms": platforms,
        "next_by_lang": next_by_lang,
        "checked_at": datetime.now(tz).strftime("%d %b %Y, %I:%M %p IST"),
    }
    _merge_overrides(info)

    if db is not None:
        try:
            db.cache_set(cache_key, info)
        except Exception:
            pass
    return info


def lang_counts(info: dict) -> dict:
    """Notification engine ke liye — {lang: released_count}."""
    return {
        "jp": info.get("jp_aired") or 0,
        "en": info.get("en_aired") or 0,
        "hi": info.get("hi_aired") or 0,
    }
