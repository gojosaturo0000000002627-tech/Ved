"""
tests/test_offline.py — offline test suite.

  * AniList / dubinfo / YouTube / AnimeSchedule  -> MOCKED (fixtures = real captured data)
  * AniNidhi                                     -> REAL package (bundled snapshot, no network)

Chalane ka tareeka:
    cd anime-dub-bot && python -m pytest tests/ -v
ya:
    python tests/test_offline.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# --- environment: sab kuch offline + clock freeze (config import se PEHLE) ---
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["ANINIDHI_SOURCE_URL"] = ""                       # bundled snapshot, no network
os.environ.setdefault("ANINIDHI_CACHE_DIR", "/tmp/aninidhi-test-cache")
os.environ["BOT_FAKE_NOW"] = "2026-09-22T12:00:00+00:00"     # 22 Sep 2026, 05:30 PM IST
os.environ["DB_PATH"] = ":memory:"

import config                                                # noqa: E402
import formatter                                             # noqa: E402
import texts                                                 # noqa: E402
from database import Database                                 # noqa: E402
from notifier import Notifier                                 # noqa: E402
from sources.aggregator import Aggregator, CardData           # noqa: E402
from sources.anilist import MediaEntry, _parse_media          # noqa: E402
from sources.aninidhi_src import (                            # noqa: E402
    AniNidhiSource,
    franchise_key,
    parse_season,
    weekly_progress,
)
from sources.youtube import YouTubeSource                     # noqa: E402

FIXTURES = json.loads((Path(__file__).parent / "anilist_fixtures.json").read_text(encoding="utf-8"))
TODAY = date(2026, 9, 22)


def run(coro):
    """pytest-asyncio plugin ke bina async test chalane ka helper."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# MOCK AniList — behaviour real API jaisa (title match + popularity order)
# ---------------------------------------------------------------------------
class FakeAniList:
    def __init__(self, rows=None):
        self.rows = {r["id"]: r for r in (rows or FIXTURES)}
        self.entries = {i: _parse_media(r) for i, r in self.rows.items()}
        self.search_queries: list[str] = []
        self.requests_made = 0
        self.fail_ids: set[int] = set()

    # -- AniListClient jaisa interface --------------------------------------
    def cached_entry(self, anime_id: int) -> MediaEntry | None:
        return self.entries.get(anime_id)

    async def search(self, query: str, per_page: int = 12) -> list[MediaEntry]:
        self.search_queries.append(query)
        self.requests_made += 1
        q = query.strip().lower()
        words = [w for w in q.replace(":", " ").split() if w]
        hits = []
        for e in self.entries.values():
            titles = [t.lower() for t in e.titles()]
            if any(q in t for t in titles) or all(
                any(w in t for t in titles) for w in words
            ):
                hits.append(e)
        hits.sort(key=lambda e: -(self.rows[e.id].get("popularity") or 0))
        return hits[:per_page]

    async def many(self, ids, force: bool = False) -> list[MediaEntry]:
        self.requests_made += 1
        out = []
        for i in ids:
            if i in self.fail_ids:
                continue
            e = self.entries.get(i)
            if e:
                out.append(e)
        return out

    async def get(self, anime_id: int, force: bool = False) -> MediaEntry | None:
        self.requests_made += 1
        return None if anime_id in self.fail_ids else self.entries.get(anime_id)

    def clear_cache(self) -> None:
        pass


class FakeYouTube:
    """YouTube mock — sirf wahi hits deta hai jo test me chahiye."""

    def __init__(self, hits=None):
        self.hits = hits or []
        self.calls = 0

    async def scan(self, titles, season=None, hindi_only=True):
        self.calls += 1
        return list(self.hits)

    async def aclose(self):
        pass


class FakeDubInfo:
    enabled = False

    async def lookup(self, titles):
        return []


class FakeScheduleSource:
    """
    AnimeSchedule.net stub — REAL captured fixtures se EN dub data deta hai
    (network nahi lagta). empty=True par bilkul khaali (fallback-path tests).
    """

    def __init__(self, empty: bool = False):
        import json

        from sources.anischedule import AnimeScheduleSource

        self.empty = empty
        self._by_id: dict[int, object] = {}
        self._q_rows: dict[str, list[dict]] = {}
        if not empty:
            rows = json.loads((Path(__file__).parent / "anischedule_fixtures.json").read_text())

            def _register(aid: int, r: dict) -> None:
                if aid not in self._by_id:
                    self._by_id[aid] = AnimeScheduleSource._parse(aid, r)

            for name, row in rows.items():
                if isinstance(row, list):
                    # q-search fixture set (list of rows) — in entries ka ID-lookup bhi hota hai
                    if name.startswith("q:"):
                        self._q_rows[name[2:]] = row
                        for r in row:
                            web = (r.get("websites") or {}).get("aniList") or ""
                            m = re.search(r"/anime/(\d+)", web)
                            if m:
                                _register(int(m.group(1)), r)
                    continue
                if not isinstance(row, dict) or "_missing" in row or "_http" in row:
                    continue
                if name.startswith("id:"):
                    # galat/stale ID map (asli API behaviour emulate)
                    _register(int(name[3:]), row)
                    continue
                web = (row.get("websites") or {}).get("aniList") or ""
                try:
                    aid = int(web.split("/anime/")[1].split("/")[0].split("-")[0])
                except (IndexError, ValueError):
                    continue
                _register(aid, row)
        self.requests_made = 0

    @property
    def enabled(self) -> bool:
        return True

    def cached_count(self) -> int:
        return len(self._by_id)

    async def lookup_many(self, anilist_ids):
        self.requests_made += 1
        return {i: self._by_id.get(i) for i in anilist_ids}

    async def search_dub(self, titles, season_no=None):
        """Real source ka same matching logic, fixtures par (offline)."""
        import re as _re

        from sources.anischedule import AnimeScheduleSource, pick_candidate

        for t in [t for t in titles if t][:2]:
            key = t.strip().lower()
            rows = None
            for qkey, rws in self._q_rows.items():
                if _re.search(r"\b" + _re.escape(qkey.split()[0]) + r"\b", key):
                    rows = rws
                    break
            if rows is None:
                continue
            row = pick_candidate(rows, titles, season_no)
            if row is not None:
                info = AnimeScheduleSource._parse(0, row)
                if info.has_dub_data:
                    return info
        return None

    async def aclose(self):
        pass


