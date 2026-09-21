"""Telegram handlers — commands, search, follow/unfollow buttons."""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          CallbackQueryHandler, ContextTypes, filters)

import config
import texts
import formatter
from sources import aggregator
from database import Database

ALL_LANGS = ["jp", "en", "hi"]


def card_keyboard(anilist_id: int, following: bool) -> InlineKeyboardMarkup:
    if following:
        row = [InlineKeyboardButton("❌ Unfollow", callback_data=f"unfollow:{anilist_id}")]
    else:
        row = [InlineKeyboardButton("✅ Follow", callback_data=f"follow:{anilist_id}")]
    row.append(InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh:{anilist_id}"))
    return InlineKeyboardMarkup([row])


def lang_keyboard(anilist_id: int, selected: set) -> InlineKeyboardMarkup:
    rows = []
    for code in ALL_LANGS:
        mark = "✅ " if code in selected else ""
        rows.append([InlineKeyboardButton(
            f"{mark}{texts.LANG_LABELS[code]}",
            callback_data=f"lang:{anilist_id}:{code}")])
    rows.append([InlineKeyboardButton("👍 Ho gaya — Follow kar do",
                                      callback_data=f"followdone:{anilist_id}")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data=f"cancel:{anilist_id}")])
    return InlineKeyboardMarkup(rows)


def _pending_key(user_id: int, anilist_id: int) -> tuple:
    return (user_id, anilist_id)


async def send_card(update_or_query, context, anilist_id: int, force=False):
    """Anime card + follow/unfollow buttons bhejo."""
    db: Database = context.bot_data["db"]
    user_id = update_or_query.effective_user.id
    try:
        info = await aggregator.get_anime_info(anilist_id, db=db, force=force)
        text = formatter.format_card(info)
        following = db.get_follow(user_id, anilist_id) is not None
        kb = card_keyboard(anilist_id, following)
        if isinstance(update_or_query, Update):
            await update_or_query.message.reply_text(text, reply_markup=kb)
        else:
            await update_or_query.edit_message_text(text, reply_markup=kb)
    except Exception as e:
        print(f"[send_card] error: {e}")
        if isinstance(update_or_query, Update) and update_or_query.message:
            await update_or_query.message.reply_text(texts.ERROR_MSG)


