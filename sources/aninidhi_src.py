"""
sources/aninidhi_src.py — Hindi dub ka REAL database (PyPI package `aninidhi`).

`aninidhi` 488+ real records deta hai (platform + release_date + status), no API key.
Package khud ek hosted dataset se daily refresh karta hai aur offline me bundled
snapshot par fallback karta hai — isliye ye source kabhi hard-fail nahi hota.

Yahan hum:
  * list_all() ko 6 ghante cache karte hain (network call hai)
  * title variants try karte hain: English -> Romaji -> Native (+ colon-prefix variants)
  * AniNidhi title se season/cour parse karte hain ("(Season 2 Cour 1)" -> S2)
  * weekly math karte hain: dub start + 7-din interval = kitne episode aa chuke
"""
from __future__ import annotations

import asyncio
import difflib
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

import config
from sources import platforms

log = logging.getLogger("aninidhi")

try:  # real package; tests me bhi yahi use hota hai
    import aninidhi
except ImportError:  # pragma: no cover - package requirements me hai
    aninidhi = None
    log.warning("aninidhi package install nahi hai — Hindi dub data nahi milega")

# ---------------------------------------------------------------------------
# Season / cour parsing
# ---------------------------------------------------------------------------
_ORDINALS = {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5, "6th": 6, "7th": 7, "8th": 8}
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}

_RE_SEASON_NUM = re.compile(r"season\s*[-–—]?\s*(\d{1,2})", re.I)
_RE_ORDINAL_SEASON = re.compile(r"(\dst|\dnd|\drd|\dth)\s+season", re.I)
_RE_COUR = re.compile(r"(?:cour|part|cours)\s*[-–—]?\s*(\d{1,2})", re.I)
_RE_ROMAN = re.compile(r"\b([ivx]{1,5})\b(?=\s*[:–—-]|\s*$)", re.I)
_RE_SEASON_WORD_TAIL = re.compile(r"\bseason\s*(\d{1,2})\b", re.I)
_PAREN = re.compile(r"\(([^)]*)\)\s*$")


def parse_season(title: str, default_one: bool = True) -> tuple[str, int | None, int | None, str | None]:
    """
    AniNidhi/AniList title -> (base_title, season_number, cour, kind)

    kind: 'series' | 'movie' | 'ova' | 'special' | None
    Example:
      'Mushoku Tensei (Season 2 Cour 1)' -> ('Mushoku Tensei', 2, 1, 'series')
      'Suzume (Movie)'                   -> ('Suzume', None, None, 'movie')
      'Black Torch'                      -> ('Black Torch', 1, None, 'series')
    """
    raw = (title or "").strip()
    base = raw
    marker = ""
    m = _PAREN.search(raw)
    if m:
        marker = m.group(1)
        base = raw[: m.start()].strip()

    blob = f"{base} {marker}"
    kind: str | None = None
    low = marker.lower()
    if "movie" in low or "film" in low:
        kind = "movie"
    elif "ova" in low or "ona" in low:
        kind = "ova"
    elif "special" in low or "recap" in low:
        kind = "special"

    season: int | None = None
    sm = _RE_SEASON_NUM.search(marker) or _RE_SEASON_NUM.search(base)
    if sm:
        season = int(sm.group(1))
    else:
        om = _RE_ORDINAL_SEASON.search(marker) or _RE_ORDINAL_SEASON.search(base)
        if om:
            season = _ORDINALS.get(om.group(1).lower())
        else:
            # "Mushoku Tensei II: ..." / "Grand Blue Season 3" style roman numeral
            rm = _RE_ROMAN.search(base)
            if rm and rm.group(1).lower() in _ROMAN and rm.group(1).lower() != "i":
                season = _ROMAN[rm.group(1).lower()]
            else:
                sm2 = _RE_SEASON_WORD_TAIL.search(base)
                if sm2:
                    season = int(sm2.group(1))

    cour: int | None = None
    cm = _RE_COUR.search(marker) or _RE_COUR.search(base)
    if cm:
        cour = int(cm.group(1))

    # "(Ep. 1089-1128)" jaise arc batches — season marker nahi hai
    if season is None and default_one and not marker.strip():
        season = 1  # plain base title = pehla season (AniNidhi records ke liye)
    return base, season, cour, kind