def make_aggregator(yt=None, cache=None, aninidhi=None, sched=None) -> Aggregator:
    return Aggregator(
        anilist=FakeAniList(),
        aninidhi=aninidhi or AniNidhiSource(),
        yt=yt or FakeYouTube(),
        dinfo=FakeDubInfo(),
        sched=sched if sched is not None else FakeScheduleSource(),
        cache=cache,
    )


# ===========================================================================
# 1. REAL AniNidhi: Black Torch -> 4 episodes + next date
# ===========================================================================
def test_01_real_aninidhi_black_torch():
    agg = make_aggregator()
    data = run(agg.build_card_by_query("black torch", force=True))[1]
    assert data is not None, "Black Torch ka card banna chahiye"
    assert data.hindi_found is True
    assert data.hi == 4, f"22 Sep 2026 tak 4 Hindi episodes hone chahiye, mile {data.hi}"
    assert data.hi_platforms == ["Crunchyroll"], data.hi_platforms
    assert data.next_hi_date == date(2026, 9, 26), data.next_hi_date
    assert "Hindi dub: 4 episodes" in formatter.format_card(data)
    assert "26 Sep 2026 (estimated)" in formatter.format_card(data)
    # Japanese audio complete hai (AniList: 12/12 FINISHED)
    assert data.jp == 12 and data.planned_total == 12


def test_01b_real_aninidhi_dataset_is_loaded():
    """AniNidhi REAL package use ho raha hai — 400+ records."""
    src = AniNidhiSource()
    rows = run(src.all_records())
    assert len(rows) >= 400, f"AniNidhi me 488 records hone chahiye, mile {len(rows)}"
    bt = [r for r in rows if (r.get("title") or "") == "Black Torch"]
    assert bt, "Black Torch record milna chahiye"
    assert bt[0]["hindi_dubs"][0]["platform"] == "Crunchyroll"


# ===========================================================================
# 2. Season matching: "Jujutsu Kaisen 2nd Season" record
# ===========================================================================
def test_02_season_matching_jujutsu_2nd_season():
    # AniList ka title "Jujutsu Kaisen 2nd Season" hai, AniNidhi ka "Jujutsu Kaisen (Season 2)"
    base, season, cour, kind = parse_season("Jujutsu Kaisen 2nd Season")
    assert season == 2
    assert franchise_key(base) == "jujutsu kaisen"

    src = AniNidhiSource()
    recs = run(src.lookup(["Jujutsu Kaisen 2nd Season"]))
    by_season = {}
    for r in recs:
        by_season.setdefault(r.season, []).append(r)
    assert 2 in by_season, f"Season 2 ka record milna chahiye, mile {sorted(by_season)}"
    s2 = by_season[2][0]
    assert s2.display_platform == "Crunchyroll"
    assert s2.release_date == date(2023, 9, 22)

    # cour parsing bhi
    assert parse_season("Mushoku Tensei (Season 2 Cour 1)") == ("Mushoku Tensei", 2, 1, None)
    assert parse_season("Demon Slayer: Kimetsu no Yaiba – Mugen Train Arc (Season 2 – Cour 1)")[1:] == (2, 1, None)


# ===========================================================================
# 3. Not-found: Bleach -> found=False (ye SAHI jawab hai)
# ===========================================================================
def test_03_not_found_bleach():
    src = AniNidhiSource()
    recs = run(src.lookup(["Bleach", "BLEACH", "ブリーチ"]))
    assert recs == [], "Bleach ka koi official streaming Hindi dub nahi hai"

    agg = make_aggregator()
    data = run(agg.build_card_by_query("bleach", force=True))[1]
    assert data is not None
    assert data.hindi_found is False
    card = formatter.format_card(data)
    assert "No official Hindi dub found" in card
    assert "Hindi dub: 4" not in card  # kabhi invent nahi karega


# ===========================================================================
# 4. Card formats: multi-season / movie / single-season
# ===========================================================================
def test_04_card_format_multi_season():
    agg = make_aggregator()
    data = run(agg.build_card_by_query("mushoku tensei", force=True))[1]
    card = formatter.format_card(data)
    assert card.startswith("🎬 Mushoku Tensei: Jobless Reincarnation")
    assert "📌 Status: Ongoing" in card
    assert "📺 Available platforms:" in card
    assert "Audio: Japanese, Hindi" in card
    assert "Subtitles: English" in card
    assert "Season 3: 14 episodes planned" in card
    assert "🎞 Season details:" in card
    # v1.2+: har season ka apna block — 📀 header + blocks ke beech blank line
    assert "📀 Season 1 (2021)" in card
    assert "\n\n📀 Season 2 (2023)" in card, "seasons ke beech gap hona chahiye"
    assert "\n\n📀 Season 3 (ongoing)" in card, "airing season ka tag (ongoing) hona chahiye"
    assert "• Season 1" not in card, "purana '•' season header ab nahi hona chahiye"
    assert "📅 Next episode:" in card
    assert "• Japanese audio: 27 Sep 2026, 04:30 PM IST" in card
    # v1.4: EN dub ab AnimeSchedule (real) se — S3 premier 19 Jul -> weekly -> 10/14
    assert "• English dub: 27 Sep 2026 (estimated)" in card
    assert "English dub: 10 episodes" in card, "S3 ka EN dub count weekly math se"
    assert "English dub: 12 episodes" in card, "S2 EN dub complete (Finished)"
    assert "⏱ Last checked:\n22 Sep 2026, 05:30 PM IST" in card
    assert card.rstrip().endswith(f"🤖 {config.VERSION}")
    assert config.VERSION.startswith("v")


