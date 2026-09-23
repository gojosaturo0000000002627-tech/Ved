"""
tests/test_live.py — REAL network tests (AniList + YouTube RSS + AniNidhi refresh).

Default me SKIP hote hain (offline promise). Chalane ke liye:

    RUN_LIVE=1 python -m pytest tests/test_live.py -v

Note: offline tests clock freeze use karte hain; live tests asli date use karte hain
(isliye har test ke around env save/restore hota hai).
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RUN_LIVE = os.environ.get("RUN_LIVE", "").strip() == "1"
pytestmark = pytest.mark.skipif(not RUN_LIVE, reason="live test — RUN_LIVE=1 se chalao")


def run(coro):
    return asyncio.run(coro)


class _EnvGuard:
    """Test ke dauraan fake clock hatao, baad me wapas laga do."""

    def __enter__(self):
        self.old = os.environ.pop("BOT_FAKE_NOW", None)
        return self

    def __exit__(self, *exc):
        if self.old is not None:
            os.environ["BOT_FAKE_NOW"] = self.old
        else:
            os.environ.pop("BOT_FAKE_NOW", None)


class _QuietYT:
    """Kaam-ka stub: har live test ko FRESH AniList client + na-chalne wala YT milta hai.
    (Module-level singleton ko cross-event-loop reuse karna 'Event loop is closed' deta hai.)"""

    def __init__(self):
        self.calls = 0

    async def scan(self, titles, season=None, hindi_only=True):
        self.calls += 1
        return []

    async def aclose(self):
        pass


def _agg():
    from database import Database
    from sources.aggregator import Aggregator
    from sources.anilist import AniListClient
    from sources.aninidhi_src import AniNidhiSource

    return Aggregator(
        anilist=AniListClient(),          # fresh client per test — pooled connections loop-bound hote hain
        aninidhi=AniNidhiSource(),
        yt=_QuietYT(),
        cache=Database(":memory:"),
    )


def test_live_anilist_card_black_torch():
    """Real AniList + real AniNidhi -> Black Torch card (network ke saath)."""
    with _EnvGuard():

        async def _work():
            # NOTE: client ka aclose() USI loop me hona chahiye jisme bana tha —
            # alag loop me karne par anyio 'Event loop is closed' deta hai
            agg = _agg()
            try:
                return await agg.build_card_by_query("black torch", force=True)
            finally:
                await agg.anilist.aclose()

        outcome, data = run(_work())
    assert data is not None
    assert data.title.upper() == "BLACK TORCH"
    assert data.hindi_found is True
    assert "Crunchyroll" in data.hi_platforms
    assert data.planned_total == 12
    assert data.jp == 12


def test_live_youtube_muse_india_feed_parse():
    """
    Real YouTube RSS: Muse India feed parse + episode extraction + Hindi detection.

    NOTE: RSS sirf latest ~15 videos deta hai aur Muse India roz 15+ uploads karta hai,
    isliye kisi EK show ke Hindi dub ka assert karna flaky hota hai (kal wala episode
    aaj feed se scroll ho chuka hota hai). Isliye yahan machinery-level assertions hain:
      * feed aata hai aur entries parse hoti hain
      * jis title me episode number hai, wo nikaala jaata hai
      * 'Hindi' wale titles ko is_hindi detect karta hai (dono directions consistent)
      * scan() end-to-end chalta hai (handle resolve -> feed -> filter)
    """
    with _EnvGuard():
        from sources.youtube import YouTubeSource

        async def _work():
            yt = YouTubeSource()
            try:
                cid = await yt.channel_id_for(
                    {"name": "Muse India", "channel_id": "UCYYhAzgWuxPauRXdPpLAX3Q"}
                )
                try:
                    videos = await yt.feed(cid) if cid else []
                except httpx.HTTPStatusError as exc:
                    # 404/500 agar doosre healthy channels par BHI mil rahe hain
                    # to ye YouTube ka is-IP soft-block hai (scraping ke baad hota
                    # hai) — channel gone nahi. Aise me skip, fail nahi.
                    if exc.response.status_code in (404, 500):
                        blocked = 0
                        for probe in ("UC0wNSTMWIL3qaorLx0jie6A", "UCGbshtvS9t-8CW11W7TooQg"):
                            try:
                                await yt.feed(probe)
                            except httpx.HTTPStatusError as perr:
                                if perr.response.status_code in (404, 500):
                                    blocked += 1
                        if blocked >= 2:
                            pytest.skip("YouTube RSS is IP par block hai (env-level, code theek)")
                    raise
                hits = await yt.scan(
                    ["Campfire Cooking", "God of High School", "JoJo", "BLACK TORCH"],
                    season=None,
                    hindi_only=True,
                )
                return videos, hits
            finally:
                await yt.aclose()

        videos, hits = run(_work())

    assert len(videos) >= 5, "Muse India RSS me videos hone chahiye"
    with_ep = [(v.title, v.episode()) for v in videos if v.episode()[0] is not None]
    assert with_ep, "kam se kam kuch titles se episode number nikaalna chahiye"
    for _title, (ep, season) in with_ep:
        assert 1 <= ep <= 2000, ep
        if season is not None:
            assert 1 <= season <= 30, season
    # Hindi detection consistent ho (regex 'hindi' dhoondta hai)
    by_flag = {v.title for v in videos if v.is_hindi}
    by_text = {v.title for v in videos if "hindi" in v.title.lower()}
    assert by_flag == by_text, "is_hindi aur title-text mismatch"
    # scan ke results well-formed hon (content roz badalta hai, isliye empty bhi chalega)
    for h in hits:
        assert h["is_hindi"] and isinstance(h["episode"], int) and h["url"]


def test_live_youtube_bad_handle_negative_cache():
    """@MuseIndia (404 deta hai) resolve fail -> turant None, aur dobara call par bhi skip."""
    with _EnvGuard():
        from sources.youtube import YouTubeSource

        async def _negative():
            yt = YouTubeSource()
            calls = {"n": 0}

            async def counting_resolve(handle):
                calls["n"] += 1
                return await YouTubeSource._resolve_handle(yt, handle)

            try:
                yt._resolve_handle = counting_resolve
                got1 = await yt.channel_id_for({"name": "Muse India", "handle": "MuseIndia"})
                got2 = await yt.channel_id_for({"name": "Muse India", "handle": "MuseIndia"})
                return got1, got2, calls["n"]
            finally:
                await yt.aclose()

        got1, got2, n = run(_negative())
    assert got1 is None and got2 is None
    assert n == 1, "negative cache se dobara network call nahi jani chahiye"


def test_live_aninidhi_fuzzy_and_search():
    """Real AniNidhi (package + dataset refresh): typo search aur Sparks of Tomorrow."""
    with _EnvGuard():
        from sources.aninidhi_src import AniNidhiSource

        src = AniNidhiSource()
        rows = run(src.all_records(force=True))
        sugg = run(src.fuzzy_titles("mushoko tensai"))
        recs = run(src.lookup(["Sparks of Tomorrow"]))
    assert len(rows) >= 400
    assert sugg and "mushoku" in sugg[0].lower()
    assert any(r.display_platform == "Netflix" for r in recs)


def test_live_anischedule_english_dub():
    """Real AnimeSchedule API: AniList-ID match + EN dub data (Black Torch/One Piece/Bleach)."""
    with _EnvGuard():
        from sources.anischedule import AnimeScheduleSource, en_dub_progress

        async def _work():
            src = AnimeScheduleSource()
            try:
                return await src.lookup_many([187538, 21, 269])
            finally:
                await src.aclose()

        m = run(_work())
    from datetime import date

    bt = m.get(187538)
    assert bt is not None and bt.has_dub_data, "Black Torch EN dub data milna chahiye"
    prog = en_dub_progress(bt, date.today())
    assert prog.count is not None  # 12, complete
    op = m.get(21)
    assert op is not None and op.has_dub_data
    bleach = m.get(269)
    assert bleach is None or not bleach.has_dub_data  # track nahi karta -> Unknown theek


def test_live_notifier_platform_url_matching():
    """Real card se JP/EN/HI teeno ke liye sahi platform+URL pair bante hain."""
    with _EnvGuard():

        async def _work():
            agg = _agg()
            try:
                return (await agg.build_card_by_query("black torch", force=True))[1]
            finally:
                await agg.anilist.aclose()

        data = run(_work())
    from notifier import Notifier

    assert data is not None
    hi_platform, hi_url = Notifier._platform_for("hi", data)
    assert hi_platform == "Crunchyroll"
    assert hi_url and "crunchyroll.com" in hi_url
    jp_platform, jp_url = Notifier._platform_for("jp", data)
    assert jp_platform and jp_url
    en_platform, en_url = Notifier._platform_for("en", data)
    assert en_platform and en_url