def franchise_key(title: str) -> str:
    """
    Title se season/part/cour/colon-prefix/dash-subtitle markers hata ke ek key.
    'Mushoku Tensei: Jobless Reincarnation Season 2 Part 2' -> 'mushoku tensei'
    'Grand Blue Dreaming Season 3'                          -> 'grand blue dreaming'
    """
    t = (title or "").lower()
    t = _PAREN.sub(" ", t)                      # (Season 2 Cour 1) hatao
    t = t.split(":")[0]                          # colon prefix/suffix
    t = re.split(r"\s[–—-]\s", t)[0]             # " – Mugen Train Arc"
    # "Season 3" / "Part 2" / "Cour 2" ko number ke saath hatao
    t = re.sub(r"\b(?:season|part|cour|cours|stage)\s*[-–—]?\s*\d{1,2}\b", " ", t)
    t = re.sub(r"\b(the|movie|film|ova|ona|special|tv|arc|cour|cours|part|season|stage|episode|ep)\b", " ", t)
    t = re.sub(r"\b(1st|2nd|3rd|\d{1,2}(st|nd|rd|th))\b", " ", t)
    t = re.sub(r"\b[ivx]{1,5}\b", " ", t)        # roman numerals
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = t.strip()
    # aakhri me bacha hua bare number ("grand blue dreaming 3") bhi hatao —
    # par agar title sirf number hai ("86") to usse chhedna nahi
    stripped = re.sub(r"\s\d{1,2}$", "", t).strip()
    return stripped or t


def title_variants(titles: list[str]) -> list[str]:
    """
    AniNidhi lookup ke liye variants, order me:
      English -> Romaji -> Native, aur har ek ka colon-prefix chhota roop.
    ('Sparks of Tomorrow' chalta hai, 'Nijusseiki Denki Mokuroku: Eureka Evrika' nahi.)
    """
    out: list[str] = []
    for t in titles:
        if not t:
            continue
        t = t.strip()
        if t not in out:
            out.append(t)
        # "Mushoku Tensei: Jobless Reincarnation Season 3" -> "Mushoku Tensei"
        if ":" in t:
            head = t.split(":")[0].strip()
            if head and head not in out:
                out.append(head)
        # "Grand Blue Dreaming Season 2" -> "Grand Blue Dreaming"
        stripped = re.sub(r"\b(season|part|cour|cours)\s*[-–—]?\s*\d{1,2}\b", " ", t, flags=re.I)
        stripped = re.sub(r"\s+", " ", stripped).strip()
        if stripped and stripped != t and stripped not in out:
            out.append(stripped)
        # parenthetical hata ke
        noparen = _PAREN.sub("", t).strip()
        if noparen and noparen not in out:
            out.append(noparen)
    return out


# ---------------------------------------------------------------------------
# Dub records
# ---------------------------------------------------------------------------
@dataclass
class DubRecord:
    platform: str
    display_platform: str
    release_date: date | None
    status: str          # Airing / Finished / TBA / Removed
    media_type: str      # series / movie
    aninidhi_title: str
    season: int | None
    cour: int | None

    @property
    def usable(self) -> bool:
        """Removed = platform se hata diya gaya, isliye usable nahi."""
        return self.status.lower() != "removed" and self.release_date is not None


