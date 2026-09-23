"""
handlers.py — commands + callback queries.

Note: CallbackQuery par `effective_user` NAHI hota — hamesha `query.from_user` use karo.
"""
from __future__ import annotations

import asyncio
import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
import formatter
import texts
from database import LANG_LABEL, LANGS, get_db
from sources.aggregator import Aggregator, CardData
from sources.anilist import AniListRateLimited

log = logging.getLogger("handlers")

CB_PICK = "pick"
CB_FOLLOW = "follow"
CB_UNFOLLOW = "unfollow"
CB_REFRESH = "refresh"
CB_LANG = "lang"
CB_CONFIRM = "confirm"
CB_CANCEL = "cancel"
CB_CLOSE = "close"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _aggregator(context: ContextTypes.DEFAULT_TYPE) -> Aggregator:
    return context.application.bot_data["aggregator"]


def _db():
    return get_db()


def _user_id(update: Update) -> int | None:
    """CallbackQuery me effective_user nahi hota — isliye ye helper."""
    if update.effective_user:
        return update.effective_user.id
    if update.callback_query and update.callback_query.from_user:
        return update.callback_query.from_user.id
    return None


def card_keyboard(data: CardData, user_id: int) -> InlineKeyboardMarkup:
    """Card ke neeche buttons: Follow/Unfollow + Refresh (+ Watch/AniList)."""
    following = _db().get_follow(user_id, data.anime_id) is not None
    row1 = []
    if following:
        row1.append(InlineKeyboardButton(texts.BTN_UNFOLLOW, callback_data=f"{CB_UNFOLLOW}:{data.anime_id}"))
    else:
        row1.append(InlineKeyboardButton(texts.BTN_FOLLOW, callback_data=f"{CB_FOLLOW}:{data.anime_id}"))
    row1.append(InlineKeyboardButton(texts.BTN_REFRESH, callback_data=f"{CB_REFRESH}:{data.anime_id}"))
    rows = [row1]

    row2 = []
    if data.watch_url and data.watch_platform:
        row2.append(
            InlineKeyboardButton(
                texts.BTN_WATCH.format(platform=data.watch_platform)[:60],
                url=data.watch_url,
            )
        )
    if data.anilist_url:
        row2.append(InlineKeyboardButton(texts.BTN_ANILIST, url=data.anilist_url))
    if row2:
        rows.append(row2)
    return InlineKeyboardMarkup(rows)


def lang_keyboard(anime_id: int, selected: list[str]) -> InlineKeyboardMarkup:
    """Multi-select language toggles + confirm."""
    toggles = []
    for lang in LANGS:
        mark = "✅" if lang in selected else "⬜"
        label = texts.BTN_LANG[lang]
        toggles.append(InlineKeyboardButton(f"{mark} {label}", callback_data=f"{CB_LANG}:{anime_id}:{lang}"))
    return InlineKeyboardMarkup(
        [
            [toggles[0]],
            [toggles[1]],
            [toggles[2]],
            [
                InlineKeyboardButton(texts.BTN_CONFIRM, callback_data=f"{CB_CONFIRM}:{anime_id}"),
                InlineKeyboardButton(texts.BTN_CANCEL, callback_data=f"{CB_CANCEL}:{anime_id}"),
            ],
        ]
    )


async def _send_card(update: Update, context: ContextTypes.DEFAULT_TYPE, data: CardData, edit_message_id: int | None = None) -> None:
    text = formatter.format_card(data)
    user_id = _user_id(update) or 0
    kb = card_keyboard(data, user_id)
    if edit_message_id and update.effective_chat:
        try:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=edit_message_id,
                text=text,
                reply_markup=kb,
            )
            return
        except Exception as exc:  # noqa: BLE001 - Telegram "message is not modified" etc.
            log.info("edit fail, naya message bhejte hain: %s", exc)
    if update.effective_chat:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=kb)