def test_04b_card_format_movie_suzume():
    agg = make_aggregator()
    data = run(agg.build_card_by_query("suzume", force=True))[1]
    assert data.kind == "movie"
    card = formatter.format_card(data)
    assert "🎬 Movie — 121 min, released 11 Nov 2022" in card
    assert "🎞 Movie details:" in card
    assert "Hindi dub: Available ✅ (Crunchyroll)" in card
    assert "📅 Next episode:" not in card, "movie card me Next episode section nahi hota"
    assert "🎞 Season details:" not in card


def test_04c_card_format_single_season():
    agg = make_aggregator()
    data = run(agg.build_card_by_query("black torch", force=True))[1]
    card = formatter.format_card(data)
    assert "🎞 Season details:" not in card, "single-season me Season details header nahi"
    assert "Released: 12/12 episodes" in card
    assert "Hindi dub: 4 episodes" in card
    assert "Japanese audio: 12 episodes" in card
    # v1.4: EN dub AnimeSchedule se — premier 4 Jul 2026, 12 eps, Finished -> complete
    assert "English dub: 12 episodes" in card
    assert "• English dub: All episodes released" in card


# ===========================================================================
# 5. Cours merge: Mushoku = 3 seasons + movies section
# ===========================================================================
def test_05_cours_merge_mushoku():
    agg = make_aggregator()
    data = run(agg.build_card_by_query("mushoku tensei", force=True))[1]
    assert len(data.seasons) == 3, f"5 AniList entries -> 3 seasons, mile {len(data.seasons)}"
    got = [(s.planned, s.released) for s in data.seasons]
    assert got == [(23, 23), (25, 25), (14, 13)], got
    assert [s.number for s in data.seasons] == [1, 2, 3]
    assert data.extras, "special/movie section ke liye entries honi chahiye"
    assert "🎥 Movies / Specials:" in formatter.format_card(data)
    # S1 ke do cours merge hue
    assert len(data.seasons[0].entry_ids) == 2
    assert len(data.seasons[1].entry_ids) == 2


# ===========================================================================
# 6. Search: franchise pick + best match + exact match + garbage guard
# ===========================================================================
def test_06a_franchise_pick_grand_blue():
    agg = make_aggregator()
    outcome = run(agg.resolve_search("grand blue dreaming"))
    assert outcome.chosen is not None, "direct card milna chahiye, pick list nahi"
    assert outcome.candidates == []
    assert outcome.matched_by in ("exact", "franchise", "single", "wordset", "best")
    data = run(agg.build_card(outcome.chosen.id, force=True))
    # Grand Blue Dreaming / Season 2 / Season 3 ek hi card me
    assert len(data.seasons) >= 3
    assert any("Grand Blue Season 3" in t or "Grand Blue Dreaming Season 3" in t
               for s in data.seasons for t in s.titles)


def test_06b_exact_match_one_piece_not_movie():
    agg = make_aggregator()
    outcome = run(agg.resolve_search("one piece"))
    assert outcome.chosen is not None, "pick list nahi, direct card"
    assert outcome.chosen.id == 21, f"main ONE PIECE series chahiye, mila {outcome.chosen.id}"
    assert "FILM" not in (outcome.chosen.romaji or "").upper()


def test_06c_best_match_dark_gathering():
    agg = make_aggregator()
    outcome = run(agg.resolve_search("dark gathering"))
    assert outcome.chosen is not None
    assert outcome.chosen.id == 152802, "Dark Gathering (2023) chahiye"


def test_06d_ambiguous_shows_pick_list():
    agg = make_aggregator()
    outcome = run(agg.resolve_search("one"))
    assert outcome.chosen is None, "'one' ambiguous hai -> pick list"
    assert outcome.needs_pick is True
    assert len(outcome.candidates) >= 2
    text = formatter.format_pick_list(outcome.candidates)
    assert texts.PICK_LIST_HEADER in text


def test_06e_garbage_guard_no_single_word_fallback():
    """
    2-word query fail ho to 1-word search KABHI nahi hona chahiye.
    ("black torch" fail hone par "Black Jack / Black Cat / Black God" ki
    garbage list dena strictly forbidden hai.)
    """
    # (a) 2-word query jo exist nahi karta -> clean not-found, koi 1-word retry nahi
    agg = make_aggregator()
    outcome = run(agg.resolve_search("zzz qqq"))
    assert outcome.not_found is True
    assert outcome.candidates == []
    for q in agg.anilist.search_queries:
        assert len(q.split()) >= 2, f"1-word fallback forbidden, par query chala: {q!r}"

    # (b) 3-word query -> sirf pehle 2 words ka subset chalega, 1 word kabhi nahi
    agg2 = make_aggregator()
    outcome2 = run(agg2.resolve_search("zzz qqq www"))
    assert outcome2.not_found is True
    for q in agg2.anilist.search_queries:
        assert len(q.split()) >= 2, f"1-word fallback forbidden: {q!r}"

    # (c) 3-word query jiska 2-word subset valid hai -> wahi card milega
    agg3 = make_aggregator()
    outcome3 = run(agg3.resolve_search("black torch xyzzy"))
    assert outcome3.chosen is not None and outcome3.matched_by.startswith("subset:")
    assert all(len(q.split()) >= 2 for q in agg3.anilist.search_queries)