@dataclass
class SeasonDub:
    season: int | None
    records: list[DubRecord] = field(default_factory=list)
    matched_title: str | None = None

    @property
    def start(self) -> date | None:
        """Dub kab shuru hua — sabse pehla usable platform."""
        dates = [r.release_date for r in self.records if r.usable]
        return min(dates) if dates else None

    @property
    def platforms(self) -> list[str]:
        return platforms.dedupe_preserve([r.display_platform for r in self.records if r.usable])

    def platform_for_status(self, status: str | None = None) -> list[str]:
        want = (status or "").lower()
        return platforms.dedupe_preserve(
            [r.display_platform for r in self.records if r.usable and (not want or r.status.lower() == want)]
        )


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Weekly math — dub start + 7 din = episodes released
# ---------------------------------------------------------------------------
@dataclass
class DubProgress:
    episodes: int | None = None
    next_date: date | None = None
    complete: bool = False
    complete_month: str | None = None      # "Jun 2026"
    status: str | None = None
    platforms: list[str] = field(default_factory=list)
    estimated: bool = False

    @property
    def known(self) -> bool:
        return self.episodes is not None


def weekly_progress(
    start: date | None,
    status: str | None,
    planned_total: int | None,
    today: date,
) -> DubProgress:
    """
    Dub start date + 7-din interval se episodes released.

    Rules:
      * start None / TBA      -> kuch nahi pata (kabhi guess nahi)
      * today < start         -> 0 episode, next = start
      * status Finished       -> planned_total (pata ho to), warna weekly math
      * planned cap           -> weekly math kabhi planned se upar nahi jaata
      * Past-date guard       -> next date nikal chuka ho to None (card par 'To be announced')
    """
    out = DubProgress(status=status)
    if start is None:
        return out

    if (status or "").lower() == "tba":
        out.next_date = start
        return out

    if today < start:
        out.episodes = 0
        out.next_date = start
        return out

    weeks = (today - start).days // 7
    n = weeks + 1
    complete = False
    if planned_total and n >= planned_total:
        n = planned_total
        complete = True
    if (status or "").lower() == "finished":
        complete = True
        if planned_total:
            n = planned_total
        else:
            # dub complete hai, par total episodes ka koi real record nahi —
            # weekly math se fake number banana forbidden hai
            n = None

    out.episodes = n
    out.complete = complete
    if complete:
        # Dub ABHI complete hai (status Finished ya weekly count planned tak pahunch
        # gaya) — completion ka future-month projection galat hota hai (Konosuba S1
        # case: Finished tha par 'Oct 2026 me complete' dikha raha tha). Isliye
        # complete_month set nahi karte; card '(complete ✅)' dikhata hai.
        out.complete_month = None
        out.next_date = None
    else:
        nxt = start + timedelta(days=7 * n)
        # Past-date guard: estimate nikal chuka hai to honest answer do
        out.next_date = None if nxt <= today else nxt
        out.estimated = True
    return out