# ---------- Commands ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db: Database = context.bot_data["db"]
    db.upsert_user(update.effective_user.id, update.effective_user.first_name or "")
    await update.message.reply_text(
        texts.START, parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(texts.HELP, parse_mode=ParseMode.MARKDOWN)


async def cmd_myfollows(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db: Database = context.bot_data["db"]
    follows = db.list_follows(update.effective_user.id)
    if not follows:
        await update.message.reply_text(texts.NOT_FOLLOWING)
        return
    lines = ["📚 *Tumhare followed anime:*\n"]
    kb = []
    for f in follows:
        langs = ", ".join(texts.LANG_LABELS.get(l, l) for l in f["langs"])
        lines.append(f"🎬 *{f['title']}*\n   {langs}\n")
        kb.append([InlineKeyboardButton(
            f"❌ {f['title'][:40]}", callback_data=f"unfollow:{f['anilist_id']}")])
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(kb))


async def do_search(update: Update, context: ContextTypes.DEFAULT_TYPE,
                    force=False):
    query = " ".join(context.args) if context.args else None
    if not query:
        if update.message:
            await update.message.reply_text(
                "Naam do: /search Black Torch")
        return
    if update.message:
        await update.message.reply_text(texts.SEARCHING)
    try:
        results = await aggregator.search(query)
    except Exception as e:
        print(f"[search] {e}")
        results = []
    if not results:
        if update.message:
            await update.message.reply_text(texts.NOT_FOUND)
        return
    if len(results) == 1:
        await send_card(update, context, results[0]["anilist_id"], force=force)
        return
    # Multiple results — choose karne do
    kb = [[InlineKeyboardButton(
        f"{r['title']} ({r.get('year') or ''})"[:60],
        callback_data=f"pick:{r['anilist_id']}")]
        for r in results[:8]]
    if update.message:
        await update.message.reply_text(
            "Ye mile — ek chun lo:", reply_markup=InlineKeyboardMarkup(kb))


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await do_search(update, context, force=False)


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await do_search(update, context, force=True)


async def cmd_setep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual data correction: /setep <jp|en|hi> <count> <anime naam>"""
    args = context.args or []
    if len(args) < 3 or args[0].lower() not in ("jp", "en", "hi") \
            or not args[1].isdigit():
        await update.message.reply_text(texts.SETEP_USAGE)
        return
    lang = args[0].lower()
    count = int(args[1])
    if not (0 < count <= 5000):
        await update.message.reply_text("Episode count 1-5000 ke beech do 🙏")
        return
    name = " ".join(args[2:])
    context.user_data["setep"] = {"lang": lang, "count": count, "name": name}
    try:
        results = await aggregator.search(name)
    except Exception:
        results = []
    if not results:
        await update.message.reply_text(texts.SETEP_NOT_FOUND)
        return
    if len(results) == 1:
        db: Database = context.bot_data["db"]
        db.set_manual_fix(results[0]["anilist_id"], lang, count)
        await update.message.reply_text(
            texts.SETEP_CONFIRM.format(
                title=results[0]["title"],
                lang_label=texts.LANG_LABELS[lang], count=count),
            parse_mode=ParseMode.MARKDOWN)
        return
    kb = [[InlineKeyboardButton(
        f"{r['title']}",
        callback_data=f"setep:{r['anilist_id']}:{lang}:{count}")]
        for r in results[:8]]
    await update.message.reply_text(
        "Ye mile — kaunsa anime?", reply_markup=InlineKeyboardMarkup(kb))


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Plain text = search query."""
    update.message.text = update.message.text.strip()
    ctx_args = [update.message.text]
    context.args = ctx_args
    await do_search(update, context, force=False)


# ---------- Callbacks ----------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    parts = data.split(":")
    action = parts[0]
    db: Database = context.bot_data["db"]

    if action == "pick":
        await send_card(q, context, int(parts[1]))

    elif action == "setep":
        # setep:{anilist_id}:{lang}:{count}
        db.set_manual_fix(int(parts[1]), parts[2], int(parts[3]))
        try:
            info = await aggregator.get_anime_info(
                int(parts[1]), db=db, force=True)
            title = info["title"]
        except Exception:
            title = "Anime"
        await q.edit_message_text(
            texts.SETEP_CONFIRM.format(
                title=title, lang_label=texts.LANG_LABELS[parts[2]],
                count=int(parts[3])),
            parse_mode=ParseMode.MARKDOWN)

    elif action == "refresh":
        await send_card(q, context, int(parts[1]), force=True)

    elif action == "follow":
        anilist_id = int(parts[1])
        # Default: saari languages selected
        context.bot_data.setdefault("pending", {})[_pending_key(q.from_user.id, anilist_id)] = set(ALL_LANGS)
        await q.edit_message_reply_markup(reply_markup=lang_keyboard(anilist_id, set(ALL_LANGS)))
        await context.bot.send_message(
            q.from_user.id,
            "Kis audio ke notification chahiye? Ek ya ek se zyada select karo 👇")

    elif action == "lang":
        anilist_id, code = int(parts[1]), parts[2]
        pending = context.bot_data.setdefault("pending", {})
        sel = pending.get(_pending_key(q.from_user.id, anilist_id), set(ALL_LANGS))
        if code in sel:
            sel.discard(code)
        else:
            sel.add(code)
        pending[_pending_key(q.from_user.id, anilist_id)] = sel
        await q.edit_message_reply_markup(reply_markup=lang_keyboard(anilist_id, sel))

    elif action == "followdone":
        anilist_id = int(parts[1])
        sel = context.bot_data.setdefault("pending", {}).pop(
            _pending_key(q.from_user.id, anilist_id), set(ALL_LANGS)) or set(ALL_LANGS)
        info = await aggregator.get_anime_info(anilist_id, db=db, force=False)
        counts = aggregator.lang_counts(info)
        created = db.add_follow(
            q.from_user.id, anilist_id, info["title"], "",
            langs=sorted(sel), counts=counts)
        if created:
            langs_txt = ", ".join(texts.LANG_LABELS[l] for l in sorted(sel))
            await q.edit_message_text(
                texts.FOLLOW_DONE.format(title=info["title"], langs=langs_txt),
                parse_mode=ParseMode.MARKDOWN)
        else:
            await q.edit_message_text(
                texts.FOLLOW_ALREADY, parse_mode=ParseMode.MARKDOWN)

    elif action == "unfollow":
        anilist_id = int(parts[1])
        removed = db.remove_follow(q.from_user.id, anilist_id)
        if removed:
            await q.edit_message_text(
                texts.UNFOLLOW_DONE, parse_mode=ParseMode.MARKDOWN)
        else:
            await q.edit_message_text(texts.NOT_FOLLOWING_THIS)

    elif action == "cancel":
        await q.edit_message_text("Theek hai, cancel 🙌")


def register_handlers(app: Application, db: Database):
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("myfollows", cmd_myfollows))
    app.add_handler(CommandHandler("follows", cmd_myfollows))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("setep", cmd_setep))
    app.add_handler(CommandHandler("fixep", cmd_setep))
    app.add_handler(CommandHandler("my", cmd_myfollows))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_callback))