def test_06f_no_synonym_exact_trap():
    """Onigiri ka synonym 'Demon Slayer' hai — exact match usse Onigiri na dikhaye."""
    agg = make_aggregator()
    outcome = run(agg.resolve_search("demon slayer"))
    assert outcome.chosen is not None
    assert outcome.chosen.id == 101922, f"Onigiri(21612) nahi, main series chahiye — mila {outcome.chosen.id}"

    data = run(agg.build_card(outcome.chosen.id, force=True))
    # har arc apna alag season (ek hi me merge nahi)
    assert len(data.seasons) == 5, [(s.number, s.titles[0]) for s in data.seasons]
    assert [(s.planned, s.released) for s in data.seasons] == [
        (26, 26), (7, 7), (11, 11), (11, 11), (8, 8)
    ]
    # Onigiri card me season ban ke nahi dikhna chahiye
    assert not any("Onigiri" in t for s in data.seasons for t in s.titles)


def test_06g_one_piece_no_relation_noise():
    """ONE PIECE ka AniList 'PREQUEL' = MONSTERS ONA — usse Season 1 me merge nahi hona chahiye."""
    agg = make_aggregator()
    data = run(agg.build_card(21, force=True))
    assert len(data.seasons) == 1
    assert data.seasons[0].entry_ids == [21], data.seasons[0].entry_ids
    assert data.seasons[0].planned is None and data.seasons[0].released == 1179
    # AniNidhi: Egghead arcs (Finished) -> count unknown, par dub available tha
    card = formatter.format_card(data)
    assert "Available ✅ (complete)" in card
    assert "• Hindi dub: All episodes released" in card


# ===========================================================================
# 7. Current season: S1 base par bhi S3 ka next episode / status
# ===========================================================================
def test_07_current_season_rule_from_s1_base():
    agg = make_aggregator()
    data = run(agg.build_card(108465, query="mushoku tensei", force=True))  # S1 entry
    assert data.current_number == 3, data.current_number
    assert data.current_label == "Season 3"
    assert data.status_text == "Ongoing", "S3 airing hai to status Ongoing"
    assert data.planned_total == 14
    assert data.jp == 13 and data.hi == 4
    assert data.next_jp_text == "27 Sep 2026, 04:30 PM IST"
    assert data.next_hi_text == "27 Sep 2026 (estimated)"
    card = formatter.format_card(data)
    assert "Season 3: 14 episodes planned" in card
    # notification engine inhi counts ko track karega
    assert data.hi_platforms == ["Crunchyroll"]


def test_07b_current_season_prefers_dub_airing_over_announced():
    """Grand Blue: JP S3 complete, S4 announced, par S3 ka Hindi dub abhi airing hai."""
    agg = make_aggregator()
    data = run(agg.build_card_by_query("grand blue dreaming", force=True))[1]
    assert data.current_number == 3, data.current_number
    assert data.hi == 4
    assert data.next_hi_date == date(2026, 9, 28), data.next_hi_date


# ===========================================================================
# 8. Notification format — exact match
# ===========================================================================
def test_08_notification_format_exact():
    db = Database(":memory:")
    agg = make_aggregator(cache=db)
    data = run(agg.build_card_by_query("black torch", force=True))[1]
    db.add_follow(
        user_id=4242,
        anime_id=data.anime_id,
        title=data.title,
        langs=["hi"],
        jp_count=data.jp,
        en_count=None,
        hi_count=4,                    # abhi 4, agla pass 5 dega
        total_eps=data.planned_total,
        watch_url="https://www.crunchyroll.com/series/black-torch",
        platform="Crunchyroll",
    )

    class FakeBot:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
            self.sent.append({"chat_id": chat_id, "text": text, "parse_mode": parse_mode, "kb": reply_markup})

    bot = FakeBot()
    notifier = Notifier(aggregator=agg, db=db, bot=bot)

    # ek hafta aage badhao -> 5 episode
    os.environ["BOT_FAKE_NOW"] = "2026-09-29T12:00:00+00:00"
    try:
        sent = run(notifier.run_once())
    finally:
        os.environ["BOT_FAKE_NOW"] = "2026-09-22T12:00:00+00:00"

    assert len(sent) == 1, sent
    assert sent[0]["user_id"] == 4242
    assert sent[0]["episode"] == 5
    assert bot.sent[0]["parse_mode"] == "HTML"
    expected = (
        "🔔 <b>BLACK TORCH</b> — Naya Episode!\n"
        "\n"
        "📌 Episode <b>5</b> (Hindi dub) aa chuka hai 🎉\n"
        "📺 Platform: Crunchyroll\n"
        "📈 Hindi dub: 5/12 episodes\n"
        "⏱ 29 Sep 2026, 05:30 PM IST"
    )
    assert bot.sent[0]["text"] == expected, repr(bot.sent[0]["text"])
    # ▶️ Watch button lag gaya
    assert bot.sent[0]["kb"] is not None
    assert "▶️ Watch" in bot.sent[0]["kb"].inline_keyboard[0][0].text
    # DB update ho gaya -> agla pass dubara notify nahi karega
    assert db.get_follow(4242, data.anime_id)["hi_count"] == 5


def test_08b_no_notification_without_increase():
    db = Database(":memory:")
    agg = make_aggregator(cache=db)
    data = run(agg.build_card_by_query("black torch", force=True))[1]
    db.add_follow(4242, data.anime_id, data.title, ["hi"], jp_count=data.jp, hi_count=4, total_eps=12)
    notifier = Notifier(aggregator=agg, db=db, bot=None)
    sent = run(notifier.run_once())
    assert sent == [], "count na badhe to notification nahi"


