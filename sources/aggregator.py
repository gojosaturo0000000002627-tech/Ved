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
import difflib
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
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
    """Smart search — layered:

    1. Direct AniList search
    2. Typo correction (AniNidhi titles se difflib — 'mushoko tensai' -> 'Mushoku Tensei')
    3. Word-subset (pehle 2 words, phir 1 word)
    """
    try:
        results = await anilist.search_anime(query)
    except Exception as e:
        print(f"[search] anilist fail: {e}")
        results = []
    if results:
        return results

    corrected = _fuzzy_correct(query)
    if corrected and corrected.lower() != query.lower():
        print(f"[search] typo corrected: '{query}' -> '{corrected}'")
        try:
            results = await anilist.search_anime(corrected)
        except Exception:
            results = []
        if results:
            return results

    words = [w for w in query.split() if len(w) > 2]
    for n in (2, 1):
        if len(words) > n:
            try:
                results = await anilist.search_anime(" ".join(words[:n]))
            except Exception:
                results = []
            if results:
                return results
    return []


def _fuzzy_correct(query: str) -> Optional[str]:
    """AniNidhi ke titles se typo correction.

    'mushoko tensai' -> 'Mushoku Tensei' (har word ~80% similar ho)
    Sirf tab jab saare query words kisi title se match karein.
    """
    try:
        import aninidhi
        records = aninidhi.list_all()
    except Exception:
        return None
    qw = re.sub(r"[^a-z0-9\s]", " ", query.lower()).split()
    if not qw:
        return None
    candidates = []
    for rec in records:
        title = rec.get("title") or ""
        tw = re.sub(r"[^a-z0-9\s]", " ", title.lower()).split()
        if not tw:
            continue
        if all(difflib.get_close_matches(w, tw, n=1, cutoff=0.8) for w in qw):
            candidates.append(title)
    if not candidates:
        return None
    # sabse chhota/seedha title best hai
    best = min(candidates, key=len)
    s = re.sub(r"\([^)]*\)", " ", best)
    words = [w for w in s.split() if len(w) > 2][:3]
    return " ".join(words) if words else None


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


async def _build_season_chain(base: dict, max_seasons: int = 5) -> list:
    """base + prequels + sequels — TV format chain (season-wise card ke liye).

    AniList entry ke relations se PREQUEL/SEQUEL follow karta hai.
    """
    def _linked(entry, relation):
        for r in entry.get("relations") or []:
            if r.get("relation") == relation and r.get("format") == "TV":
                return r
        return None

    chain_ids = {base.get("anilist_id")}
    seasons = [base]

    cur = base
    while len(seasons) < max_seasons:
        pre = _linked(cur, "PREQUEL")
        if not pre or pre.get("anilist_id") in chain_ids:
            break
        try:
            node = await anilist.get_anime(pre["anilist_id"])
        except Exception as e:
            print(f"[seasons] prequel fetch fail: {e}")
            break
        seasons.insert(0, node)
        chain_ids.add(node["anilist_id"])
        cur = node

    cur = base
    while len(seasons) < max_seasons:
        seq = _linked(cur, "SEQUEL")
        if not seq or seq.get("anilist_id") in chain_ids:
            break
        try:
            node = await anilist.get_anime(seq["anilist_id"])
        except Exception as e:
            print(f"[seasons] sequel fetch fail: {e}")
            break
        seasons.append(node)
        chain_ids.add(node["anilist_id"])
        cur = node
    return seasons


def _season_base(title: str) -> str:
    """Title se saare season/part/cour markers hata kar normalized base.

    'Mushoku Tensei: Jobless Reincarnation Season 2 Part 2' ->
    'mushokutenseijoblessreincarnation'
    """
    t = re.sub(r"\([^)]*\)", " ", title or "")
    t = re.sub(r"\b\d+(?:st|nd|rd|th)\s+(?:season|part|cour)\b", " ", t,
               flags=re.IGNORECASE)
    t = re.sub(r"\b(?:season|part|cour)\s*\d+\b", " ", t, flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]", "", t.lower())


MOVIE_FORMATS = ("MOVIE", "OVA", "SPECIAL")


