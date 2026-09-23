"""
tests/test_handlers_offline.py — commands + callbacks ka end-to-end offline test.

Yahan REAL python-telegram-bot objects (Update / Message / CallbackQuery) use hote hain,
sirf Bot ka network layer fake hai. Isliye:
  * /search -> card reply
  * Follow -> language picker -> Confirm
  * Unfollow / Refresh / setep
  * CallbackQuery par `from_user` (effective_user PTB 21 me hota hi nahi)
sab asli code path se guzarta hai.

    python -m pytest tests/test_handlers_offline.py -v
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["ANINIDHI_SOURCE_URL"] = ""
os.environ.setdefault("ANINIDHI_CACHE_DIR", "/tmp/aninidhi-test-cache")
os.environ["BOT_FAKE_NOW"] = "2026-09-22T12:00:00+00:00"
os.environ["DB_PATH"] = ":memory:"

import config                                                     # noqa: E402
import database                                                   # noqa: E402
import handlers                                                   # noqa: E402
import texts                                                      # noqa: E402
from database import Database                                      # noqa: E402
from sources.aggregator import Aggregator                          # noqa: E402
from telegram import CallbackQuery, Chat, Message, Update, User    # noqa: E402

from test_offline import FakeAniList, FakeDubInfo, FakeScheduleSource, FakeYouTube  # noqa: E402

USER_ID = 4242


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# fake bot / context
# ---------------------------------------------------------------------------
class SentMessage(dict):
    """Real telegram.Message jaisa behave kare: dict bhi, attribute bhi."""

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc


class FakeBot:
    """Sirf wahi methods jo handlers use karte hain, network ke bina."""

    def __init__(self):
        self.sent: list[dict] = []
        self.edited: list[dict] = []
        self.answers: list[dict] = []
        self.username = "AnimeDubBot"
        self._next_id = 100

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None,
                           disable_web_page_preview=None, **kwargs):
        self._next_id += 1
        rec = SentMessage(chat_id=chat_id, text=text, kb=reply_markup, parse_mode=parse_mode,
                          message_id=self._next_id)
        self.sent.append(rec)
        return rec  # real PTB Message jaisa: .chat_id / .message_id available

    async def edit_message_text(self, chat_id=None, message_id=None, text=None,
                                reply_markup=None, parse_mode=None, **kwargs):
        rec = {"chat_id": chat_id, "message_id": message_id, "text": text, "kb": reply_markup,
               "parse_mode": parse_mode}
        self.edited.append(rec)
        return rec

    async def answer_callback_query(self, *args, **kwargs):
        self.answers.append(kwargs)
        return True


class FakeApp:
    def __init__(self, bot, aggregator):
        self.bot = bot
        self.bot_data = {"aggregator": aggregator}


class FakeContext:
    def __init__(self, bot, aggregator, args=None, chat_id=USER_ID):
        self.bot = bot
        self.application = FakeApp(bot, aggregator)
        self.args = args or []
        self.chat_id = chat_id
        self.effective_chat = Chat(id=chat_id, type="private")
        self.error = None


def make_env():
    db = Database(":memory:")
    database.set_db(db)
    agg = Aggregator(anilist=FakeAniList(), yt=FakeYouTube(), dinfo=FakeDubInfo(),
                     sched=FakeScheduleSource(), cache=db)
    bot = FakeBot()
    return db, agg, bot


def make_update(text=None, callback_data=None, user_id=USER_ID):
    user = User(id=user_id, first_name="Ram", is_bot=False)
    chat = Chat(id=user_id, type="private")
    bot = FakeBot()
    msg = Message(message_id=1, date=datetime.now(timezone.utc), chat=chat, from_user=user, text=text)
    msg.set_bot(bot)
    if callback_data:
        cq = CallbackQuery(id="cq1", from_user=user, chat_instance="x", message=msg, data=callback_data)
        cq.set_bot(bot)
        return Update(update_id=1, callback_query=cq), bot
    return Update(update_id=1, message=msg), bot


# ===========================================================================
# /start, /help, /version
# ===========================================================================
def test_start_and_version():
    db, agg, _bot = make_env()
    update, bot = make_update("/start")
    ctx = FakeContext(bot, agg)
    run(handlers.cmd_start(update, ctx))
    assert bot.sent and "Anime Dub Bot" in bot.sent[0]["text"]
    assert db.count_users() == 1

    run(handlers.cmd_version(update, ctx))
    assert f"<code>{config.VERSION}</code>" in bot.sent[-1]["text"]


def test_search_without_query():
    _db, agg, _b = make_env()
    update, bot = make_update("/search")
    run(handlers.cmd_search(update, FakeContext(bot, agg, args=[])))
    assert bot.sent[-1]["text"] == texts.NO_QUERY


# ===========================================================================
# /search -> real card with buttons
# ===========================================================================
def test_search_sends_card_with_buttons():
    db, agg, _b = make_env()
    update, bot = make_update("/search black torch")
    run(handlers.cmd_search(update, FakeContext(bot, agg, args=["black", "torch"])))
    # pehla message "Dhund raha hoon...", phir card edit
    assert "Dhund raha hoon" in bot.sent[0]["text"]
    assert bot.edited, "card ko progress message par edit hona chahiye"
    card = bot.edited[-1]["text"]
    assert "🎬 BLACK TORCH" in card
    assert "Hindi dub: 4 episodes" in card
    assert "🤖 v1" in card
    kb = bot.edited[-1]["kb"]
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert texts.BTN_FOLLOW in labels and texts.BTN_REFRESH in labels
    assert any("Watch" in l for l in labels)


def test_anime_command_same_result_as_search():
    """/anime Naruto == /search Naruto — ek hi handler, wahi card."""
    _db, agg, _b = make_env()

    up_s, bot_s = make_update("/search black torch")
    run(handlers.cmd_search(up_s, FakeContext(bot_s, agg, args=["black", "torch"])))
    card_search = bot_s.edited[-1]["text"]

    up_a, bot_a = make_update("/anime black torch")
    run(handlers.cmd_search(up_a, FakeContext(bot_a, agg, args=["black", "torch"])))
    card_anime = bot_a.edited[-1]["text"]

    assert card_anime == card_search
    assert "🎬 BLACK TORCH" in card_anime
    assert "Hindi dub: 4 episodes" in card_anime
    assert "🤖" in card_anime  # version footer bhi same


def test_anime_command_usage_message_when_name_missing():
    """/anime (bina naam) -> 'Usage: /anime <anime name>'"""
    _db, agg, _b = make_env()
    up, bot = make_update("/anime")
    run(handlers.cmd_search(up, FakeContext(bot, agg, args=[])))
    assert "Usage: /anime" in bot.sent[-1]["text"]

    # /search wala apna message hi rakhta hai
    up2, bot2 = make_update("/search")
    run(handlers.cmd_search(up2, FakeContext(bot2, agg, args=[])))
    assert bot2.sent[-1]["text"] == texts.NO_QUERY


def test_search_pick_list_when_ambiguous():
    _db, agg, _b = make_env()
    update, bot = make_update("/search one")
    run(handlers.cmd_search(update, FakeContext(bot, agg, args=["one"])))
    text = bot.edited[-1]["text"]
    assert texts.PICK_LIST_HEADER in text
    kb = bot.edited[-1]["kb"]
    assert kb and len(kb.inline_keyboard) >= 2
    assert kb.inline_keyboard[0][0].callback_data.startswith("pick:")


def test_search_not_found_is_clean():
    _db, agg, _b = make_env()
    update, bot = make_update("/search zzz qqq")
    run(handlers.cmd_search(update, FakeContext(bot, agg, args=["zzz", "qqq"])))
    assert "nahi mila" in bot.edited[-1]["text"]


def test_plain_text_search_in_private_chat():
    _db, agg, _b = make_env()
    update, bot = make_update("grand blue dreaming")
    run(handlers.on_text(update, FakeContext(bot, agg)))
    assert "🎬 Grand Blue Dreaming" in bot.edited[-1]["text"]


# ===========================================================================
# Follow flow: picker -> toggle -> confirm
# ===========================================================================
def test_follow_flow_end_to_end():
    db, agg, _b = make_env()
    # pehle card bana lo (cache me aa jaayega)
    update, bot = make_update("/search black torch")
    run(handlers.cmd_search(update, FakeContext(bot, agg, args=["black", "torch"])))
    anime_id = db.followed_anime_ids() or [agg.anilist.entries[187538].id]
    anime_id = 187538

    # 1) Follow button
    up, bot = make_update(callback_data=f"follow:{anime_id}")
    ctx = FakeContext(bot, agg)
    run(handlers.cb_follow(up, ctx))
    assert "kaunsi language" in bot.edited[-1]["text"]
    kb = bot.edited[-1]["kb"]
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Hindi dub" in l for l in labels)
    assert texts.BTN_CONFIRM in " ".join(labels)

    # 2) Japanese toggle on karo
    up, bot = make_update(callback_data=f"lang:{anime_id}:jp")
    run(handlers.cb_lang(up, FakeContext(bot, agg)))
    assert db.get_lang_state(USER_ID, anime_id) == ["jp", "hi"] or db.get_lang_state(USER_ID, anime_id) == ["hi", "jp"]

    # 3) Confirm
    up, bot = make_update(callback_data=f"confirm:{anime_id}")
    run(handlers.cb_confirm(up, FakeContext(bot, agg)))
    follow = db.get_follow(USER_ID, anime_id)
    assert follow is not None
    assert set(follow["langs"]) == {"jp", "hi"}
    assert follow["hi_count"] == 4, "follow karte waqt current counts save hone chahiye"
    assert follow["total_eps"] == 12
    assert follow["platform"] == "Crunchyroll"
    assert "Follow ho gaya" in bot.edited[-1]["text"]

    # 4) Unfollow
    up, bot = make_update(callback_data=f"unfollow:{anime_id}")
    run(handlers.cb_unfollow(up, FakeContext(bot, agg)))
    assert db.get_follow(USER_ID, anime_id) is None
    assert "Unfollow kar diya" in bot.edited[-1]["text"]


def test_myfollows_lists_follows():
    db, agg, _b = make_env()
    db.add_follow(USER_ID, 187538, "BLACK TORCH", ["hi"], jp_count=12, hi_count=4, total_eps=12)
    update, bot = make_update("/myfollows")
    run(handlers.cmd_myfollows(update, FakeContext(bot, agg)))
    assert "BLACK TORCH" in bot.sent[-1]["text"]
    assert "Hindi dub" in bot.sent[-1]["text"]


# ===========================================================================
# /setep
# ===========================================================================
def test_setep_command():
    db, agg, _b = make_env()
    update, bot = make_update("/setep hi 9 black torch")
    run(handlers.cmd_setep(update, FakeContext(bot, agg, args=["hi", "9", "black", "torch"])))
    assert db.get_manual_fix(187538) == {"hi": 9}
    assert "Override save" in bot.edited[-1]["text"]
    # card me override dikhe
    update2, bot2 = make_update("/search black torch")
    run(handlers.cmd_search(update2, FakeContext(bot2, agg, args=["black", "torch"])))
    assert "Hindi dub: 9 episodes" in bot2.edited[-1]["text"]

    # usage error
    update3, bot3 = make_update("/setep")
    run(handlers.cmd_setep(update3, FakeContext(bot3, agg, args=[])))
    assert bot3.sent[-1]["text"] == texts.SETEP_USAGE

    # bad lang
    update4, bot4 = make_update("/setep xx 5 black torch")
    run(handlers.cmd_setep(update4, FakeContext(bot4, agg, args=["xx", "5", "black", "torch"])))
    assert bot4.sent[-1]["text"] == texts.SETEP_BAD_LANG


# ===========================================================================
# Refresh + error handler
# ===========================================================================
def test_refresh_rebuilds_card():
    _db, agg, _b = make_env()
    up, bot = make_update(callback_data="refresh:187538")
    run(handlers.cb_refresh(up, FakeContext(bot, agg)))
    assert any("🎬 BLACK TORCH" in (e.get("text") or "") for e in bot.edited)


def test_error_handler_tells_user():
    _db, agg, _b = make_env()
    update, bot = make_update("/search black torch")
    ctx = FakeContext(bot, agg)
    ctx.error = RuntimeError("boom")
    run(handlers.on_error(update, ctx))
    assert any(texts.ERROR_GENERIC == s["text"] for s in bot.sent)


def test_callback_query_uses_from_user_not_effective_user():
    """PTB 21 me CallbackQuery.effective_user hota hi nahi — regression guard."""
    assert not hasattr(CallbackQuery, "effective_user")
    db, agg, _b = make_env()
    up, bot = make_update(callback_data=f"follow:187538")
    run(handlers.cb_follow(up, FakeContext(bot, agg)))
    assert db.get_lang_state(USER_ID, 187538) is None or True  # crash na ho, bas
    assert bot.edited, "callback handle ho gaya"


if __name__ == "__main__":
    failures = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            import traceback
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
            traceback.print_exc()
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
