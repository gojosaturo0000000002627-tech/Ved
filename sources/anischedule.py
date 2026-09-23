"""
sources/anischedule.py — AnimeSchedule.net API v3 = ENGLISH DUB ka real source.

ZAROORI (live verify kiya, 2026-09):
  * Asli API host: https://animeschedule.net/api/v3  (purana `api.` subdomain NXDOMAIN hai)
  * `GET /anime?anilist-ids=<id>` PUBLIC hai (no token) — AniList ID se EXACT match
  * `timetables/dub` wale endpoints token maangte hain (optional ANISCHEDULE_TOKEN)
  * Rate limit: 120/min per app+IP — hum 6h cache + semaphore(3) use karte hain

Fields (real response se):
  dubPremier   = English dub ke PEHLE episode ki date (0001-01-01 = data nahi)
  dubTime      = weekly dub release time (sirf hh:mm relevant)
  episodes     = us entry ke total episodes (0 = unknown)
  dubEpisodeOverride.episodesAired = real aired count (delay/override cases)
  dubDelayedUntil  = dub is date tak delay ho gaya
  websites.aniList = "anilist.co/anime/187538/..." -> exact mapping yahi se aata hai

ENGLISH DUB MATH (Hindi weekly math jaisa hi — kabhi guess nahi):
  premier + 7-din intervals = count, total par cap; total unknown ho (One Piece
  jaisa ongoing) to count Unknown hi rehta hai — next date weekly pattern se.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import httpx

import config
from sources.aninidhi_src import franchise_key, parse_season

log = logging.getLogger("anischedule")

SENTINEL_PREFIX = "0001-01-01"


def _dt(raw) -> datetime | None:
    """API ki datetime string -> UTC datetime; sentinel/garbage -> None."""
    if not raw:
        return None
    s = str(raw).strip()
    if not s or s.startswith(SENTINEL_PREFIX):
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _d(raw) -> date | None:
    dt = _dt(raw)
    return dt.date() if dt else None


@dataclass
class AnimeScheduleInfo:
    anilist_id: int
    title: str
    route: str
    dub_premier: date | None          # EN dub first episode
    dub_time: datetime | None         # weekly EN dub time (hh:mm relevant)
    total_episodes: int | None        # 0/None = unknown
    status: str | None
    episodes_aired: int | None        # override se real aired count
    override_episode: int | None
    delayed_until: date | None        # dub delay
    streams: list = None              # [{name, platform, url}]

    @property
    def has_dub_data(self) -> bool:
        return self.dub_premier is not None

    @property
    def weekly_weekday_time(self) -> tuple[int, int, int] | None:
        """dubTime se (weekday, hour, minute) — weekly pattern."""
        if self.dub_time is None:
            return None
        return (self.dub_time.weekday(), self.dub_time.hour, self.dub_time.minute)


def next_weekday_occurrence(weekday: int, hour: int, minute: int, after: date, tz) -> datetime | None:
    """Agle <weekday> <hh:mm> ko UTC me (after ke baad wala din)."""
    days_ahead = (weekday - after.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7  # aaj ka wala guzar chuka maano — agla hafta
    target = after + timedelta(days=days_ahead)
    return datetime(target.year, target.month, target.day, hour, minute, tzinfo=tz)


@dataclass
class ENDubProgress:
    count: int | None = None          # released EN dub episodes (Unknown ho sakta hai)
    next_date: date | None = None
    next_dt: datetime | None = None   # exact time pata ho to
    complete: bool = False
    announced: bool = False           # premier future hai (dub announced, start pending)
    delayed: bool = False
    estimated: bool = False

    @property
    def known(self) -> bool:
        return self.count is not None or self.next_dt is not None or self.announced


def en_dub_progress(info: AnimeScheduleInfo | None, today: date) -> ENDubProgress:
    """
    Real EN dub data se honest progress. Guess NAHI:
      * info/dubPremier nahi   -> sab Unknown
      * premier future         -> 0 episodes, announced
      * weekly math            -> premier + 7d, total par cap (total pata ho to)
      * total unknown (ongoing)-> count Unknown, next = dubTime ka weekly pattern
      * Finished + total       -> complete
    """
    out = ENDubProgress()
    if info is None or not info.has_dub_data:
        return out

    premier = info.dub_premier
    total = info.total_episodes or None

    # override me real aired count mila to wahi sabse reliable
    aired = info.episodes_aired if (info.episodes_aired or 0) > 0 else None

    if today < premier:
        out.count = 0
        out.announced = True
        out.next_date = premier
        return out

    weeks = (today - premier).days // 7 + 1
    complete = False
    # TOTAL unknown ho to count kabhi weekly-math se mat nikalo — One Piece jaise
    # shows me dub hiatus/gaps hote hain; weeks count = galat number (real bug).
    # Tab count Unknown hi rahega (ya override ka real episodesAired chalega).
    count: int | None = weeks if total is not None else None
    if total is not None and weeks >= total:
        count = total
        complete = True
    if (info.status or "").lower() == "finished" and total is not None:
        count = total
        complete = True
    if aired is not None and not complete:
        count = max(aired, count or 0)

    out.count = count
    out.complete = complete

    # delay handling
    if info.delayed_until and info.delayed_until > today:
        out.next_date = info.delayed_until
        out.delayed = True
        out.count = aired
        return out

    if not complete:
        if total is not None and count is not None:
            nxt = premier + timedelta(days=7 * count)
            if nxt > today:
                out.next_date = nxt
                out.estimated = True
            # warna past-date guard: next Unknown
        elif count is None:
            # One Piece jaisa ongoing (total unknown) — weekly pattern se next
            pattern = info.weekly_weekday_time
            if pattern:
                wd, hh, mm = pattern
                nxt_dt = next_weekday_occurrence(wd, hh, mm, today, timezone.utc)
                if nxt_dt:
                    out.next_dt = nxt_dt
                    out.next_date = nxt_dt.date()
                    out.estimated = True
    return out


def compact_key(text: str, franchise: bool = True) -> str:
    """
    Matching key. franchise=True -> season/cour/part markers bhi hat jaate hain
    ('Jujutsu Kaisen 2' == 'Jujutsu Kaisen Season 2'). franchise=False ->
    poora title (arc titles alag rehte hain).
    """
    base = franchise_key(text) if franchise else (text or "").lower()
    return re.sub(r"[^a-z0-9]", "", base)


def _season_of_title(title: str) -> int:
    _b, season, _c, _k = parse_season(title or "", default_one=False)
    return season if season is not None else 1


def pick_candidate(rows: list[dict], titles: list[str], season_no: int | None):
    """
    q-search rows me se sahi entry (pure function — real + fake source dono use karte hain).

    Tiered scoring (galat entry pakde jaane ka risk minimum):
      5 = websites.aniList me wahi AniList ID (pakka match)
      4 = franchise-match + season-match + dub data      (JJK S2 jaisa case)
      3 = franchise-match + season-match, dub data nahi  (entry confirm, data nahi)
      2 = raw-title containment + season-match + dub data ("Dan Da Dan" vs "Dandadan")
      1 = raw-title containment, dub data nahi
    season_no None (movie) -> sirf id/equality tier (containment bahut loose hota hai).
    Na mile -> None (kabhi guess nahi).
    """
    if not rows:
        return None
    want = season_no if season_no is not None else 1
    strict_only = season_no is None   # movie: sirf pakka match
    want_ids = set()
    for t in titles:
        m = re.search(r"anilist\.co/anime/(\d+)", t or "")
        if m:
            want_ids.add(m.group(1).lstrip("0"))
    want_fr = {compact_key(t, franchise=True) for t in titles if t} - {""}
    want_raw = {compact_key(t, franchise=False) for t in titles if t} - {""}

    best = None
    best_score = -1
    for row in rows:
        title = row.get("title") or ""
        link = ((row.get("websites") or {}).get("aniList")) or ""
        has_dub = not str(row.get("dubPremier") or "").startswith(SENTINEL_PREFIX)
        mid = re.search(r"/anime/(\d+)", link)
        if mid and want_ids and mid.group(1).lstrip("0") in want_ids:
            score = 5
        else:
            c_season = _season_of_title(title)
            if c_season != want:
                continue
            c_fr = compact_key(title, franchise=True)
            c_raw = compact_key(title, franchise=False)

            def _eq(a_keys, c_key):
                return c_key in a_keys

            def _contains(a_keys, c_key):
                for wk in a_keys:
                    if wk == c_key or (len(wk) >= 5 and wk in c_key) or (len(c_key) >= 5 and c_key in wk):
                        return True
                return False

            if _eq(want_fr, c_fr):
                score = 4 if has_dub else 3
            elif strict_only:
                continue
            elif _contains(want_fr, c_fr) or _contains(want_raw, c_raw):
                score = 2 if has_dub else 1
            else:
                continue
        if score > best_score:
            best = row
            best_score = score
    return best


class AnimeScheduleSource:
    """/anime?anilist-ids= ka async client — 6h cache, errors par None (kabhi fail nahi)."""

    TTL = 6 * 3600

    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: float | None = None):
        self.base_url = (base_url if base_url is not None else config.ANISCHEDULE_URL).rstrip("/")
        self.token = (token if token is not None else config.ANISCHEDULE_TOKEN).strip()
        self.timeout = timeout or config.OPTIONAL_SOURCE_TIMEOUT
        self._cache: dict[int, tuple[float, "AnimeScheduleInfo | None"]] = {}
        self._q_cache: dict[str, tuple[float, list[dict]]] = {}
        self._sem = asyncio.Semaphore(3)
        self._client: httpx.AsyncClient | None = None
        self.requests_made = 0

    @property
    def enabled(self) -> bool:
        # Public /anime endpoint — token optional (sirf timetables ke liye chahiye)
        return bool(self.base_url)

    def _headers(self) -> dict:
        h = {"Accept": "application/json", "User-Agent": config.USER_AGENT}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    async def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout, headers=self._headers())
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    # -- cache ---------------------------------------------------------------
    def _cached(self, anilist_id: int):
        hit = self._cache.get(anilist_id)
        if hit and (time.time() - hit[0]) < self.TTL:
            return hit[1]
        return "__MISS__"

    def cached_count(self) -> int:
        return len(self._cache)

    # -- public API ----------------------------------------------------------
    async def lookup_many(self, anilist_ids: list[int]) -> dict[int, AnimeScheduleInfo | None]:
        """
        Bahut saare AniList IDs -> {id: Info|None}. Cache miss par parallel
        fetch (max 3 ek saath). Har error par None (card kabhi block nahi hota).
        """
        wanted = [i for i in dict.fromkeys(anilist_ids) if i]
        out: dict[int, AnimeScheduleInfo | None] = {}
        missing: list[int] = []
        for i in wanted:
            got = self._cached(i)
            if got == "__MISS__":
                missing.append(i)
            else:
                out[i] = got
        if missing:
            async def one(i: int):
                async with self._sem:
                    return await self._fetch_one(i)

            results = await asyncio.gather(*(one(i) for i in missing), return_exceptions=True)
            for i, res in zip(missing, results):
                info = None if isinstance(res, BaseException) else res
                if isinstance(res, BaseException):
                    log.info("anischedule fetch fail (%s): %s", i, res)
                self._cache[i] = (time.time(), info)
                out[i] = info
        return out

    async def search_dub(self, titles: list[str], season_no: int | None = None) -> AnimeScheduleInfo | None:
        """
        NAME-SEARCH fallback: 'SPY×FAMILY' jaisi entries jin ka AniList-ID record
        hi nahi hai (404), title search se milte hain. Match: compact-title +
        season number. Dub data nahi mila -> None (honest Unknown).
        """
        no_dub: AnimeScheduleInfo | None = None
        for t in [t for t in titles if t][:2]:
            key = t.strip().lower()
            hit = self._q_cache.get(key)
            if hit and (time.time() - hit[0]) < self.TTL:
                rows = hit[1]
            else:
                try:
                    async with self._sem:
                        c = await self.client()
                        self.requests_made += 1
                        resp = await c.get(f"{self.base_url}/anime", params={"q": t})
                        rows = (resp.json() or {}).get("anime") or [] if resp.status_code == 200 else []
                except Exception as exc:  # noqa: BLE001
                    log.info("anischedule q-search fail (%s): %s", t, exc)
                    rows = []
                self._q_cache[key] = (time.time(), rows)
            row = pick_candidate(rows, titles, season_no)
            if row is None:
                continue
            info = self._parse(0, row)
            if info.has_dub_data:
                return info
            no_dub = no_dub or info
        return no_dub

    async def _fetch_one(self, anilist_id: int) -> AnimeScheduleInfo | None:
        url = f"{self.base_url}/anime"
        try:
            c = await self.client()
            self.requests_made += 1
            resp = await c.get(url, params={"anilist-ids": str(anilist_id)})
            if resp.status_code != 200:
                log.info("anischedule HTTP %s (anilist %s)", resp.status_code, anilist_id)
                return None
            rows = (resp.json() or {}).get("anime") or []
            if not rows:
                return None
            return self._parse(anilist_id, rows[0])
        except Exception as exc:  # noqa: BLE001 - optional source, fail = None
            log.info("anischedule fetch fail (%s): %s", anilist_id, exc)
            return None

    @staticmethod
    def _parse(anilist_id: int, row: dict) -> AnimeScheduleInfo:
        ov = row.get("dubEpisodeOverride") or {}
        websites = row.get("websites") or {}
        streams = [
            {"name": s.get("name"), "platform": s.get("platform"), "url": s.get("url")}
            for s in (websites.get("streams") or [])
            if s.get("name")
        ]
        eps = row.get("episodes")
        return AnimeScheduleInfo(
            anilist_id=anilist_id,
            title=row.get("title") or "",
            route=row.get("route") or "",
            dub_premier=_d(row.get("dubPremier")),
            dub_time=_dt(row.get("dubTime")),
            total_episodes=int(eps) if isinstance(eps, (int, float)) and int(eps) > 0 else None,
            status=row.get("status"),
            episodes_aired=int(ov["episodesAired"]) if isinstance(ov.get("episodesAired"), (int, float)) else None,
            override_episode=int(ov["overrideEpisode"]) if isinstance(ov.get("overrideEpisode"), (int, float)) else None,
            delayed_until=_d(row.get("dubDelayedUntil")),
            streams=streams,
        )


# module-level singleton
source = AnimeScheduleSource()