def test_08c_new_dub_available_notifies():
    """KONOSUBA case: follow ke waqt dub nahi tha (hi_count=NULL). Naya dub
    aane par None->N transition PAR 'Dub Shuru' notification JAANI CHAHIYE —
    yehi chhup-jana wo bug tha jisse user ko update miss hui."""
    db = Database(":memory:")
    agg = make_aggregator(cache=db)
    data = run(agg.build_card_by_query("black torch", force=True))[1]
    db.add_follow(4242, data.anime_id, data.title, ["hi"])          # sab counts NULL
    # purana follower (migration jaisa): baseline kabhi set hua, dub data kabhi nahi mila
    with db._lock, db._conn:
        db._conn.execute(
            "UPDATE follows SET seen=1, hi_count=NULL WHERE user_id=? AND anime_id=?",
            (4242, data.anime_id),
        )

    class FakeBot:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
            self.sent.append({"chat_id": chat_id, "text": text})

    bot = FakeBot()
    notifier = Notifier(aggregator=agg, db=db, bot=bot)
    sent = run(notifier.run_once())
    assert len(sent) == 1, sent
    assert sent[0]["lang"] == "hi"
    assert "— Hindi dub Shuru! 🎉" in bot.sent[0]["text"], bot.sent[0]["text"]
    assert "ke naye episodes aa gaye hain — Episode <b>4</b> tak available" in bot.sent[0]["text"]
    assert "📈 Hindi dub: 4/12 episodes" in bot.sent[0]["text"]
    # dobara pass -> dubara notification nahi (ab count stored hai)
    sent2 = run(notifier.run_once())
    assert sent2 == [], f"repeat notification: {sent2}"
    row = db.get_follow(4242, data.anime_id)
    assert row["hi_count"] == 4 and row["seen"]


def test_08d_first_poll_is_silent_baseline():
    """Bilkula naya follow (seen=0): pehla pass sirf baseline set kare — koi notification nahi."""
    db = Database(":memory:")
    agg = make_aggregator(cache=db)
    data = run(agg.build_card_by_query("black torch", force=True))[1]
    db.add_follow(4242, data.anime_id, data.title, ["hi", "jp", "en"])   # sab NULL, seen=0
    notifier = Notifier(aggregator=agg, db=db, bot=None)
    sent = run(notifier.run_once())
    assert sent == [], f"pehle pass par notification: {sent}"
    row = db.get_follow(4242, data.anime_id)
    assert row["seen"] and row["hi_count"] == 4 and row["jp_count"] == data.jp


def test_08e_new_season_start_notifies():
    """Naya season: counts 1 se restart hote hain (1 < 12) — fir bhi 'Season Shuru'
    notification aani chahiye, aur sirf EK baar."""
    db = Database(":memory:")
    agg = make_aggregator(cache=db)
    data = run(agg.build_card_by_query("black torch", force=True))[1]
    cur = data.current_number
    db.add_follow(4242, data.anime_id, data.title, ["jp"], jp_count=12, total_eps=12)
    with db._lock, db._conn:
        # stored season purana hai (jaise S2 ke counts the) — ab current badal gaya
        db._conn.execute(
            "UPDATE follows SET seen=1, season_no=? WHERE user_id=? AND anime_id=?",
            (cur + 1, 4242, data.anime_id),
        )
    class FakeBot:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
            self.sent.append({"chat_id": chat_id, "text": text})

    bot = FakeBot()
    notifier = Notifier(aggregator=agg, db=db, bot=bot)
    sent = run(notifier.run_once())
    assert len(sent) == 1, sent
    assert sent[0]["lang"] == "jp"
    text = bot.sent[0]["text"]
    assert f"Season {cur} Shuru" in text, text
    assert "aa chuka hai" in text
    # agla pass -> season stored, count same -> kuch nahi
    sent2 = run(notifier.run_once())
    assert sent2 == [], f"repeat: {sent2}"
    assert db.get_follow(4242, data.anime_id)["season_no"] == cur


# ===========================================================================
# 9. /setep override — sabse high priority
# ===========================================================================
def test_09_setep_override():
    db = Database(":memory:")
    agg = make_aggregator(cache=db)
    before = run(agg.build_card_by_query("black torch", force=True))[1]
    assert before.hi == 4

    db.set_manual_fix(before.anime_id, "hi", 9, note="test", set_by=1)
    after = run(agg.build_card(before.anime_id, force=True))
    assert after.hi == 9, "manual override jeetna chahiye"
    assert "Hindi dub: 9 episodes" in formatter.format_card(after)
    assert db.get_manual_fix(before.anime_id) == {"hi": 9}

    # override hatao -> real data wapas
    db.delete_manual_fix(before.anime_id, "hi")
    again = run(agg.build_card(before.anime_id, force=True))
    assert again.hi == 4


def test_09b_overrides_json_highest_priority(tmp_path=None):
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "overrides.json"
        agg = make_aggregator()
        data = run(agg.build_card_by_query("black torch", force=True))[1]
        p.write_text(json.dumps({str(data.anime_id): {"hi": 7, "jp": 12}}), encoding="utf-8")
        old = config.OVERRIDES_PATH
        config.OVERRIDES_PATH = str(p)
        try:
            d2 = run(agg.build_card(data.anime_id, force=True))
        finally:
            config.OVERRIDES_PATH = old
        assert d2.hi == 7


