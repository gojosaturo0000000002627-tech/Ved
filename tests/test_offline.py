"""Offline test — mock + REAL aninidhi data ke saath poora pipeline.

Run: python tests/test_offline.py
(AniList/dubinfo/YouTube mocked — aninidhi REAL package use hota hai)
"""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import formatter
from database import Database
from sources import aggregator, anilist, anischedule, dubinfo, youtube, aninidhi_src

# ---- Mock data (AniList sandbox se reachable nahi, isliye mock) ----
# Real facts use kiye hain: 12 episodes, ep 12 -> 19 Sep 2026, ab season complete
MOCK_ANILIST = {
    "anilist_id": 123456,
    "mal_id": 555000,
    "title": "Black Torch",
    "romaji": "Black Torch",
    "native": "ブラックトーチ",
    "status": "FINISHED",
    "status_display": "Completed ✅ (sab episodes release ho chuke)",
    "episodes": 12,
    "format": "TV",
    "season": "SUMMER",
    "year": 2026,
    "next_episode": None,
    "next_airing_at": None,
    "links": [
        {"site": "Crunchyroll", "url": "https://crunchyroll.com/black-torch"},
        {"site": "Netflix", "url": "https://netflix.com/title/123"},
    ],
}

# dubinfo purana data deta hai (3 eps) — aninidhi fresh hai (4) — max lena chahiye
MOCK_DUBINFO = {
    "title": "Black Torch",
    "platforms": [
        {"name": "Crunchyroll", "url": "https://crunchyroll.com/x",
         "region": "India", "langs": ["jp", "en", "hi"]},
    ],
    "langs": {
        "jp": {"released": 12, "latest": "2026-09-19T15:30:00Z"},
        "en": {"released": 12, "latest": "2026-09-19T15:30:00Z"},
        "hi": {"released": 3, "latest": "2026-09-12T12:00:00Z"},
    },
}


async def mock_get_anime(anilist_id):
    return dict(MOCK_ANILIST)


async def mock_get_dub_info(query, base_url):
    return dict(MOCK_DUBINFO)


async def mock_as_search(query, token):
    return []


async def mock_yt_find(query):
    return None  # Black Torch YouTube pe nahi hai (Crunchyroll pe hai)