# ---------------------------------------------------------------------------
# AniNidhi wrapper + 6h cache
# ---------------------------------------------------------------------------
class AniNidhiSource:
    def __init__(self) -> None:
        self._all: list[dict] | None = None
        self._fetched_at: float = 0.0
        self._fuzzy_titles: list[str] | None = None
        self._lock = asyncio.Lock()

    # -- raw dataset ---------------------------------------------------------
    def _load_sync(self) -> list[dict]:
        """Blocking network call — hamesha thread me chalao."""
        if aninidhi is None:
            return []
        return list(aninidhi.list_all() or [])

    async def all_records(self, force: bool = False) -> list[dict]:
        """list_all() ka 6-ghante cache."""
        if not force and self._all is not None and (time.time() - self._fetched_at) < config.ANINIDHI_TTL:
            return self._all
        async with self._lock:
            if not force and self._all is not None and (time.time() - self._fetched_at) < config.ANINIDHI_TTL:
                return self._all
            data = await asyncio.wait_for(
                asyncio.to_thread(self._load_sync), timeout=config.DUB_LOOKUP_TIMEOUT
            )
            self._all = data
            self._fetched_at = time.time()
            self._fuzzy_titles = None
            return data

    def cached_count(self) -> int:
        return len(self._all or [])

    async def age_seconds(self) -> float | None:
        return None if not self._fetched_at else time.time() - self._fetched_at

    # -- records -> DubRecord -------------------------------------------------
    def _to_records(self, rows: list[dict]) -> list[DubRecord]:
        recs: list[DubRecord] = []
        for row in rows:
            title = row.get("title") or ""
            base, season, cour, kind = parse_season(title)
            for d in row.get("hindi_dubs") or []:
                plat = d.get("platform")
                if not plat:
                    continue
                recs.append(
                    DubRecord(
                        platform=platforms.canon(plat) or plat.lower(),
                        display_platform=platforms.display(plat),
                        release_date=parse_date(d.get("release_date")),
                        status=(d.get("status") or "TBA").strip(),
                        media_type=(d.get("media_type") or kind or "series"),
                        aninidhi_title=title,
                        season=season,
                        cour=cour,
                    )
                )
        return recs

    # -- main lookup ---------------------------------------------------------
    async def lookup(self, titles: list[str], timeout: float | None = None) -> list[DubRecord]:
        """
        Title variants try karke matching AniNidhi records dhundho.
        Franchise key par match hota hai taaki "(Season 2 Cour 1)" jaise
        suffix wale records bhi pakde jaayein.
        """
        limit = timeout or config.DUB_LOOKUP_TIMEOUT
        try:
            rows = await asyncio.wait_for(self.all_records(), timeout=limit)
        except asyncio.TimeoutError:
            log.warning("AniNidhi lookup timeout (%ss) — Hindi dub data skip", limit)
            return []

        variants = title_variants(titles)
        keys = {franchise_key(v) for v in variants}
        keys.discard("")
        picked: dict[str, dict] = {}
        for row in rows:
            rtitle = row.get("title") or ""
            rkey = franchise_key(rtitle)
            if not rkey:
                continue
            # exact key match ya variant ka substring match (AniNidhi ke chhote titles ke liye)
            hit = rkey in keys
            if not hit:
                for v in variants:
                    vk = franchise_key(v)
                    if vk and (vk == rkey or vk.startswith(rkey + " ") or rkey.startswith(vk + " ")):
                        hit = True
                        break
            if hit:
                picked[rtitle] = row
        return self._to_records(list(picked.values()))

    async def group_by_season(self, titles: list[str]) -> dict[int | None, SeasonDub]:
        """Records ko season number par group karo."""
        recs = await self.lookup(titles)
        groups: dict[int | None, SeasonDub] = {}
        for r in recs:
            g = groups.setdefault(r.season, SeasonDub(season=r.season))
            g.records.append(r)
            g.matched_title = g.matched_title or r.aninidhi_title
        return groups

    # -- fuzzy (typo tolerance) ---------------------------------------------
    async def fuzzy_titles(self, query: str, cutoff: float | None = None) -> list[str]:
        """
        'mushoko tensai' -> ['Mushoku Tensei (Season 1)', ...]
        Har word ~80% similar ho to hi accept (warna garbage aayega).
        """
        rows = await self.all_records()
        if self._fuzzy_titles is None:
            self._fuzzy_titles = sorted({(r.get("title") or "") for r in rows if r.get("title")})
        cut = cutoff or config.FUZZY_CUTOFF
        qwords = [w for w in re.split(r"[^a-z0-9]+", query.lower()) if len(w) >= 3]
        if not qwords:
            return []
        good: list[tuple[float, str]] = []
        for t in self._fuzzy_titles:
            base, _s, _c, _k = parse_season(t)
            candidates = {franchise_key(t), franchise_key(base)}
            best_word_score = 1.0
            matched_words = 0
            for w in qwords:
                score = max(
                    difflib.SequenceMatcher(None, w, tw).ratio()
                    for cand in candidates
                    for tw in cand.split()
                ) if candidates else 0.0
                if score >= cut:
                    matched_words += 1
                best_word_score = min(best_word_score, score)
            if matched_words == len(qwords) and best_word_score >= cut:
                good.append((best_word_score, t))
        good.sort(reverse=True)
        return [t for _s, t in good[:10]]


# module-level singleton
source = AniNidhiSource()
