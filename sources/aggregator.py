"""
sources/aggregator.py — saare sources ko merge karna + search logic + season logic.

Yahi bot ka dimaag hai:
  * smart search (direct card vs pick list, typo tolerance, garbage guard)
  * season chain (PREQUEL/SEQUEL BFS + batch fetch) + cours merging
  * CURRENT SEASON rule (S1 search karo, S3 ka data dikhe)
  * Hindi dub counts (AniNidhi weekly math + YouTube cross-check)
  * manual overrides (/setep + overrides.json) — sabse high priority
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import config
from sources import anilist as anilist_mod
from sources import aninidhi_src, anischedule, dubinfo, platforms, youtube

log = logging.getLogger("aggregator")

SERIES_FORMATS = {"TV", "TV_SHORT", "ONA"}
EXTRA_FORMATS = {"MOVIE", "SPECIAL", "OVA", "MUSIC"}
CHAIN_RELATIONS = {"PREQUEL", "SEQUEL"}
EXTRA_RELATIONS = {"PARENT", "SIDE_STORY", "SPIN_OFF", "OTHER", "ALTERNATIVE"}

_WORD_SPLIT = re.compile(r"[^a-z0-9]+")


def norm_words(text: str) -> list[str]:
    return [w for w in _WORD_SPLIT.split((text or "").lower()) if w]


def significant_words(text: str, min_len: int = 3) -> set[str]:
    return {w for w in norm_words(text) if len(w) >= min_len}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class SeasonInfo:
    number: int | None = None
    label: str = "Season 1"
    entry_ids: list[int] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)
    planned: int | None = None
    released: int | None = None
    year: int | None = None
    ongoing: bool = False
    not_yet_released: bool = False
    formats: list[str] = field(default_factory=list)
    jp_count: int | None = None
    en_count: int | None = None
    hi_count: int | None = None
    hi_platforms: list[str] = field(default_factory=list)
    hi_start: date | None = None
    hi_status: str | None = None
    hi_next: date | None = None
    hi_complete_month: str | None = None
    next_airing_at: int | None = None
    next_airing_ep: int | None = None
    override: dict = field(default_factory=dict)

    @property
    def jp_next_dt(self) -> datetime | None:
        if not self.next_airing_at:
            return None
        return datetime.fromtimestamp(self.next_airing_at, tz=timezone.utc)

    def progress_text(self) -> str:
        if self.planned is None and self.released is None:
            return "episodes unknown"
        if self.planned is None:
            return f"{self.released} episodes released"
        if self.released is None:
            return f"{self.planned} episodes planned"
        return f"{self.released}/{self.planned} episodes"


@dataclass
class ExtraInfo:
    title: str
    year: int | None
    format: str | None
    minutes: int | None
    hindi: str            # 'Available ✅' | 'No official Hindi dub found' | 'Unknown'
    hi_platforms: list[str] = field(default_factory=list)
    entry_id: int | None = None
    url: str | None = None


@dataclass
class CardData:
    anime_id: int
    query: str = ""
    title: str = ""
    romaji: str | None = None
    native: str | None = None
    kind: str = "series"                       # series | movie | special | ova
    status_text: str = "Unknown"
    is_complete: bool = False
    seasons: list[SeasonInfo] = field(default_factory=list)
    extras: list[ExtraInfo] = field(default_factory=list)
    current_number: int | None = None
    current_label: str = ""
    planned_total: int | None = None
    jp: int | None = None
    en: int | None = None
    hi: int | None = None
    hindi_found: bool = False
    hi_platforms: list[str] = field(default_factory=list)
    streaming_platforms: list[str] = field(default_factory=list)
    streaming_links: list[dict] = field(default_factory=list)   # [{site, url}]
    audio_langs: list[str] = field(default_factory=list)
    sub_langs: list[str] = field(default_factory=list)
    next_jp_dt: datetime | None = None
    next_jp_text: str = "To be announced"
    next_en_text: str = "To be announced"
    next_hi_date: date | None = None
    next_hi_text: str = "To be announced"
    movie_minutes: int | None = None
    movie_date: date | None = None
    movie_hindi: str = "Unknown"
    last_checked: datetime = field(default_factory=config.now_utc)
    anilist_url: str = ""
    watch_url: str | None = None
    watch_platform: str | None = None
    yt_hits: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    # -- (de)serialization: SQLite card cache ke liye ------------------------
    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["last_checked"] = self.last_checked.isoformat()
        for s in d["seasons"]:
            for f in ("hi_start", "hi_next"):
                s[f] = s[f].isoformat() if s[f] else None
        d["next_jp_dt"] = self.next_jp_dt.isoformat() if self.next_jp_dt else None
        d["next_hi_date"] = self.next_hi_date.isoformat() if self.next_hi_date else None
        d["movie_date"] = self.movie_date.isoformat() if self.movie_date else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CardData":
        d = dict(d)
        seasons = []
        for s in d.pop("seasons", []):
            s = dict(s)
            for f in ("hi_start", "hi_next"):
                s[f] = date.fromisoformat(s[f]) if s.get(f) else None
            seasons.append(SeasonInfo(**s))
        extras = [ExtraInfo(**e) for e in d.pop("extras", [])]
        d["seasons"] = seasons
        d["extras"] = extras
        d["last_checked"] = datetime.fromisoformat(d["last_checked"]) if d.get("last_checked") else config.now_utc()
        d["next_jp_dt"] = datetime.fromisoformat(d["next_jp_dt"]) if d.get("next_jp_dt") else None
        d["next_hi_date"] = date.fromisoformat(d["next_hi_date"]) if d.get("next_hi_date") else None
        d["movie_date"] = date.fromisoformat(d["movie_date"]) if d.get("movie_date") else None
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class SearchOutcome:
    """Search ka nateeja: ya to ek chosen entry, ya pick list, ya not-found."""

    query: str = ""
    chosen: anilist_mod.MediaEntry | None = None
    candidates: list[anilist_mod.MediaEntry] = field(default_factory=list)
    matched_by: str = ""
    not_found: bool = False
    suggestions: list[str] = field(default_factory=list)   # fuzzy se mile asli titles

    @property
    def needs_pick(self) -> bool:
        return self.chosen is None and bool(self.candidates)


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------
class Aggregator:
    def __init__(self, anilist=None, aninidhi=None, yt=None, dinfo=None, sched=None, cache=None):
        self.anilist = anilist or anilist_mod.client
        self.aninidhi = aninidhi or aninidhi_src.source
        self.yt = yt or youtube.source
        self.dubinfo = dinfo or dubinfo.source
        self.schedule = sched or anischedule.source
        self.cache = cache            # database.Database (card cache) ya None

    # =======================================================================
    # SEARCH
    # =======================================================================
    async def resolve_search(self, raw_query: str) -> SearchOutcome:
        """
        /search ka pura flow:
          1. direct AniList search (1 result -> card)
          2. franchise pick (2+ same franchise -> direct card)
          3. exact match (word-for-word)
          4. best match (saare words ek hi title me)
          5. genuinely ambiguous -> pick list
          6. typo tolerance (AniNidhi titles par difflib)
          7. word-subset fallback (sirf 3+ word query, pehle 2 words)
          * kabhi 1-word fallback nahi — garbage list strictly forbidden
        """
        query = (raw_query or "").strip()
        if not query:
            return SearchOutcome(query=query, not_found=True)

        # numeric = AniList ID shortcut
        if query.isdigit() and 2 <= len(query) <= 7:
            entry = await self.anilist.get(int(query))
            if entry:
                return SearchOutcome(query=query, chosen=entry, matched_by="id")
            return SearchOutcome(query=query, not_found=True)

        entries = await self._safe_search(query)
        outcome = self._pick_from_entries(entries, query)
        if outcome:
            return outcome

        # --- 6. typo tolerance: AniNidhi titles par fuzzy -------------------
        suggestions = await self._fuzzy_suggestions(query)
        for sug in suggestions:
            base, _s, _c, _k = aninidhi_src.parse_season(sug)
            for cand_query in {base, sug}:
                entries2 = await self._safe_search(cand_query)
                if not entries2:
                    continue
                picked = self._pick_from_entries(entries2, cand_query, allow_ambiguous=False)
                if picked and picked.chosen:
                    picked.query = query
                    picked.suggestions = suggestions
                    picked.matched_by = f"typo:{picked.matched_by}"
                    return picked

        # --- 7. word-subset fallback: sirf 3+ word query, pehle 2 words -----
        words = norm_words(query)
        if len(words) >= 3:
            subset = " ".join(words[:2])
            entries3 = await self._safe_search(subset)
            if entries3:
                picked = self._pick_from_entries(entries3, subset, allow_ambiguous=True)
                if picked and (picked.chosen or picked.candidates):
                    picked.query = query
                    picked.matched_by = f"subset:{picked.matched_by}"
                    picked.suggestions = suggestions
                    return picked

        return SearchOutcome(query=query, not_found=True, suggestions=suggestions)

    async def _safe_search(self, query: str) -> list[anilist_mod.MediaEntry]:
        try:
            return await self.anilist.search(query, per_page=12)
        except ConnectionError as exc:
            log.warning("AniList search fail: %s", exc)
            return []

    async def _fuzzy_suggestions(self, query: str) -> list[str]:
        try:
            return await self.aninidhi.fuzzy_titles(query)
        except Exception as exc:
            log.info("fuzzy fail: %s", exc)
            return []

    @staticmethod
    def _main_titles(e) -> list[str]:
        return [t for t in (e.english, e.romaji, e.native) if t and t.strip()]

    # -- selection rules -----------------------------------------------------
    def _pick_from_entries(
        self,
        entries: list[anilist_mod.MediaEntry],
        query: str,
        allow_ambiguous: bool = True,
    ) -> SearchOutcome | None:
        if not entries:
            return None
        q_norm = query.strip().lower()
        q_words = norm_words(query)
        q_sig = significant_words(query)

        # --- 3. exact match (title exactly = query; sirf main titles —
        #        synonyms par match Onigiri="Demon Slayer" jaisa garbage deta hai)
        exact = []
        for e in entries:
            for t in self._main_titles(e):
                t_norm = t.lower().strip()
                t_noparen = re.sub(r"\s*\([^)]*\)\s*", " ", t_norm).strip()
                if q_norm in (t_norm, t_noparen, re.sub(r"[^a-z0-9 ]+", "", t_norm).strip()):
                    exact.append(e)
                    break
        if len(exact) == 1:
            return SearchOutcome(query=query, chosen=exact[0], matched_by="exact")
        if len(exact) > 1:
            # TV series ko movie/special par preference
            exact.sort(key=lambda e: 0 if e.format in SERIES_FORMATS else 1)
            if exact[0].format in SERIES_FORMATS and exact[1].format not in SERIES_FORMATS:
                return SearchOutcome(query=query, chosen=exact[0], matched_by="exact")

        # --- 1. single result -> turant card --------------------------------
        if len(entries) == 1:
            return SearchOutcome(query=query, chosen=entries[0], matched_by="single")

        # --- 2. franchise pick ---------------------------------------------
        top = entries[0]
        top_keys = {anilist_src_key(t) for t in top.titles()}
        top_keys.discard("")
        same = [e for e in entries if top_keys & {anilist_src_key(t) for t in e.titles()} - {""}]
        if len(same) >= 2:
            chosen = top if top.format in SERIES_FORMATS else next(
                (e for e in same if e.format in SERIES_FORMATS), top
            )
            return SearchOutcome(query=query, chosen=chosen, matched_by="franchise")

        # --- 3b. exact word-set match --------------------------------------
        wordset = [e for e in entries if any(set(q_words) == set(norm_words(t)) for t in e.titles())]
        if len(wordset) == 1:
            return SearchOutcome(query=query, chosen=wordset[0], matched_by="wordset")

        # --- 4. best match: saare 3+ char words ek hi entry me --------------
        if q_sig:
            hits = [e for e in entries if any(q_sig <= set(norm_words(t)) for t in e.titles())]
            if len(hits) == 1:
                return SearchOutcome(query=query, chosen=hits[0], matched_by="best")

        # --- 5. genuinely ambiguous ----------------------------------------
        if allow_ambiguous:
            return SearchOutcome(query=query, candidates=entries[: config.MAX_PICK_LIST], matched_by="ambiguous")
        return None

    # =======================================================================
    # CARD
    # =======================================================================
    async def build_card(self, anime_id: int, query: str = "", force: bool = False) -> CardData | None:
        """Card data banao. force=False par 30-min SQLite card cache use hota hai."""
        if not force and self.cache is not None:
            cached = self.cache.get_card(anime_id)
            if cached:
                try:
                    data = CardData.from_dict(json.loads(cached))
                    data.query = query or data.query
                    return data
                except Exception as exc:
                    log.warning("card cache corrupt, rebuild: %s", exc)

        base = await self.anilist.get(anime_id, force=force)
        if base is None:
            return None

        # saare network sources parallel — ek slow source card block na kare
        chain_task = asyncio.create_task(self._season_chain(base))
        dub_task = asyncio.create_task(self._dub_groups(base))
        extra_task = asyncio.create_task(self._optional_sources(base))

        seasons_raw, extras_raw = await chain_task
        dub_groups = await dub_task
        optional = await extra_task

        data = self._assemble(base, seasons_raw, extras_raw, dub_groups, optional, query)

        # YouTube cross-check (real uploads = real evidence)
        try:
            data.yt_hits = await asyncio.wait_for(
                self.yt.scan(base.titles(), season=data.current_number),
                timeout=config.YOUTUBE_TIMEOUT + 5,
            )
        except Exception as exc:
            log.info("youtube scan skip: %s", exc)
            data.yt_hits = []
        self._apply_youtube(data)

        # manual overrides — sabse high priority
        self._apply_overrides(data)

        data.last_checked = config.now_utc()
        if self.cache is not None:
            try:
                self.cache.set_card(anime_id, json.dumps(data.to_dict()))
            except Exception as exc:
                log.warning("card cache save fail: %s", exc)
        return data

    async def build_card_by_query(self, query: str, force: bool = False) -> tuple[SearchOutcome, CardData | None]:
        outcome = await self.resolve_search(query)
        if outcome.chosen is None:
            return outcome, None
        data = await self.build_card(outcome.chosen.id, query=query, force=force)
        return outcome, data

    # -- season chain --------------------------------------------------------
    async def _season_chain(self, base: anilist_mod.MediaEntry):
        """
        PREQUEL/SEQUEL relations par level-wise BFS (har level = 1 batch request),
        plus franchise-key search fallback (AniNidhi jaise unlinked entries ke liye).
        Returns (series_entries, extra_entries)
        """
        seen: dict[int, anilist_mod.MediaEntry] = {base.id: base}
        frontier = [base.id]
        series: dict[int, anilist_mod.MediaEntry] = {base.id: base} if base.format in SERIES_FORMATS else {}
        extras: dict[int, anilist_mod.MediaEntry] = {}
        if base.format in EXTRA_FORMATS:
            extras[base.id] = base

        for _level in range(config.MAX_SEASON_CHAIN):
            wanted: set[int] = set()
            for eid in frontier:
                entry = seen.get(eid)
                if not entry:
                    continue
                for rel in entry.relations:
                    if rel["type"] in CHAIN_RELATIONS and rel.get("id") and rel["id"] not in seen:
                        wanted.add(rel["id"])
                    elif rel["type"] in EXTRA_RELATIONS and rel.get("id") and rel["id"] not in seen:
                        # extras sirf tab, jab relation node ka format extra ho
                        if rel.get("format") in EXTRA_FORMATS:
                            wanted.add(rel["id"])
            if not wanted:
                break
            try:
                fetched = await self.anilist.many(sorted(wanted))
            except ConnectionError as exc:
                log.warning("chain batch fail: %s", exc)
                break
            new_frontier = []
            for e in fetched:
                seen[e.id] = e
                if e.format in SERIES_FORMATS:
                    series[e.id] = e
                    new_frontier.append(e.id)
                elif e.format in EXTRA_FORMATS:
                    extras[e.id] = e
            frontier = new_frontier
            if not frontier:
                break

        # franchise search fallback: "Grand Blue Season 3" relation me na ho tab bhi pakda jaaye
        base_keys = {anilist_src_key(t) for t in _main_titles(base)}
        base_keys.discard("")
        for t in _main_titles(base)[:2]:
            head = t.split(":")[0].strip()
            try:
                found = await self.anilist.search(head, per_page=12)
            except ConnectionError:
                break
            for e in found:
                if e.id in seen:
                    continue
                if not self._franchise_match(e, base_keys):
                    continue
                seen[e.id] = e
                if e.format in SERIES_FORMATS:
                    series[e.id] = e
                elif e.format in EXTRA_FORMATS:
                    extras[e.id] = e
            break

        # Relation-noise filter: AniList kabhi-kabhi ajeeb PREQUEL/SEQUEL lagata hai
        # (jaise ONE PIECE ka PREQUEL = MONSTERS ONA). Jo entry base franchise se
        # hi nahi hai use series se hata ke extras me daal do.
        base_keys = {anilist_src_key(t) for t in _main_titles(base)} - {""}
        kept: list[anilist_mod.MediaEntry] = []
        for e in series.values():
            if e.id == base.id or self._franchise_match(e, base_keys):
                kept.append(e)
            else:
                extras[e.id] = e
        return kept, list(extras.values())

    async def _dub_groups(self, base: anilist_mod.MediaEntry) -> dict:
        """AniNidhi records ko season-wise group karo (25-40s cap)."""
        try:
            return await asyncio.wait_for(
                self.aninidhi.group_by_season(base.titles()),
                timeout=config.DUB_LOOKUP_TIMEOUT,
            )
        except Exception as exc:
            log.info("AniNidhi lookup fail: %s", exc)
            return {}

    async def _optional_sources(self, base: anilist_mod.MediaEntry) -> dict:
        """dubinfo + anischedule — dono optional, dono timeout ke saath."""
        out: dict = {"dubinfo": [], "schedule": None}
        if not self.dubinfo.enabled and not self.schedule.enabled:
            return out
        tasks = []
        if self.dubinfo.enabled:
            tasks.append(("dubinfo", asyncio.create_task(self.dubinfo.lookup(base.titles()))))
        if self.schedule.enabled:
            tasks.append(("schedule", asyncio.create_task(self.schedule.next_episode(base.best_title))))
        for name, task in tasks:
            try:
                out[name] = await asyncio.wait_for(task, timeout=config.OPTIONAL_SOURCE_TIMEOUT)
            except Exception as exc:
                log.info("%s fail: %s", name, exc)
        return out

    # -- assembly ------------------------------------------------------------
    def _assemble(
        self,
        base: anilist_mod.MediaEntry,
        series_entries: list[anilist_mod.MediaEntry],
        extra_entries: list[anilist_mod.MediaEntry],
        dub_groups: dict,
        optional: dict,
        query: str,
    ) -> CardData:
        today = config.now_utc().date()
        seasons = self._merge_cours(series_entries)
        seasons = self._fill_japanese(seasons)
        seasons = self._fill_hindi(seasons, dub_groups, today)

        data = CardData(
            anime_id=base.id,
            query=query,
            title=base.best_title,
            romaji=base.romaji,
            native=base.native,
            anilist_url=f"https://anilist.co/anime/{base.id}",
        )

        # ---------------- MOVIE / SPECIAL / OVA card -----------------------
        if base.format in EXTRA_FORMATS or (not seasons and base.format not in SERIES_FORMATS):
            data.kind = {"MOVIE": "movie", "SPECIAL": "special", "OVA": "ova"}.get(base.format or "", "special")
            data.seasons = []
            data.movie_minutes = base.duration
            data.movie_date = self._date_of(base)
            data.is_complete = (base.status or "").upper() in ("FINISHED",)
            data.status_text = self._status_text(base.status, ongoing=False, released=False, not_yet=(base.status or "").upper() == "NOT_YET_RELEASED")
            dub = self._dub_for_season(dub_groups, None) or self._dub_for_season(dub_groups, 1)
            data.hindi_found = bool(dub and dub.records)
            if data.hindi_found:
                data.movie_hindi = "Available ✅"
                data.hi_platforms = dub.platforms if dub else []
            else:
                data.movie_hindi = "No official Hindi dub found"
            # extras me khud ko mat dikhao
            data.extras = [self._extra_info(e, dub_groups) for e in extra_entries if e.id != base.id]
            data.streaming_platforms = platforms.dedupe_preserve([s["site"] for s in base.streaming])
            data.streaming_links = [
                {"site": platforms.display(s["site"]), "url": s.get("url")} for s in base.streaming
            ]
            data.audio_langs, data.sub_langs = self._languages(data, base)
            data.watch_platform, data.watch_url = self._watch(base, data)
            return data

        # ---------------- SERIES card --------------------------------------
        data.kind = "series"
        data.seasons = seasons
        data.extras = [self._extra_info(e, dub_groups) for e in extra_entries if e.id != base.id]
        current = self._current_season(seasons)
        data.current_number = current.number if current else None
        data.current_label = current.label if current else (seasons[0].label if seasons else "Season 1")
        if current:
            data.planned_total = current.planned
            data.jp = current.jp_count
            data.en = current.en_count
            data.hi = current.hi_count
            data.hi_platforms = current.hi_platforms
            data.hindi_found = any(s.hi_count is not None or s.hi_platforms for s in seasons)
            data.next_jp_dt = current.jp_next_dt
            data.next_hi_date = current.hi_next
        else:
            data.hindi_found = False

        # status: koi bhi season JP me airing ho to Ongoing (spec ka rule)
        any_ongoing = any(s.ongoing for s in seasons)
        any_released = any(bool(s.released) for s in seasons)
        any_nyr = any(s.not_yet_released for s in seasons)
        data.is_complete = bool(any_released and not any_ongoing)
        data.status_text = self._status_text(
            base.status, ongoing=any_ongoing, released=any_released, not_yet=any_nyr and not any_released
        )

        # next episode texts
        data.next_jp_text = self._jp_next_text(current)
        data.next_hi_text = self._hi_next_text(current, today)
        data.next_en_text = self._en_next_text(optional)

        # optional dubinfo: English/Hindi counts (agar real data mile)
        self._apply_dubinfo(data, optional, current)

        # streaming platforms: saare seasons + base ke links
        all_links: list[dict] = []
        for e in [base]:
            all_links.extend(e.streaming)
        data.streaming_platforms = platforms.dedupe_preserve([s["site"] for s in all_links])
        data.streaming_links = [
            {"site": platforms.display(s["site"]), "url": s.get("url")} for s in all_links if s.get("url")
        ]
        data.audio_langs, data.sub_langs = self._languages(data, base)
        data.watch_platform, data.watch_url = self._watch(base, data)
        return data

    def _merge_cours(self, entries: list[anilist_mod.MediaEntry]) -> list[SeasonInfo]:
        """
        Cours merging: AniList 'Part 2' / 'Cour 2' ko alag entry rakhta hai —
        season number + base title par group karke merge karte hain.
        """
        groups: dict[tuple, SeasonInfo] = {}
        for e in entries:
            season_no = self._season_number_of(e)
            # explicit season number -> wahi key (cours merge ho jata hai);
            # unknown -> season IDENTITY: title se Part/Cour number hata ke.
            #   * "…Part 2" apne base season me merge hota hai (Mushoku S1 Cour 2)
            #   * par "…Arc" titles alag rehte hain (Demon Slayer seasons)
            if season_no is not None:
                key = (season_no, "")
            else:
                identity = re.sub(r"[^a-z0-9]+", " ", e.best_title.lower()).strip()
                identity = re.sub(r"\s*(?:part|cour|cours)\s*[-–—]?\s*\d{1,2}\s*$", "", identity).strip()
                key = (0, identity)
            g = groups.get(key)
            if g is None:
                g = SeasonInfo(number=season_no, entry_ids=[], titles=[], formats=[])
                groups[key] = g
            g.entry_ids.append(e.id)
            g.formats.append(e.format or "")
            for t in e.titles()[:2]:
                if t not in g.titles:
                    g.titles.append(t)
        ordered = self._renumber_seasons(list(groups.values()))
        for idx, s in enumerate(ordered, start=1):
            s.label = f"Season {s.number or idx}"
            if not s.year:
                years = [self.anilist.cached_entry(i).year for i in s.entry_ids if self.anilist.cached_entry(i)]
                years = [y for y in years if y]
                s.year = min(years) if years else None
        return ordered

    def _start_key(self, g: SeasonInfo):
        """Season group ki shuruaat ki date (chronological ordering ke liye)."""
        dates = []
        for eid in g.entry_ids:
            e = self.anilist.cached_entry(eid)
            if e:
                d = self._date_of(e)
                if d:
                    dates.append(d)
                elif e.year:
                    dates.append(date(e.year, 12, 31))
        return min(dates) if dates else date(9999, 12, 31)

    def _renumber_seasons(self, group_list: list[SeasonInfo]) -> list[SeasonInfo]:
        """
        Numbering:
          * explicit numbers (Season 2 / II) unki jagah par rehte hain
          * bina marker wale groups (base + uske cours) chronologically pehle
            aate hain -> 1 se number (base season = 1)
          * duplicate number par sabko sequential renumber kar do
        """
        implicit = [g for g in group_list if g.number is None]
        implicit.sort(key=self._start_key)
        for i, g in enumerate(implicit, start=1):
            g.number = i
        ordered = sorted(group_list, key=lambda g: g.number or 0)

        # duplicate-number guard: (cours merge ke baad bhi takraye to)
        by_no: dict[int, int] = {}
        for g in ordered:
            by_no[g.number] = by_no.get(g.number, 0) + 1
        if any(c > 1 for c in by_no.values()):
            ordered.sort(key=lambda g: (g.number or 0, self._start_key(g)))
            for i, g in enumerate(ordered, start=1):
                g.number = i
        return ordered

    @staticmethod
    def _season_number_of(e: anilist_mod.MediaEntry) -> int | None:
        """
        AniList title se EXPLICIT season number (roman numerals bhi).
        default_one=False: bina marker wale title ka matlab season 1 NahI hai —
        warna saari arc entries ek hi season me merge ho jaati (Demon Slayer case).
        """
        for t in e.titles():
            _base, season, _cour, _kind = aninidhi_src.parse_season(t, default_one=False)
            if season is not None:
                return season
        return None

    @staticmethod
    def _franchise_match(e: anilist_mod.MediaEntry, base_keys: set[str]) -> bool:
        """Main titles (EN/RJ/Native) se franchise match — synonyms nahi
        (warna Onigiri ke 'Demon Slayer' synonym se galat entry ghus jaati)."""
        ekeys = {anilist_src_key(t) for t in _main_titles(e)} - {""}
        return bool(ekeys & base_keys)

    def _fill_japanese(self, seasons: list[SeasonInfo]) -> list[SeasonInfo]:
        """Har season ke entries se planned/released/airing nikalo (real AniList numbers)."""
        for s in seasons:
            entries = [self.anilist.cached_entry(i) for i in s.entry_ids]
            entries = [e for e in entries if e]
            planned = 0
            planned_known = True
            released = 0
            released_known = True
            year = None
            for e in entries:
                if e.episodes:
                    planned += e.episodes
                else:
                    planned_known = False
                status = (e.status or "").upper()
                if status == "FINISHED":
                    released += e.episodes or 0
                elif status == "RELEASING":
                    s.ongoing = True
                    na = e.next_airing or {}
                    if na.get("episode"):
                        released += max(0, int(na["episode"]) - 1)
                        if na.get("airingAt"):
                            s.next_airing_at = int(na["airingAt"])
                            s.next_airing_ep = int(na["episode"])
                    else:
                        released_known = False
                elif status == "NOT_YET_RELEASED":
                    s.not_yet_released = True
                    released += 0  # abhi kuch release nahi hua, ye real fact hai
                else:
                    released_known = False
                y = e.year
                if y and (year is None or y < year):
                    year = y
            s.planned = planned if planned_known else (planned or None)
            s.released = released if released_known else None
            s.year = s.year or year
            s.jp_count = s.released
        return seasons

    def _fill_hindi(self, seasons: list[SeasonInfo], dub_groups: dict, today: date) -> list[SeasonInfo]:
        """AniNidhi weekly math se har season ka Hindi dub count."""
        for s in seasons:
            dub = dub_groups.get(s.number) or dub_groups.get(None if s.number == 1 else -1)
            if not dub or not dub.records:
                continue
            usable = [r for r in dub.records if r.usable]
            if not usable:
                continue
            start = dub.start
            statuses = [r.status for r in usable]
            status = "Airing" if any(st.lower() == "airing" for st in statuses) else statuses[0]
            prog = aninidhi_src.weekly_progress(start, status, s.planned, today)
            s.hi_start = start
            s.hi_status = status
            s.hi_platforms = dub.platforms
            s.hi_count = prog.episodes
            s.hi_next = prog.next_date
            s.hi_complete_month = prog.complete_month
        return seasons

    @staticmethod
    def _dub_for_season(dub_groups: dict, number: int | None):
        if not dub_groups:
            return None
        return dub_groups.get(number)

    @staticmethod
    def _current_season(seasons: list[SeasonInfo]) -> SeasonInfo | None:
        """
        CURRENT SEASON — notification engine inhi top-level counts ko track karta hai,
        isliye ye choice critical hai. Priority:
          1. Japanese audio me airing season (highest number)
          2. jiska Hindi dub abhi airing hai (JP khatam, dub chal raha ho — Grand Blue jaisa case)
          3. sabse naya released season
          4. announced season (agar kuch bhi release nahi hua)
        """
        if not seasons:
            return None
        key = lambda s: s.number or 0
        ongoing = [s for s in seasons if s.ongoing]
        if ongoing:
            return max(ongoing, key=key)
        dub_airing = [
            s for s in seasons
            if (s.hi_status or "").lower() == "airing" and s.hi_count is not None
        ]
        if dub_airing:
            return max(dub_airing, key=key)
        released = [s for s in seasons if s.released]
        if released:
            return max(released, key=key)
        nyr = [s for s in seasons if s.not_yet_released]
        if nyr:
            return max(nyr, key=key)
        return max(seasons, key=key)

    # -- text helpers --------------------------------------------------------
    @staticmethod
    def _status_text(status: str | None, ongoing: bool, released: bool, not_yet: bool) -> str:
        up = (status or "").upper()
        if ongoing:
            return "Ongoing"
        if not_yet or (up == "NOT_YET_RELEASED" and not released):
            return "Upcoming"
        if up in ("FINISHED",) or released:
            return "Completed ✅"
        if up == "CANCELLED":
            return "Cancelled"
        if up == "HIATUS":
            return "Hiatus"
        return "Unknown"

    def _jp_next_text(self, current: SeasonInfo | None) -> str:
        if current is None:
            return "To be announced"
        if current.ongoing and current.next_airing_at:
            return config.ts_ist(current.jp_next_dt)
        if current.jp_count is not None and current.planned is not None and current.jp_count >= current.planned:
            return "All episodes released"
        if not current.ongoing and not current.not_yet_released:
            return "All episodes released"
        return "To be announced"

    def _hi_next_text(self, current: SeasonInfo | None, today: date) -> str:
        if current is None:
            return "No official Hindi dub found"
        if not current.hi_platforms and current.hi_count is None:
            return "No official Hindi dub found"
        if (current.hi_status or "").lower() == "tba":
            return "Announced — date to be announced"
        if (current.hi_status or "").lower() == "finished":
            return "All episodes released"
        # PAST-DATE GUARD: estimate ki date nikal chuki ho to "To be announced"
        if current.hi_next and current.hi_next > today:
            return f"{config.date_ist_short(current.hi_next)} (estimated)"
        if current.hi_count is not None and current.planned and current.hi_count >= current.planned:
            return "All episodes released"
        if current.hi_count == 0 and current.hi_start and current.hi_start > today:
            return f"{config.date_ist_short(current.hi_start)} (estimated)"
        return "To be announced"

    @staticmethod
    def _en_next_text(optional: dict) -> str:
        sched = optional.get("schedule") if optional else None
        # AnimeSchedule ka data Japanese schedule hai, English dub ka nahi -> honest answer
        del sched
        return "To be announced"

    def _apply_dubinfo(self, data: CardData, optional: dict, current: SeasonInfo | None) -> None:
        rows = optional.get("dubinfo") or []
        if not rows or current is None:
            return
        for r in rows:
            if r.language == "english" and r.episodes is not None:
                current.en_count = r.episodes
                data.en = r.episodes
                if r.next_episode:
                    data.next_en_text = config.date_ist_short(r.next_episode)
            elif r.language == "hindi" and r.episodes is not None:
                # real API data estimate se zyada reliable
                current.hi_count = r.episodes
                data.hi = r.episodes
                if r.platform:
                    current.hi_platforms = platforms.dedupe_preserve(current.hi_platforms + [r.platform])
                    data.hi_platforms = current.hi_platforms
                data.hindi_found = True
                if r.next_episode:
                    data.next_hi_date = r.next_episode
                    data.next_hi_text = f"{config.date_ist_short(r.next_episode)} (estimated)"

    def _apply_youtube(self, data: CardData) -> None:
        """
        YouTube par asli Hindi dub upload dikha = real evidence.
        Weekly estimate se zyada ho (par planned ke andar) to use karo.
        """
        hindi_hits = [h for h in data.yt_hits if h.get("is_hindi")]
        if not hindi_hits:
            return
        best = max(hindi_hits, key=lambda h: h["episode"])
        ep = best["episode"]
        season_hit = best.get("season")
        if data.current_number is not None and season_hit is not None and season_hit != data.current_number:
            return
        target = next((s for s in data.seasons if s.number == data.current_number), None)
        planned = target.planned if target else data.planned_total
        if planned and ep > planned:
            return  # galat season ka number ho sakta hai — ignore
        if target is not None:
            if target.hi_count is None or ep > target.hi_count:
                target.hi_count = ep
                data.hi = ep
            # YouTube upload = real evidence, isliye channel ko platform list me jodo
            target.hi_platforms = platforms.dedupe_preserve(target.hi_platforms + [best["channel"]])
            data.hi_platforms = target.hi_platforms
            data.hindi_found = True

    def _apply_overrides(self, data: CardData) -> None:
        """/setep + overrides.json — sabse high priority."""
        overrides = self._load_overrides(data.anime_id)
        if not overrides:
            return
        current = next((s for s in data.seasons if s.number == data.current_number), None)
        for lang, count in overrides.items():
            if lang == "jp":
                data.jp = count
                if current:
                    current.jp_count = count
            elif lang == "en":
                data.en = count
                if current:
                    current.en_count = count
            elif lang == "hi":
                data.hi = count
                data.hindi_found = True
                if current:
                    current.hi_count = count
        if current:
            current.override = overrides
        data.notes.append("manual override applied")

    def _load_overrides(self, anime_id: int) -> dict:
        """DB manual_fix + overrides.json dono milake (DB jeet-ta hai)."""
        out: dict[str, int] = {}
        path = config.OVERRIDES_PATH
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                for key, val in (payload or {}).items():
                    if str(key) == str(anime_id) and isinstance(val, dict):
                        for lang, count in val.items():
                            if lang in ("jp", "en", "hi") and isinstance(count, int):
                                out[lang] = count
        except Exception as exc:
            log.warning("overrides.json read fail: %s", exc)
        if self.cache is not None:
            try:
                out.update(self.cache.get_manual_fix(anime_id))
            except Exception as exc:
                log.warning("manual_fix read fail: %s", exc)
        return out

    # -- platforms / languages ----------------------------------------------
    @staticmethod
    def _languages(data: CardData, base: anilist_mod.MediaEntry) -> tuple[list[str], list[str]]:
        """
        Audio/Subtitle lines sirf real evidence se:
          * Japanese — anime hai, hamesha
          * Hindi    — AniNidhi/YouTube par dub mila tabhi
          * English  — dubinfo/anischedule se confirm ho tabhi
          * Subtitles: koi streaming platform linked ho to English
        """
        audio = ["Japanese"]
        if data.hindi_found or data.hi_platforms:
            audio.append("Hindi")
        if data.en is not None:
            audio.append("English")
        subs: list[str] = []
        if data.streaming_platforms or base.streaming:
            subs.append("English")
        return platforms.dedupe_preserve(audio), subs

    @staticmethod
    def _watch(base: anilist_mod.MediaEntry, data: CardData) -> tuple[str | None, str | None]:
        """Watch button ka platform + URL. Hindi dub platform ko preference."""
        want = (data.hi_platforms or [""])[0]
        if want:
            for link in base.streaming:
                if platforms.canon(link.get("site")) == platforms.canon(want):
                    return platforms.display(link.get("site")), link.get("url")
            link = platforms.build_link(want, base.best_title)
            return link.display_name, link.url
        if base.streaming:
            first = base.streaming[0]
            return platforms.display(first.get("site")), first.get("url")
        return None, None

    # -- extras --------------------------------------------------------------
    def _extra_info(self, e: anilist_mod.MediaEntry, dub_groups: dict) -> ExtraInfo:
        hindi = "Unknown"
        hi_plats: list[str] = []
        # movie/special ka dub: AniNidhi me movie record ya same-franchise record
        for key, dub in (dub_groups or {}).items():
            for r in dub.records:
                if not r.usable:
                    continue
                if r.media_type == "movie" and e.format == "MOVIE":
                    hindi = "Available ✅"
                    hi_plats = platforms.dedupe_preserve(hi_plats + [r.display_platform])
        if hindi == "Unknown" and not hi_plats:
            hindi = "No official Hindi dub found"
        return ExtraInfo(
            title=e.best_title,
            year=e.year,
            format=e.format,
            minutes=e.duration,
            hindi=hindi,
            hi_platforms=hi_plats,
            entry_id=e.id,
            url=f"https://anilist.co/anime/{e.id}",
        )

    @staticmethod
    def _date_of(e: anilist_mod.MediaEntry) -> date | None:
        sd = e.start_date or {}
        try:
            if sd.get("year") and sd.get("month") and sd.get("day"):
                return date(int(sd["year"]), int(sd["month"]), int(sd["day"]))
        except (TypeError, ValueError):
            return None
        return None


def _main_titles(e: "anilist_mod.MediaEntry") -> list[str]:
    """English / Romaji / Native hi — synonyms nahi (exact-match ke liye)."""
    return [t for t in (e.english, e.romaji, e.native) if t and t.strip()]


def anilist_src_key(title: str) -> str:
    """Franchise key (aninidhi_src.franchise_key ka wrapper — naam clear rakha)."""
    return aninidhi_src.franchise_key(title)


# module-level singleton (main.py isi ko use karta hai)
aggregator = Aggregator()