# ===========================================================================
# 10. Fuzzy search: "mushoko tensai" -> Mushoku Tensei
# ===========================================================================
def test_10_fuzzy_typo_tolerance():
    agg = make_aggregator()
    outcome = run(agg.resolve_search("mushoko tensai"))
    assert outcome.chosen is not None, "typo ke baad bhi result milna chahiye"
    assert "mushoku" in (outcome.chosen.romaji or "").lower()
    assert outcome.matched_by.startswith("typo:")
    assert outcome.suggestions, "suggestions bhi milni chahiye"

    # galat-cheez guard: har word ~80% similar na ho to suggestion nahi
    src = AniNidhiSource()
    assert run(src.fuzzy_titles("mushoko tensai")), "mushoko tensai -> suggestions"
    assert run(src.fuzzy_titles("zzzzzzz qqqqqqq")) == []


# ===========================================================================
# Extra: weekly math, YouTube parsing, negative cache, DB flow, formatter
# ===========================================================================
def test_11_weekly_math_and_past_guard():
    # Black Torch: Crunchyroll 29 Aug 2026
    prog = weekly_progress(date(2026, 8, 29), "Airing", 12, TODAY)
    assert prog.episodes == 4
    assert prog.next_date == date(2026, 9, 26)
    assert prog.estimated is True

    # start se pehle
    prog0 = weekly_progress(date(2026, 10, 1), "Airing", 12, TODAY)
    assert prog0.episodes == 0 and prog0.next_date == date(2026, 10, 1)

    # complete — status Finished ka matlab dub ABHI poora hai; future-month
    # projection galat thi (Konosuba bug), isliye complete_month kabhi nahi
    done = weekly_progress(date(2026, 4, 2), "Finished", 12, TODAY)
    assert done.episodes == 12 and done.complete and done.next_date is None
    assert done.complete_month is None

    # planned cap
    capped = weekly_progress(date(2020, 1, 1), "Airing", 12, TODAY)
    assert capped.episodes == 12

    # weekly estimate kabhi past date nahi deta (formula hi future deta hai)
    assert weekly_progress(date(2026, 8, 29), "Airing", None, date(2026, 9, 26)).next_date == date(2026, 10, 3)

    # TBA / unknown
    assert weekly_progress(None, "Airing", 12, TODAY).episodes is None
    assert weekly_progress(date(2026, 12, 1), "TBA", 12, TODAY).episodes is None


def test_11b_past_date_guard_on_card():
    """Agar estimated date nikal chuki ho to card par 'To be announced' dikhega."""
    from sources.aggregator import SeasonInfo

    agg = make_aggregator()
    stale = SeasonInfo(number=1, label="Season 1", planned=12, released=12,
                       hi_count=6, hi_platforms=["Crunchyroll"], hi_status="Airing",
                       hi_next=date(2026, 9, 1))          # ye date nikal chuki (today 22 Sep)
    assert agg._hi_next_text(stale, TODAY) == "To be announced"

    future = SeasonInfo(number=1, label="Season 1", planned=12, released=12,
                        hi_count=4, hi_platforms=["Crunchyroll"], hi_status="Airing",
                        hi_next=date(2026, 9, 26))
    assert agg._hi_next_text(future, TODAY) == "26 Sep 2026 (estimated)"

    nodub = SeasonInfo(number=1, label="Season 1", planned=12, released=12, jp_count=12)
    assert agg._hi_next_text(nodub, TODAY) == "No official Hindi dub found"

    done = SeasonInfo(number=1, label="Season 1", planned=12, released=12,
                      hi_count=12, hi_platforms=["Crunchyroll"], hi_status="Finished")
    assert agg._hi_next_text(done, TODAY) == "All episodes released"


def test_11c_finished_hindi_line_no_future_month():
    """Konosuba bug: AniNidhi Finished -> '(complete ✅)', future month kabhi nahi."""
    from formatter import _hindi_line

    # S1 jaisa case: 10/10, status Finished -> pehle '10 episodes (Oct 2026 me
    # complete)' dikhta tha jo galat tha
    assert _hindi_line(10, None, ["Crunchyroll"], "Finished") == "10 episodes (complete ✅)"
    # chal raha dub — plain count
    assert _hindi_line(4, None, ["Crunchyroll"], "Airing") == "4 episodes"
    # count ka record nahi par dub complete
    assert _hindi_line(None, None, ["Crunchyroll"], "Finished") == "Available ✅ (complete)"
    # dub hai par kuch nahi pata -> Unknown (guess nahi)
    assert _hindi_line(None, None, ["Crunchyroll"], "Airing") == "Unknown"


def test_12_youtube_episode_extraction():
    from sources.youtube import YTVideo

    v1 = YTVideo("a", "BLACK TORCH - Episode 08 [EN Sub] | Muse IN", "", "")
    assert v1.episode() == (8, None) and v1.is_hindi is False
    v2 = YTVideo("b", "Black Torch Episode 4 Hindi Dub", "", "")
    assert v2.episode() == (4, None) and v2.is_hindi is True
    v3 = YTVideo("c", "[Hindi Dub] Campfire Cooking - Episode 22 (S2E10)", "", "")
    assert v3.episode() == (10, 2) and v3.is_hindi is True
    v4 = YTVideo("d", "JoJo's Bizarre Adventure (S3): Diamond is Unbreakable - Episode 20 [Hindi Dub]", "", "")
    assert v4.episode() == (20, 3) and v4.is_hindi is True