def _franchise_key(title: str) -> str:
    """Franchise pehchanne wala key — cours/seasons/movies sab same key denge.

    'Mushoku Tensei: Jobless Reincarnation Cour 2 - Eris the Goblin Slayer'
      -> 'mushokutensei'
    """
    t = title or ""
    if ":" in t:
        t = t.split(":")[0]
    t = re.sub(r"\([^)]*\)", " ", t)
    t = re.sub(r"\b\d+(?:st|nd|rd|th)\s+(?:season|part|cour)\b", " ", t,
               flags=re.IGNORECASE)
    t = re.sub(r"\b(?:season|part|cour)\s*\d+\b", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*[-–—].*$", "", t)
    return re.sub(r"[^a-z0-9]", "", t.lower())


def franchise_pick(results: list, query: str):
    """Same franchise ke multiple results? Seedha full card bhejo.

    AniList har cour/movie/spinoff ko alag entry rakhta hai. Pehle result
    ka franchise key agar 2+ results me milta hai (seasons, parts, movies)
    to pick-list ki jagah seedha full card bhejo — usme sab aa jaata hai.
    Genuinely alag anime (Naruto vs Blue Lock) me first ka group akela
    hota hai -> None -> pick list dikhegi.
    """
    if not results or len(results) < 2:
        return None
    top = results[:6]
    keys = [_franchise_key(r.get("title") or "") for r in top]
    first = keys[0]
    if not first:
        return None
    if sum(1 for k in keys if k == first) < 2:
        return None
    # Query me season/part/cour number hai to wahi entry lo
    for kind in ("season", "part", "cour"):
        qn = _marker_num(query, kind)
        if qn:
            for r in top:
                if _marker_num(r.get("title") or "", kind) == qn:
                    return r["anilist_id"]
    # Default: pehla result (AniList relevance order — usually S1)
    return results[0]["anilist_id"]


async def _anidhi_lookup(entry: dict) -> dict:
    """AniNidhi lookup — primary title se na mile to romaji/native bhi try.

    Kuch records English naam se hain (Sparks of Tomorrow), kuch romaji
    se (Dan Da Dan) — teeno try karke dekh lo, koi miss nahi honi chahiye.
    """
    eps = entry.get("episodes")
    tried, result = [], {}
    for t in (entry.get("title"), entry.get("romaji"),
              entry.get("native")):
        if not t or t in tried:
            continue
        tried.append(t)
        try:
            result = await asyncio.to_thread(
                aninidhi_src.hindi_dub_status, t, eps)
        except Exception:
            continue
        if result and result.get("found"):
            return result
    return result or {}


def _marker_num(title: str, kind: str) -> Optional[int]:
    """'Season 2' / 'Part 3' / '2nd Season' -> number."""
    t = title or ""
    m = re.search(rf"\b{kind}\s*(\d+)\b", t, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(rf"\b(\d+)(?:st|nd|rd|th)\s+{kind}\b", t, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def _group_seasons(seasons: list) -> list:
    """Cours ko merge karke asli season groups.

    AniList har cour ko alag entry rakhta hai (jaise 'Part 2',
    'Season 2 Part 2') — ye sab continuation hain, naya season nahi.
    Rule: same base naam + part/cour marker (aur same season number)
    = pichle season ka hi cour.
    """
    groups = []
    for s in seasons:
        t = s.get("title") or ""
        b = _season_base(t)
        part = _marker_num(t, "part") or _marker_num(t, "cour")
        season = _marker_num(t, "season")
        if groups and part and b == groups[-1]["base"]:
            prev_season = _marker_num(
                groups[-1]["entries"][-1].get("title") or "", "season")
            if season is None or (prev_season is not None
                                  and season == prev_season):
                groups[-1]["entries"].append(s)
                continue
        groups.append({"base": b, "entries": [s]})
    return groups


def _season_complete_note(anidhi: dict, total: Optional[int]) -> str:
    """Finished Hindi dub ka note: '(Apr 2026 me complete)' ya '(complete)'."""
    rd = None
    for p in anidhi.get("platforms") or []:
        try:
            rd = date.fromisoformat(str(p.get("release_date")))
            if rd:
                break
        except (TypeError, ValueError):
            continue
    if rd and total:
        last = rd + timedelta(days=7 * max(total - 1, 0))
        return f"({last.strftime('%b %Y')} me complete)"
    return "(complete)"


async def _movie_details(base: dict, chain: list) -> list:
    """Franchise ki movies/OVAs — card me dikhane ke liye.

    Chain ke har entry ke relations me MOVIE/OVA/SPECIAL format wale
    links uthata hai (max 3), unki detail + Hindi dub status laata hai.
    """
    seen, ids = set(), []
    for e in chain:
        for r in e.get("relations") or []:
            fmt = r.get("format")
            rid = r.get("anilist_id")
            if fmt in MOVIE_FORMATS and rid and rid not in seen:
                seen.add(rid)
                ids.append(rid)
    if not ids:
        return []
    ids = ids[:3]

    details = await asyncio.gather(*[anilist.get_anime(i) for i in ids],
                                   return_exceptions=True)
    valid = [d for d in details if isinstance(d, dict)]
    if not valid:
        return []
    try:
        anidhis = await asyncio.gather(*[_anidhi_lookup(d)
                                         for d in valid])
    except Exception:
        anidhis = [None] * len(valid)

    out = []
    for i, d in enumerate(valid):
        a = anidhis[i] if i < len(anidhis) else None
        a = a if isinstance(a, dict) else None
        found = bool(a and a.get("found"))
        hi = found and bool(a.get("finished") or a.get("eps"))
        note = None
        if not hi and found:
            if a.get("upcoming"):
                note = (f"Starts "
                        f"{a['upcoming'].strftime('%d %b %Y')} (announced)")
            elif a.get("announced"):
                note = "Announced — date TBA"
        out.append({
            "title": d.get("title"),
            "year": d.get("year"),
            "format": d.get("format"),
            "hi": hi,
            "hi_note": note,
        })
    return out


async def _season_details(base: dict, seasons: list | None = None) -> list:
    """Season-wise blocks — cours merge karke asli seasons:

    • Season 1 (2021)
    Released: 23/23 episodes
    Hindi dub: 23 episodes (Jun 2021 me complete)
    Japanese audio: 23 episodes
    """
    seasons = seasons if seasons is not None else await _build_season_chain(base)
    if len(seasons) < 2:
        return []

    groups = _group_seasons(seasons)
    if len(groups) < 2:
        return []

    # Har entry ka AniNidhi status (parallel)
    anidhi_list = []
    try:
        anidhi_list = await asyncio.gather(*[_anidhi_lookup(s)
                                             for s in seasons])
    except Exception as e:
        print(f"[seasons] aninidhi fail: {e}")
    anidhi_map = {id(s): anidhi_list[i] for i, s in enumerate(seasons)
                  if i < len(anidhi_list)}

    out = []
    for gi, g in enumerate(groups, 1):
        entries = g["entries"]
        total = sum(e.get("episodes") or 0 for e in entries) or None
        ongoing_s = any(e.get("status") == "RELEASING" for e in entries)
        jp = 0
        for e in entries:
            if e.get("status") == "FINISHED":
                jp += e.get("episodes") or 0
            else:
                jp += _airing_from_next(e.get("next_episode")) or 0
        jp = jp or None

        # ---- Hindi dub aggregation (cour-wise records ko jodo) ----
        hi_s, hi_note = None, None
        found_any, finished_all, eps_sum = False, True, 0
        last_finished_a, last_finished_total = None, 0
        for e in entries:
            a = anidhi_map.get(id(e))
            if not (a and a.get("found")):
                continue
            found_any = True
            e_total = e.get("episodes")
            if a.get("finished"):
                eps_sum += e_total or 0
                last_finished_a, last_finished_total = a, e_total
            elif a.get("eps"):
                finished_all = False
                eps_sum += a["eps"]
            else:
                finished_all = False
                if a.get("upcoming"):
                    hi_note = (f"Starts "
                               f"{a['upcoming'].strftime('%d %b %Y')} "
                               f"(announced)")
                elif a.get("announced"):
                    hi_note = "Announced — date TBA"
        if found_any and finished_all and total:
            hi_s = total
            hi_note = _season_complete_note(last_finished_a,
                                            last_finished_total)
        elif eps_sum:
            hi_s = eps_sum

        out.append({
            "num": gi,
            "year": entries[0].get("year"),
            "ongoing": ongoing_s,
            "total": total,
            "jp_aired": jp,
            "hi_aired": hi_s,
            "hi_note": hi_note,
            "is_current": any(e.get("anilist_id") == base.get("anilist_id")
                               for e in entries),
        })
    return out


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
    is_movie = (base.get("format") == "MOVIE")

    # Parallel mein sab optional sources
    tasks = [
        dubinfo.get_dub_info(title, config.DUB_INFO_API),
        youtube.find_episodes(title),
        anischedule.search_anime(title, config.ANIMESCHEDULE_TOKEN),
        _anidhi_lookup(base),
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
        if is_movie:
            jp_aired = 1  # movie = ek hi "episode"
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
        # Movie: finished dub = available (count 1)
        if is_movie and anidhi_finished:
            hi_aired = 1
            hi_source = hi_source or "aninidhi"
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
    if is_movie:
        if base.get("status") == "FINISHED":
            status_display = "Released ✅"
        elif base.get("status") == "NOT_YET_RELEASED":
            status_display = "Upcoming movie (abhi release nahi hui)"

    # Chain ek hi baar banao — seasons + movies dono ke liye
    chain = [base] if is_movie else await _build_season_chain(base)
    seasons_blocks = [] if is_movie else await _season_details(base, seasons=chain)
    current_season_num = next((s["num"] for s in seasons_blocks
                               if s.get("is_current")), None)
    movies_out = await _movie_details(base, chain)

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
        "seasons": seasons_blocks,
        "current_season_num": current_season_num,
        "movies": movies_out,
        "is_movie": is_movie,
        "release_date": base.get("release_date"),
        "duration": base.get("duration"),
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