async def _search_and_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str, progress_msg_id: int | None = None) -> None:
    agg = _aggregator(context)
    try:
        outcome, data = await asyncio.wait_for(agg.build_card_by_query(query), timeout=config.CARD_BUILD_TIMEOUT)
    except asyncio.TimeoutError:
        await _edit_or_send(context, progress_msg_id, texts.TOO_SLOW)
        return
    except AniListRateLimited:
        await _edit_or_send(context, progress_msg_id, texts.ANILIST_RATELIMIT)
        return
    except ConnectionError as exc:
        log.warning("search fail: %s", exc)
        await _edit_or_send(context, progress_msg_id, texts.ANILIST_DOWN)
        return
    except Exception:  # noqa: BLE001
        log.exception("search me unexpected error")
        await _edit_or_send(context, progress_msg_id, texts.BUILD_ERROR)
        return

    if data is not None:
        await _send_card(update, context, data, edit_message_id=progress_msg_id)
        return
    if outcome is not None and outcome.needs_pick:
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(e.best_title[:58], callback_data=f"{CB_PICK}:{e.id}")]
                for e in outcome.candidates
            ]
        )
        await _edit_or_send(context, progress_msg_id, formatter.format_pick_list(outcome.candidates), kb)
        return
    if outcome is not None and outcome.suggestions:
        sug = "\n".join(f"• {s}" for s in outcome.suggestions[:5])
        await _edit_or_send(context, progress_msg_id, texts.NOT_FOUND_WITH_SUGGESTIONS.format(query=html.escape(query), suggestions=sug))
        return
    await _edit_or_send(context, progress_msg_id, texts.NOT_FOUND.format(query=html.escape(query)))


async def _edit_or_send(
    context: ContextTypes.DEFAULT_TYPE,
    message_id: int | None,
    text: str,
    kb: InlineKeyboardMarkup | None = None,
    parse: str | None = ParseMode.HTML,
) -> None:
    chat_id = None
    if context.effective_chat is not None:
        chat_id = context.effective_chat.id
    elif getattr(context, "chat_id", None):
        chat_id = context.chat_id
    if chat_id is None:
        return
    if message_id:
        try:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=kb, parse_mode=parse)
            return
        except Exception as exc:  # noqa: BLE001
            log.debug("edit fail: %s", exc)
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=kb, parse_mode=parse)


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user:
        _db().touch_user(user.id, user.username)
    name = user.first_name if user else "dost"
    await update.effective_message.reply_text(texts.START.format(name=html.escape(name)), parse_mode=ParseMode.HTML)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(texts.HELP, parse_mode=ParseMode.HTML)