def test_12b_youtube_negative_cache(tmp_path=None):
    """Handle resolve fail ho to 1 ghanta skip (warna har card 45s waste)."""
    import tempfile

    class Store:
        def __init__(self):
            self.data = {}

        def get(self, key):
            return self.data.get(key)

        def set(self, key, value, ttl):
            self.data[key] = value

        def expired_negative(self, key):
            return key in self.data and self.data[key] is None

    store = Store()
    yt = YouTubeSource(store=store)
    calls = {"n": 0}

    async def fail_resolve(handle):
        calls["n"] += 1
        return None

    yt._resolve_handle = fail_resolve
    got = run(yt.channel_id_for({"name": "Muse India", "handle": "MuseIndia"}))
    assert got is None and calls["n"] == 1
    got2 = run(yt.channel_id_for({"name": "Muse India", "handle": "MuseIndia"}))
    assert got2 is None and calls["n"] == 1, "negative cache ke baad dobara resolve nahi hona chahiye"

    # success permanent cache
    async def ok_resolve(handle):
        return "UCYYhAzgWuxPauRXdPpLAX3Q"

    yt2 = YouTubeSource(store=store)
    yt2._resolve_handle = ok_resolve
    assert run(yt2.channel_id_for({"name": "Muse India", "handle": "MuseIndiaOK"})) == "UCYYhAzgWuxPauRXdPpLAX3Q"
    assert store.data["yt:handle:museindiaok"] == "UCYYhAzgWuxPauRXdPpLAX3Q"


def test_12c_youtube_can_raise_hindi_count():
    """YouTube par asli episode dikha to count badhna chahiye (planned ke andar)."""
    hits = [{"channel": "Muse India", "title": "Black Torch Episode 6 Hindi Dub",
             "episode": 6, "season": None, "is_hindi": True, "url": "u", "published": "", "channel_id": "UC"}]
    agg = make_aggregator(yt=FakeYouTube(hits))
    data = run(agg.build_card_by_query("black torch", force=True))[1]
    assert data.hi == 6, data.hi
    # AniNidhi (Crunchyroll) + YouTube evidence (Muse India) dono platform list me
    assert data.hi_platforms == ["Crunchyroll", "Muse India"], data.hi_platforms


def test_12d_no_schedule_data_falls_back_to_unknown():
    """AnimeSchedule me entry na ho (Bleach jaisa) -> EN Unknown/'To be announced' (guess nahi)."""
    agg = make_aggregator(sched=FakeScheduleSource(empty=True))
    data = run(agg.build_card_by_query("black torch", force=True))[1]
    card = formatter.format_card(data)
    assert "English dub: Unknown" in card
    assert "• English dub: To be announced" in card


def test_12e_english_dub_name_search_fallback():
    """
    EN fallback: Spy x Family S1 ka AniList-ID AnimeSchedule me NAHI hai (404),
    par name-search me dub data hai (premier 2022-04-16, 12 eps) -> milna chahiye.
    """
    import json

    from sources.anischedule import AnimeScheduleSource, en_dub_progress, pick_candidate

    fx = json.loads((Path(__file__).parent / "anischedule_fixtures.json").read_text())
    rows = fx["q:Spy x Family"]
    # S1: titles "SPY x Family" style; candidate base entry chunna chahiye
    row = pick_candidate(rows, ["SPY x Family", "Spy x Family"], 1)
    assert row is not None and row["route"] == "spy-x-family", row and row.get("route")
    info = AnimeScheduleSource._parse(40960, row)
    prog = en_dub_progress(info, TODAY)
    assert prog.count == 12 and prog.complete

    # S3 bhi: "SPY x Family Season 3" -> 13 eps
    row3 = pick_candidate(rows, ["SPY x Family Season 3"], 3)
    assert row3 is not None and row3["route"] == "spy-x-family-season-3"
    prog3 = en_dub_progress(AnimeScheduleSource._parse(190529, row3), TODAY)
    assert prog3.count == 13

    # season mismatch guard: S1 titles ke saath S3 entry nahi milni chahiye
    assert pick_candidate(rows, ["SPY x Family"], 3) is None or \
           pick_candidate(rows, ["SPY x Family"], 3)["route"] != "spy-x-family"


def test_12f_english_dub_wired_into_card():
    """Poora card: Spy x Family S1 (cours split 12+13) ka EN combine = 25, S3 = 13."""
    agg = make_aggregator()
    data = run(agg.build_card_by_query("spy x family", force=True))[1]
    card = formatter.format_card(data)
    assert "English dub: 25 episodes" in card, "S1 = Part1(12) + Part2(13) combine"
    assert "English dub: 13 episodes" in card
    assert data.en == 13, "current season (S3) ka EN count top-level par"
    assert "• English dub: All episodes released" in card


def test_12g_schedule_lacks_dub_stays_unknown():
    """Source me hi dub data nahi (JJK S1 / OPM S1 sentinel) -> Unknown (guess nahi)."""
    import json

    from sources.anischedule import AnimeScheduleSource, en_dub_progress, pick_candidate

    fx = json.loads((Path(__file__).parent / "anischedule_fixtures.json").read_text())
    # Kimetsu S1: entry hai, dubPremier sentinel
    rows = fx["q:Kimetsu no Yaiba"]
    row = pick_candidate(rows, ["Demon Slayer: Kimetsu no Yaiba"], 1)
    assert row is not None and row["route"] == "kimetsu-no-yaiba"
    info = AnimeScheduleSource._parse(101922, row)
    assert info.has_dub_data is False
    assert en_dub_progress(info, TODAY).count is None
    # dandadan S1: bhi sentinel
    dd = pick_candidate(fx["q:Dan Da Dan"], ["Dan Da Dan", "Dandadan"], 1)
    assert dd is not None and dd["route"] == "dandadan"
    assert en_dub_progress(AnimeScheduleSource._parse(171031, dd), TODAY).count is None


def test_13_database_follow_flow():
    db = Database(":memory:")
    db.touch_user(1, "ram")
    db.add_follow(1, 111, "Black Torch", ["hi", "jp"], jp_count=12, hi_count=4, total_eps=12)
    assert db.follow_count() == 1
    f = db.get_follow(1, 111)
    assert f["langs"] == ["hi", "jp"] and f["hi_count"] == 4
    db.set_lang_state(1, 111, ["en"])
    assert db.get_lang_state(1, 111) == ["en"]
    db.update_counts(1, 111, jp_count=12, hi_count=5)
    assert db.get_follow(1, 111)["hi_count"] == 5
    assert db.remove_follow(1, 111) is True
    assert db.follow_count() == 0
    assert db.get_lang_state(1, 111) is None
    # cache
    db.cache_set("k", "v", 60)
    assert db.cache_get("k") == "v"
    db.cache_set("neg", None, 60)
    assert db.cache_get("neg") is None and db.expired_negative("neg") is True
    assert db.stats()["users"] == 1