async def main():
    anilist.get_anime = mock_get_anime
    dubinfo.get_dub_info = mock_get_dub_info
    anischedule.search_anime = mock_as_search
    youtube.find_episodes = mock_yt_find

    dbpath = os.path.join(tempfile.mkdtemp(), "test.db")
    db = Database(dbpath)

    # ---- 1. REAL aninidhi — Black Torch ka actual record ----
    r = aninidhi_src.hindi_dub_status("Black Torch", total=12)
    assert r is not None and r["platforms"], "aninidhi record milna chahiye"
    assert any(p["platform"] == "Crunchyroll" for p in r["platforms"])
    # Real check: 29 Aug start, weekly => aaj tak jitne hone chahiye
    from datetime import date
    expected_eps = (date.today() - date(2026, 8, 29)).days // 7 + 1
    assert r["eps"] == min(expected_eps, 12), f"eps={r['eps']} != {expected_eps}"
    print(f"[OK] aninidhi REAL — eps={r['eps']}, next={r['next']}")

    # ---- 2. Season-aware + variant matching (REAL data) ----
    r2 = aninidhi_src.hindi_dub_status("Dan Da Dan 2nd Season")
    assert r2 and r2["found"], "variant matching se Season 2 milna chahiye"
    assert any(p["platform"] == "Crunchyroll" for p in r2["platforms"])
    assert r2["finished"], "DDD S2 ka dub finished hai"
    print("[OK] aninidhi season matching — 'Dan Da Dan 2nd Season'")

    r3 = aninidhi_src.hindi_dub_status("Jujutsu Kaisen 2nd Season")
    assert r3 and r3["found"] and r3["finished"]
    print("[OK] aninidhi season matching — 'Jujutsu Kaisen 2nd Season'")

    r4 = aninidhi_src.hindi_dub_status("Bleach")
    assert r4 is not None and not r4.get("found"), "Bleach nahi milna chahiye (no official HI dub)"
    print("[OK] aninidhi not-found case — 'Bleach'")

    r5 = aninidhi_src.hindi_dub_status("Blue Box 2nd Season")
    if r5 and r5.get("found"):
        print(f"[OK] aninidhi upcoming — Blue Box S2: upcoming={r5.get('upcoming')}, announced={r5.get('announced')}")

    # ---- 3. Aggregator: aninidhi > purana dubinfo (max lena chahiye) ----
    info = await aggregator.get_anime_info(123456, db=db, force=True)
    assert info["hi_aired"] == r["eps"], f"hi_aired={info['hi_aired']}"
    assert info["en_aired"] == 12
    assert info["jp_aired"] == 12  # FINISHED => sab episodes
    assert info["hi_source"] == "aninidhi"
    assert info["next_by_lang"]["hi"] == r["next"].strftime("%d %b %Y (estimated)")
    print(f"[OK] aggregator — hi={info['hi_aired']}, next_hi={info['next_by_lang']['hi']}")

    # ---- 4. Card ----
    card = formatter.format_card(info)
    print("\n---------- CARD ----------")
    print(card)
    print("--------------------------\n")
    for must in ("🎬 Black Torch", "Hindi dub: 4 episodes" if r["eps"] == 4 else "Hindi dub:",
                 "📅 Next episode:", "Crunchyroll"):
        assert must in card, f"card me missing: {must}"
    print("[OK] formatter card")

    # ---- 5. Past-date guard (_est_next) ----
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    out = aggregator._est_next(old)
    assert out is None or "(estimated)" in out, "past date se future estimate hi aana chahiye"
    now = datetime.now(timezone.utc)
    assert aggregator._est_next(now + timedelta(days=7)) is not None
    # purani date aage bump hoti hai, past me nahi rehti
    if out:
        print(f"[OK] past-date guard — 2020 se -> {out}")
    else:
        print("[OK] past-date guard — None (8+ hafte purana)")

    # ---- 6. Manual fix (/setep) ----
    db.set_manual_fix(123456, "hi", 5)
    info2 = await aggregator.get_anime_info(123456, db=db, force=True)
    assert info2["hi_aired"] == 5
    assert info2["hi_source"] == "manual"
    assert "/setep se set kiya" in formatter.format_card(info2)
    print("[OK] manual fix (/setep)")

    # ---- 7. Notification (user ke exact format mein) ----
    new_ep = r["eps"] + 1
    note = formatter.format_notification(info, "hi", new_ep, 12)
    print("---------- NOTIF ----------")
    print(note)
    print("---------------------------")
    assert "aa chuka hai 🎉" in note
    assert f"Episode <b>{new_ep}</b> (Hindi dub)" in note
    assert "Platform: Crunchyroll (India)" in note, "sirf relevant platform dikhna chahiye"
    assert "Netflix" not in note, "Hindi dub notification me Netflix nahi aana chahiye"
    assert f"Hindi dub: {new_ep}/12 episodes" in note
    assert "⏱ " in note
    assert formatter.notification_watch_url(info, "hi"), "watch URL milna chahiye"
    note_jp = formatter.format_notification(info, "jp", 5, 12)
    assert "Japanese audio: 5/12 episodes" in note_jp
    print("[OK] notification format — user ke format jaisa")

    # ---- 8. YouTube feed parsing (pure function) ----
    sample_feed = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><title>Black Torch Episode 4 [Hindi Dub]</title>
  <published>2026-09-15T15:30:00+00:00</published>
  <link href="https://youtu.be/xyz"/></entry>
  <entry><title>Some Other Anime Episode 7 Hindi Dub</title>
  <published>2026-09-14T15:30:00+00:00</published>
  <link href="https://youtu.be/abc"/></entry>
</feed>"""
    entries = youtube.parse_feed(sample_feed)
    assert len(entries) == 2
    assert youtube.extract_episode("Black Torch Episode 4 [Hindi Dub]") == 4
    assert youtube.extract_episode("EP. 12 Hindi") == 12
    assert youtube._matches("Black Torch", "BLACK TORCH Episode 4 [Hindi Dub]")
    assert not youtube._matches("Black Torch", "Some Other Anime Episode 7")
    print("[OK] YouTube feed parse")

    # ---- 9. Database ----
    db.upsert_user(999, "Shinchan")
    counts = aggregator.lang_counts(info)
    db.add_follow(999, 123456, "Black Torch", "", ["hi"], counts)
    f = db.get_follow(999, 123456)
    assert f["last_counts"]["hi"] == info["hi_aired"]
    db.set_lang_state(123456, "hi", counts["hi"], "2026-09-19T10:00:00+00:00")
    st = db.get_lang_states(123456)
    assert st["hi"]["last_ep"] == counts["hi"]
    print("[OK] database + lang_state")

    print("\n✅ SAB TESTS PASS HO GAYE!")


if __name__ == "__main__":
    asyncio.run(main())