async def cmd_version(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        texts.VERSION_MSG.format(
            version=config.VERSION,
            version_long=config.VERSION_LONG,
            poll_minutes=config.POLL_MINUTES,
            ist_now=config.ts_ist(config.now_ist()),
        ),
        parse_mode=ParseMode.HTML,
    )


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /search aur /anime DONO isi ek handler se chalte hain (CommandHandler aliases) —
    search logic, card format, buttons, error handling sab bilkul same.
    Sirf "naam missing" wale case me usage message command ke hisaab se jaata hai.
    """
    user = update.effective_user
    if user:
        _db().touch_user(user.id, user.username)
    query = " ".join(context.args).strip() if context.args else ""
    if not query and update.effective_message:
        # "/search@BotName grand blue" style
        raw = update.effective_message.text or ""
        parts = raw.split(maxsplit=1)
        query = parts[1].strip() if len(parts) > 1 else ""
    if not query:
        invoked = "search"
        if update.effective_message:
            head = (update.effective_message.text or "").split()[0] if (update.effective_message.text or "").split() else ""
            invoked = head.lstrip("/").split("@")[0].lower() or "search"
        usage = texts.ANIME_USAGE if invoked == "anime" else texts.NO_QUERY
        await update.effective_message.reply_text(usage, parse_mode=ParseMode.HTML)
        return
    msg = await update.effective_message.reply_text(texts.SEARCHING_DETAIL)
    await _search_and_reply(update, context, query, progress_msg_id=msg.message_id)


async def cmd_myfollows(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = _user_id(update)
    if not user_id:
        return
    follows = _db().get_follows(user_id)
    await update.effective_message.reply_text(
        formatter.format_myfollows(follows), parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )


async def cmd_setep(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/setep jp|en|hi <count> <name> — manual override, sabse high priority."""
    args = list(context.args or [])
    if len(args) < 3:
        await update.effective_message.reply_text(texts.SETEP_USAGE, parse_mode=ParseMode.HTML)
        return
    lang = args[0].lower().strip()
    if lang not in LANGS:
        await update.effective_message.reply_text(texts.SETEP_BAD_LANG, parse_mode=ParseMode.HTML)
        return
    raw_count = args[1]
    clear = raw_count.lower() in ("clear", "reset", "-")
    if not clear:
        try:
            count = int(raw_count)
            if count < 0:
                raise ValueError
        except ValueError:
            await update.effective_message.reply_text(texts.SETEP_BAD_COUNT, parse_mode=ParseMode.HTML)
            return
    name = " ".join(args[2:]).strip()
    agg = _aggregator(context)
    msg = await update.effective_message.reply_text(texts.SEARCHING)
    try:
        outcome = await asyncio.wait_for(agg.resolve_search(name), timeout=config.CARD_BUILD_TIMEOUT)
    except asyncio.TimeoutError:
        await context.bot.edit_message_text(chat_id=msg.chat_id, message_id=msg.message_id, text=texts.TOO_SLOW)
        return
    except AniListRateLimited:
        await context.bot.edit_message_text(chat_id=msg.chat_id, message_id=msg.message_id, text=texts.ANILIST_RATELIMIT)
        return
    except Exception:  # noqa: BLE001
        log.exception("setep search fail")
        await context.bot.edit_message_text(chat_id=msg.chat_id, message_id=msg.message_id, text=texts.BUILD_ERROR)
        return
    if outcome.chosen is None:
        await context.bot.edit_message_text(
            chat_id=msg.chat_id, message_id=msg.message_id, text=texts.NOT_FOUND.format(query=html.escape(name))
        )
        return
    db = _db()
    anime_id = outcome.chosen.id
    title = outcome.chosen.best_title
    if clear:
        db.delete_manual_fix(anime_id, lang)
        text = texts.SETEP_CLEARED.format(title=html.escape(title), lang_label=LANG_LABEL[lang])
    else:
        db.set_manual_fix(anime_id, lang, count, note=f"via /setep by {_user_id(update)}", set_by=_user_id(update))
        text = texts.SETEP_OK.format(title=html.escape(title), lang_label=LANG_LABEL[lang], count=count)
    db.cache_set(f"card:{anime_id}", "", 1)  # card cache invalidate
    await context.bot.edit_message_text(chat_id=msg.chat_id, message_id=msg.message_id, text=text, parse_mode=ParseMode.HTML)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = _user_id(update)
    if config.ADMIN_IDS and user_id not in config.ADMIN_IDS:
        await update.effective_message.reply_text(texts.ADMIN_ONLY)
        return
    db = _db()
    agg = _aggregator(context)
    aninidhi_count = agg.aninidhi.cached_count()
    lines = [
        "📊 <b>Bot stats</b>",
        f"• Version: <code>{config.VERSION}</code>",
        f"• Follows: {db.follow_count()}",
        f"• Users: {db.count_users()}",
        f"• Followed anime: {len(db.followed_anime_ids())}",
        f"• Manual fixes: {len(db.all_manual_fixes())}",
        f"• Poll: har {config.POLL_MINUTES} min",
        f"• AniList requests: {agg.anilist.requests_made}",
        f"• AniNidhi cache: {aninidhi_count}",
        f"• Time: {config.ts_ist(config.now_ist())}",
    ]
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bina command ke naam likha ho to bhi search kar do (sirf private chat me)."""
    text = (update.effective_message.text or "").strip()
    if not text:
        return
    chat = update.effective_chat
    if chat is not None and chat.type != "private":
        # group me sirf reply/mention par kaam karo, warna bahut shor hoga
        msg = update.effective_message
        me = context.bot.username or ""
        replied_to_bot = bool(msg.reply_to_message and msg.reply_to_message.from_user
                              and msg.reply_to_message.from_user.username == me)
        if not replied_to_bot and me and me.lower() not in text.lower():
            return
    user = update.effective_user
    if user:
        _db().touch_user(user.id, user.username)
    msg = await update.effective_message.reply_text(texts.SEARCHING_DETAIL)
    await _search_and_reply(update, context, text, progress_msg_id=msg.message_id)


# ---------------------------------------------------------------------------
# callbacks
# ---------------------------------------------------------------------------
async def cb_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    anime_id = int(q.data.split(":")[1])
    await _safe_edit(q, texts.SEARCHING)
    agg = _aggregator(context)
    try:
        data = await asyncio.wait_for(agg.build_card(anime_id, force=False), timeout=config.CARD_BUILD_TIMEOUT)
    except AniListRateLimited:
        await _safe_edit(q, texts.ANILIST_RATELIMIT)
        return
    except (asyncio.TimeoutError, ConnectionError):
        await _safe_edit(q, texts.TOO_SLOW)
        return
    if data is None:
        await q.edit_message_text(texts.BUILD_ERROR)
        return
    await _send_card(update, context, data, edit_message_id=q.message.message_id if q.message else None)


async def cb_follow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    user = q.from_user
    if not user:
        return
    anime_id = int(q.data.split(":")[1])
    db = _db()
    db.touch_user(user.id, user.username)
    existing = db.get_follow(user.id, anime_id)
    selected = db.get_lang_state(user.id, anime_id) or (existing["langs"] if existing else ["hi"])
    title = existing["title"] if existing else await _title_for(context, anime_id)
    await q.edit_message_text(
        texts.FOLLOW_PICK_LANG.format(title=html.escape(title)),
        parse_mode=ParseMode.HTML,
        reply_markup=lang_keyboard(anime_id, selected),
    )


async def cb_unfollow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    user = q.from_user
    if not user:
        return
    anime_id = int(q.data.split(":")[1])
    db = _db()
    title = await _title_for(context, anime_id)
    removed = db.remove_follow(user.id, anime_id)
    text = texts.UNFOLLOW_DONE if removed else texts.NOT_FOLLOWED
    await q.edit_message_text(text.format(title=html.escape(title)), parse_mode=ParseMode.HTML)


async def cb_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    user = q.from_user
    if not user:
        return
    _parts, anime_id, lang = q.data.split(":")
    anime_id = int(anime_id)
    db = _db()
    selected = db.get_lang_state(user.id, anime_id) or ["hi"]
    if lang in selected:
        selected = [l for l in selected if l != lang]
    else:
        selected = [l for l in LANGS if l in selected + [lang]]
    db.set_lang_state(user.id, anime_id, selected)
    title = await _title_for(context, anime_id)
    await q.edit_message_text(
        texts.FOLLOW_PICK_LANG.format(title=html.escape(title)),
        parse_mode=ParseMode.HTML,
        reply_markup=lang_keyboard(anime_id, selected),
    )


async def cb_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    user = q.from_user
    if not user:
        return
    anime_id = int(q.data.split(":")[1])
    db = _db()
    selected = db.get_lang_state(user.id, anime_id) or ["hi"]
    if not selected:
        await q.answer(texts.NO_LANG_SELECTED, show_alert=True)
        return
    agg = _aggregator(context)
    data = await _card_from_cache_or_build(agg, anime_id)
    title = data.title if data else (db.get_follow(user.id, anime_id) or {}).get("title", f"#{anime_id}")
    db.add_follow(
        user_id=user.id,
        anime_id=anime_id,
        title=title,
        langs=selected,
        jp_count=data.jp if data else None,
        en_count=data.en if data else None,
        hi_count=data.hi if data else None,
        total_eps=data.planned_total if data else None,
        watch_url=data.watch_url if data else None,
        platform=(data.hi_platforms or [data.watch_platform] or [None])[0] if data else None,
    )
    db.clear_lang_state(user.id, anime_id)
    text = texts.FOLLOW_DONE.format(title=html.escape(title), langs=html.escape(texts.lang_names(selected)))
    kb = None
    if data is not None:
        kb = card_keyboard(data, user.id)
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    user = q.from_user
    if not user:
        return
    anime_id = int(q.data.split(":")[1])
    db = _db()
    db.clear_lang_state(user.id, anime_id)
    agg = _aggregator(context)
    data = await _card_from_cache_or_build(agg, anime_id)
    if data is not None:
        await _send_card(update, context, data, edit_message_id=q.message.message_id if q.message else None)
    else:
        await q.edit_message_text(texts.ERROR_GENERIC)


async def cb_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    anime_id = int(q.data.split(":")[1])
    await _safe_edit(q, texts.REFRESHING)
    agg = _aggregator(context)
    agg.anilist.clear_cache()
    try:
        data = await asyncio.wait_for(agg.build_card(anime_id, force=True), timeout=config.CARD_BUILD_TIMEOUT)
    except AniListRateLimited:
        await _safe_edit(q, texts.ANILIST_RATELIMIT)
        return
    except (asyncio.TimeoutError, ConnectionError):
        await _safe_edit(q, texts.TOO_SLOW)
        return
    if data is None:
        await q.edit_message_text(texts.BUILD_ERROR)
        return
    await _send_card(update, context, data, edit_message_id=q.message.message_id if q.message else None)


async def cb_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("👍 Band kar diya.")


# ---------------------------------------------------------------------------
# small utils
# ---------------------------------------------------------------------------
async def _safe_edit(q, text: str, **kwargs) -> None:
    """Purane messages (>48h) edit nahi hote — exception kha jao."""
    try:
        await q.edit_message_text(text=text, **kwargs)
    except Exception as exc:  # noqa: BLE001
        log.info("edit_message_text fail: %s", exc)


async def _title_for(context: ContextTypes.DEFAULT_TYPE, anime_id: int) -> str:
    """Title cache/AniList se — card banaye bina (fast)."""
    agg = _aggregator(context)
    entry = agg.anilist.cached_entry(anime_id)
    if entry:
        return entry.best_title
    try:
        entry = await agg.anilist.get(anime_id)
    except ConnectionError:
        entry = None
    return entry.best_title if entry else f"#{anime_id}"


async def _card_from_cache_or_build(agg: Aggregator, anime_id: int) -> CardData | None:
    cached = None
    if agg.cache is not None:
        cached = agg.cache.get_card(anime_id)
    if cached:
        import json

        try:
            return CardData.from_dict(json.loads(cached))
        except Exception:  # noqa: BLE001
            pass
    try:
        return await asyncio.wait_for(agg.build_card(anime_id), timeout=config.CARD_BUILD_TIMEOUT)
    except Exception:  # noqa: BLE001
        log.exception("card build fail (%s)", anime_id)
        return None


# ---------------------------------------------------------------------------
# error handler
# ---------------------------------------------------------------------------
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler: log + user ko message."""
    log.error("Update %s par exception", update, exc_info=context.error)
    chat_id = None
    if isinstance(update, Update):
        if update.effective_chat:
            chat_id = update.effective_chat.id
        elif update.callback_query and update.callback_query.message:
            chat_id = update.callback_query.message.chat_id
    if chat_id:
        try:
            await context.bot.send_message(chat_id=chat_id, text=texts.ERROR_GENERIC)
        except Exception:  # noqa: BLE001
            log.warning("error message bhi nahi bhej paye")
    for admin in config.ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin, text=f"⚠️ Error: {context.error}")
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------
def register(app: Application) -> None:
    app.add_handler(CommandHandler(["start"], cmd_start))
    app.add_handler(CommandHandler(["help"], cmd_help))
    app.add_handler(CommandHandler(["version"], cmd_version))
    app.add_handler(CommandHandler(["search", "anime", "s"], cmd_search))
    app.add_handler(CommandHandler(["myfollows", "follows"], cmd_myfollows))
    app.add_handler(CommandHandler(["setep"], cmd_setep))
    app.add_handler(CommandHandler(["stats"], cmd_stats))

    app.add_handler(CallbackQueryHandler(cb_pick, pattern=f"^{CB_PICK}:"))
    app.add_handler(CallbackQueryHandler(cb_follow, pattern=f"^{CB_FOLLOW}:"))
    app.add_handler(CallbackQueryHandler(cb_unfollow, pattern=f"^{CB_UNFOLLOW}:"))
    app.add_handler(CallbackQueryHandler(cb_lang, pattern=f"^{CB_LANG}:"))
    app.add_handler(CallbackQueryHandler(cb_confirm, pattern=f"^{CB_CONFIRM}:"))
    app.add_handler(CallbackQueryHandler(cb_cancel, pattern=f"^{CB_CANCEL}:"))
    app.add_handler(CallbackQueryHandler(cb_refresh, pattern=f"^{CB_REFRESH}:"))
    app.add_handler(CallbackQueryHandler(cb_close, pattern=f"^{CB_CLOSE}"))

    # command ke alawa plain text bhi search maanta hai
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