def test_14_platform_canon():
    from sources import platforms

    assert platforms.canon("Anime Times (Prime Video)") == "anime-times"
    assert platforms.canon("Amazon Prime Video") == "prime-video"
    assert platforms.canon("Muse India") == "muse-india"
    assert platforms.canon("crunchyroll") == "crunchyroll"
    assert platforms.display("Anime Times (Prime Video)") == "Anime Times"
    assert platforms.is_india_available("Crunchyroll") is True
    assert platforms.is_india_available("Hulu") is False
    assert platforms.dedupe_preserve(["Crunchyroll", "crunchyroll", "Netflix"]) == ["Crunchyroll", "Netflix"]


def test_15_franchise_key_stripping():
    assert franchise_key("Mushoku Tensei: Jobless Reincarnation Season 2 Part 2") == "mushoku tensei"
    assert franchise_key("Grand Blue Dreaming Season 3") == "grand blue dreaming"
    assert franchise_key("Demon Slayer: Kimetsu no Yaiba – Mugen Train Arc (Season 2 – Cour 1)") == "demon slayer"
    assert franchise_key("Jujutsu Kaisen 2nd Season") == "jujutsu kaisen"
    assert franchise_key("One Piece: Egghead Arc (Ep. 1089-1128)") == "one piece"


def test_16_card_cache_sqlite():
    db = Database(":memory:")
    agg = make_aggregator(cache=db)
    d1 = run(agg.build_card_by_query("black torch", force=True))[1]
    assert db.get_card(d1.anime_id) is not None
    # cache hit (force=False)
    d2 = run(agg.build_card(d1.anime_id))
    assert isinstance(d2, CardData)
    assert d2.hi == d1.hi and d2.title == d1.title
    # round-trip me data lost nahi hona chahiye
    restored = CardData.from_dict(json.loads(db.get_card(d1.anime_id)))
    assert restored.next_hi_date == d1.next_hi_date
    assert restored.seasons[0].hi_start == d1.seasons[0].hi_start


def test_17_title_variants_for_aninidhi():
    from sources.aninidhi_src import title_variants

    v = title_variants(["Sparks of Tomorrow", "Nijusseiki Denki Mokuroku: Eureka Evrika", "二十世紀電氣目錄"])
    assert v[0] == "Sparks of Tomorrow"
    assert "Nijusseiki Denki Mokuroku" in v
    # AniNidhi me English naam se record hai -> mil jaana chahiye
    src = AniNidhiSource()
    recs = run(src.lookup(["Sparks of Tomorrow", "Nijusseiki Denki Mokuroku: Eureka Evrika"]))
    assert recs, "Sparks of Tomorrow ka Netflix Hindi dub milna chahiye"
    assert any(r.display_platform == "Netflix" for r in recs)


def test_18_anischedule_english_dub_math():
    """AnimeSchedule EN dub: parsing + weekly math (sab OFFLINE, real fixtures)."""
    import json

    from sources.anischedule import AnimeScheduleSource, en_dub_progress

    rows = json.loads((Path(__file__).parent / "anischedule_fixtures.json").read_text())
    bt = AnimeScheduleSource._parse(187538, rows["black_torch"])
    assert bt.has_dub_data and bt.dub_premier == date(2026, 7, 4)
    prog = en_dub_progress(bt, TODAY)
    assert prog.count == 12 and prog.complete, "4 Jul + weekly -> 22 Sep tak 12/12"

    ms3 = AnimeScheduleSource._parse(178789, rows["mushoku_s3"])
    prog3 = en_dub_progress(ms3, TODAY)
    assert prog3.count == 10, prog3.count
    assert prog3.next_date == date(2026, 9, 27) and prog3.estimated

    op = AnimeScheduleSource._parse(21, rows["one_piece"])
    progop = en_dub_progress(op, TODAY)
    assert progop.count is None, "One Piece ongoing, total unknown -> count Unknown (guess nahi)"
    assert progop.next_dt is not None and progop.estimated, "weekly pattern se next"

    bleach = AnimeScheduleSource._parse(269, rows["bleach"])
    assert bleach.has_dub_data is False
    assert en_dub_progress(bleach, TODAY).count is None

    # dubinfo (self-hosted, optional) disabled-by-default bhi abhi hai
    from sources.dubinfo import DubInfoSource

    assert DubInfoSource(base_url="").enabled is False
    assert run(DubInfoSource(base_url="").lookup(["anything"])) == []


def test_19_ist_formatting():
    dt = datetime.fromtimestamp(1790506800, tz=timezone.utc)
    assert config.ts_ist(dt) == "27 Sep 2026, 04:30 PM IST"
    assert config.date_ist_short(date(2026, 9, 26)) == "26 Sep 2026"


def test_20_version_tag_everywhere():
    agg = make_aggregator()
    data = run(agg.build_card_by_query("suzume", force=True))[1]
    assert formatter.format_card(data).rstrip().endswith(f"🤖 {config.VERSION}")
    assert config.VERSION in texts.VERSION_MSG.format(
        version=config.VERSION, version_long=config.VERSION_LONG,
        poll_minutes=20, ist_now="x"
    )


# ---------------------------------------------------------------------------
# bina pytest ke chalane par bhi sab test chalein
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    failures = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
