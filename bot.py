"""Anime Info Telegram Bot — multi-season cards, follow with language buttons,
near-real-time episode notifications (Telegram DM).

Commands:
  /anime <name>    -> full multi-season card (+ Follow button agar ongoing hai)
  /follow <name>    -> direct follow (language picker khulega, ongoing hone par)
  /unfollow <name>  -> watchlist se hatao
  /list             -> meri list (languages ke saath)
  /settings <name>  -> languages dobara chuno
  <any text>        -> /anime <text>

Env vars (Render me set karo):
  BOT_TOKEN      - @BotFather se mila token
  GITHUB_TOKEN   - (optional) watchlist GitHub pe save karne ke liye
  GITHUB_REPO    - (optional) e.g. "shinchan/anime-bot-data"
  CHECK_INTERVAL - (optional) seconds, default 900 (15 min)
"""

import asyncio
import logging
import os
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          MessageHandler, ContextTypes, filters)

import scraper
import storage

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("bot")

IST = ZoneInfo("Asia/Kolkata")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "900"))

LANG_LABEL = {"Japanese": "🇯🇵 Japanese (sub)", "Hindi": "🇮🇳 Hindi dub",
              "Tamil": "Tamil dub", "Telugu": "Telugu dub", "English": "English dub"}

WELCOME = (
    "🎬 Anime Info Bot\n\n"
    "Anime ki puri availability info — kaunsi language, kitne episodes "
    "(Hindi dub included), kaunsa season, aur agla episode kab.\n\n"
    "• Anime ka naam likho — full card aa jayega\n"
    "• /follow <name> — ongoing anime ke naye episodes ki notification 🔔\n"
    "• /list — followed anime\n"
    "• /unfollow <name> — notification band\n\n"
    "Try: grand blue"
)


# ---------------------------------------------------------------- keep-alive

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"bot running")

    def log_message(self, *args):
        pass


def start_web():
    port = int(os.environ.get("PORT", "8080"))
    try:
        HTTPServer(("0.0.0.0", port), _Handler).serve_forever()
    except Exception as e:  # pragma: no cover
        log.warning("keep-alive server failed: %s", e)


# ---------------------------------------------------------------- helpers

async def send_card(update_or_query, context, name):
    """Look up and send a card; attach Follow button if ongoing."""
    msg = update_or_query.message or update_or_query.effective_message
    await msg.chat.send_action(action="typing")
    try:
        card, meta = await asyncio.to_thread(scraper.get_card, name)
    except Exception:
        log.exception("card failed: %s", name)
        await msg.reply_text("Info laane me dikkat aayi. Thodi der baad try karo.")
        return

    keyboard = None
    if meta["ongoing"] and meta.get("follow_target"):
        t = meta["follow_target"]
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔔 Follow this anime",
                                   callback_data=f"fw|{t['sid']}")]])
        # remember this sid for the follow flow
        context.bot_data.setdefault("catalog", {})[t["sid"]] = {
            "title": t["title"], "url": t["url"], "base": meta["base"],
            "season": t["n"] if isinstance(t.get("n"), int) else t["show"]["season"],
            "show": t["show"], "languages": meta["languages"],
        }

    await msg.reply_text(card, reply_markup=keyboard, disable_web_page_preview=True)


def _open_language_picker(context, chat_id, sid):
    """Show multi-select language buttons for a pending follow."""
    info = context.bot_data.get("catalog", {}).get(sid)
    if not info:
        return None, None
    selected = context.user_data.setdefault("sel", {})
    cur = selected.get(sid, set())

    rows = []
    for lang in info["languages"]:
        mark = "✅ " if lang in cur else ""
        rows.append([InlineKeyboardButton(mark + LANG_LABEL.get(lang, lang),
                                          callback_data=f"lg|{sid}|{lang}")])
    rows.append([InlineKeyboardButton("✅ Done", callback_data=f"ok|{sid}"),
                 InlineKeyboardButton("❌ Cancel", callback_data=f"cl|{sid}")])
    return info, InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------- commands

async def cmd_start(update: Update, context):
    await update.message.reply_text(WELCOME)


async def cmd_anime(update: Update, context):
    name = " ".join(context.args).strip()
    if not name:
        await update.message.reply_text("Anime ka naam do. Jaise: /anime grand blue")
        return
    await send_card(update, context, name)


async def cmd_follow(update: Update, context):
    name = " ".join(context.args).strip()
    if not name:
        await update.message.reply_text("Naam do. Jaise: /follow grand blue")
        return
    await update.message.chat.send_action(action="typing")
    try:
        results = await asyncio.to_thread(scraper.search_rareanimes, name)
    except Exception:
        results = []
    if not results:
        # maybe finished hai, ya Hindi dub nahi hai — card try karo
        await update.message.reply_text(
            "Catalog me Hindi dub nahi mila. Card dekh lo — agar ongoing hai "
            "to Japanese ke liye follow kar sakte ho.")
        await send_card(update, context, name)
        return
    top = results[0]
    try:
        show = await asyncio.to_thread(scraper.parse_show, top["url"])
    except Exception:
        await update.message.reply_text("Add karne me dikkat aayi. Baad me try karo.")
        return
    if show["total"] and show["latest"] >= show["total"]:
        await update.message.reply_text("Ye season pura ho chuka hai — follow ki zarurat nahi 🙂\nInfo ke liye naam likho.")
        return
    context.bot_data.setdefault("catalog", {})[top["sid"]] = {
        "title": top["title"], "url": top["url"], "show": show,
        "languages": ["Hindi"] + [l for l in show["lang_counts"] if l != "Hindi"],
    }
    info, kb = _open_language_picker(context, update.effective_chat.id, top["sid"])
    if kb is None:
        await update.message.reply_text("Dikkat aayi, dobara try karo.")
        return
    await update.message.reply_text(
        f"🔔 {top['title']}\n\nKaunsi languages ke notifications chahiye? "
        "(ek se zyada bhi choose kar sakte ho)", reply_markup=kb)


async def cmd_unfollow(update: Update, context):
    name = " ".join(context.args).strip().lower()
    data = storage.load()
    chat_id = str(update.effective_chat.id)
    entries = data.get("users", {}).get(chat_id, [])
    if not entries:
        await update.message.reply_text("List khaali hai — kuch follow hi nahi kiya.")
        return
    if not name:
        await update.message.reply_text("Kaunsa hatana hai? /list se naam dekho.")
        return
    remaining, removed = [], None
    for e in entries:
        if not removed and name in e["title"].lower():
            removed = e["title"]
        else:
            remaining.append(e)
    if not removed:
        await update.message.reply_text("Naam match nahi hua. /list se exact naam dekho.")
        return
    data["users"][chat_id] = remaining
    storage.save(data)
    await update.message.reply_text(f"❌ Unfollow: {removed}")


async def cmd_list(update: Update, context):
    data = storage.load()
    entries = data.get("users", {}).get(str(update.effective_chat.id), [])
    if not entries:
        await update.message.reply_text("Abhi kuch follow nahi kiya. /follow <name> se shuru karo.")
        return
    lines = ["📋 Meri list:\n"]
    for e in entries:
        langs = ", ".join(e.get("langs", {}).keys()) or "—"
        lines.append(f"• {e['title']}\n   Languages: {langs}")
    lines.append("\n/unfollow <name> — hatao\n/settings <name> — languages badlo")
    await update.message.reply_text("\n".join(lines))


async def cmd_settings(update: Update, context):
    name = " ".join(context.args).strip().lower()
    data = storage.load()
    chat_id = str(update.effective_chat.id)
    for e in data.get("users", {}).get(chat_id, []):
        if name and name in e["title"].lower():
            context.bot_data.setdefault("catalog", {})[e["sid"]] = {
                "title": e["title"], "url": e["url"], "show": None,
                "languages": list(e.get("langs", {}).keys()) or ["Hindi"],
            }
            info, kb = _open_language_picker(context, chat_id, e["sid"])
            if kb:
                await update.message.reply_text(
                    f"⚙️ {e['title']}\nLanguages dobara chuno:", reply_markup=kb)
                return
    await update.message.reply_text("Naam match nahi hua. /list se dekho.")


async def on_text(update: Update, context):
    await send_card(update, context, update.message.text.strip())


# ---------------------------------------------------------------- callbacks (buttons)

async def on_callback(update: Update, context):
    q = update.callback_query
    await q.answer()
    try:
        action, sid, *rest = q.data.split("|")
    except ValueError:
        return
    catalog = context.bot_data.setdefault("catalog", {})
    chat_id = str(q.effective_chat.id)

    if action == "fw":  # Follow button (card se aaya)
        info = catalog.get(sid)
        if not info:
            await q.message.reply_text("Thodi der pehle ka card tha — dobara search karke try karo.")
            return
        # current language state as pre-selection: none
        context.user_data.setdefault("sel", {})[sid] = set()
        _, kb = _open_language_picker(context, chat_id, sid)
        if kb:
            await q.message.reply_text(
                f"🔔 {info['title']}\n\nKaunsi languages ke notifications chahiye?",
                reply_markup=kb)

    elif action == "lg":  # language toggle
        lang = rest[0] if rest else ""
        sel = context.user_data.setdefault("sel", {}).setdefault(sid, set())
        if lang in sel:
            sel.discard(lang)
        else:
            sel.add(lang)
        info, kb = _open_language_picker(context, chat_id, sid)
        if kb:
            await q.edit_message_reply_markup(reply_markup=kb)

    elif action == "cl":  # cancel
        context.user_data.get("sel", {}).pop(sid, None)
        try:
            await q.edit_message_text("❌ Cancel ho gaya")
        except Exception:
            pass

    elif action == "ok":  # done -> save
        info = catalog.get(sid)
        sel = context.user_data.setdefault("sel", {}).get(sid, set())
        if not info:
            return
        if not sel:
            await q.message.reply_text("Kam se kam ek language choose karo 🙂")
            return

        show = info.get("show")
        if not show:
            try:
                show = await asyncio.to_thread(scraper.parse_show, info["url"])
            except Exception:
                show = None

        data = storage.load()
        entries = data.setdefault("users", {}).setdefault(chat_id, [])
        entry = next((e for e in entries if e.get("sid") == sid or e["url"] == info["url"]), None)
        if entry is None:
            entry = {"sid": sid, "title": info["title"], "url": info["url"],
                     "total": (show or {}).get("total"), "langs": {}}
            entries.append(entry)

        for lang in sel:
            if lang == "Japanese":
                jp = await asyncio.to_thread(scraper.jikan_search, info["title"])
                if jp:
                    entry["mal_id"] = jp.get("mal_id")
                    cnt = (await asyncio.to_thread(
                        scraper.jikan_episode_count, jp["mal_id"])
                        if jp.get("mal_id") else None) or jp.get("aired") or 0
                else:
                    cnt = 0
            else:
                cnt = (show or {}).get("lang_counts", {}).get(lang, 0) or \
                      ((show or {}).get("latest", 0) if lang == "Hindi" else 0)
            entry["langs"][lang] = cnt
            if lang != "Japanese" and show and show.get("total"):
                entry["total"] = show["total"]

        storage.save(data)
        try:
            await q.edit_message_text(
                f"✅ {info['title']}\n🔔 Notifications ON for: {', '.join(sorted(sel))}\n\n"
                "Naya episode aate hi yahin DM milega. /list se dekho, "
                "/unfollow se band.")
        except Exception:
            pass
        context.user_data.get("sel", {}).pop(sid, None)


# ---------------------------------------------------------------- poller

async def _check_entry(context, chat_id, entry):
    """Check one followed anime for new episodes in selected languages."""
    notifications = []
    show = None
    if any(l != "Japanese" for l in entry.get("langs", {})):
        try:
            show = await asyncio.to_thread(scraper.parse_show, entry["url"])
        except Exception as ex:
            log.warning("parse failed %s: %s", entry["title"], ex)

    now_s = datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")

    for lang, last_seen in entry.get("langs", {}).items():
        current = None
        network = (show or {}).get("network") or "Crunchyroll"
        if lang == "Japanese":
            mal_id = entry.get("mal_id")
            if not mal_id:
                jp = await asyncio.to_thread(scraper.jikan_search, entry["title"])
                mal_id = (jp or {}).get("mal_id")
                if jp and not entry.get("mal_id"):
                    entry["mal_id"] = mal_id
            if not mal_id:
                continue
            current = await asyncio.to_thread(scraper.jikan_episode_count, mal_id)
            network = "Crunchyroll"
        elif show:
            if lang == "Hindi":
                current = show.get("latest") or show.get("lang_counts", {}).get("Hindi", 0)
            else:
                current = show.get("lang_counts", {}).get(lang, 0)
        if not current:
            continue

        if current > (last_seen or 0):
            for ep in range((last_seen or 0) + 1, current + 1):
                title = show["episodes"].get(ep, "") if show and lang != "Japanese" else ""
                notifications.append(
                    "🔔 NEW EPISODE OUT!\n"
                    f"🎬 {entry['title']}\n"
                    f"Episode {ep}" + (f": {title}" if title else "") + "\n"
                    f"Language: {LANG_LABEL.get(lang, lang)}\n"
                    f"Platform: {network}\n"
                    f"Released: {now_s}")
            entry["langs"][lang] = current

        # season-complete note
        if lang == "Hindi" and show and show.get("total") and current >= show["total"]:
            if not entry.get(f"done_hi"):
                entry["done_hi"] = True
                notifications.append(
                    f"🏁 {entry['title']}\nHindi dub season complete: {current}/{show['total']} "
                    "episodes. Ab naya episode nahi aayega.")
        if lang == "Japanese":
            jp = await asyncio.to_thread(scraper.jikan_search, entry["title"])
            if jp and jp.get("total") and current >= jp["total"] and not entry.get("done_jp"):
                entry["done_jp"] = True
                notifications.append(
                    f"🏁 {entry['title']}\nJapanese season complete: {current}/{jp['total']} "
                    "episodes. Next season aaye to /follow kar lena.")

    if show and show.get("total"):
        entry["total"] = show["total"]
    return notifications


async def check_job(context: ContextTypes.DEFAULT_TYPE):
    data = storage.load()
    changed, sent = False, 0
    for chat_id, entries in list(data.get("users", {}).items()):
        for e in entries:
            try:
                notes = await _check_entry(context, chat_id, e)
            except Exception:
                log.exception("check failed: %s", e.get("title"))
                continue
            if notes:
                changed = True
                for text in notes:
                    try:
                        await context.bot.send_message(chat_id=int(chat_id), text=text)
                        sent += 1
                    except Exception as ex:
                        log.warning("send failed %s: %s", chat_id, ex)
    if changed:
        storage.save(data)
    if sent:
        log.info("sent %d notifications", sent)


# ---------------------------------------------------------------- main

def main():
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN env var missing — Render me set karo.")

    threading.Thread(target=start_web, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("anime", cmd_anime))
    app.add_handler(CommandHandler("follow", cmd_follow))
    app.add_handler(CommandHandler("unfollow", cmd_unfollow))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_callback))

    if app.job_queue:
        app.job_queue.run_repeating(check_job, interval=CHECK_INTERVAL, first=20)
    else:
        log.error("job_queue missing — 'pip install python-telegram-bot[job-queue]'")

    log.info("Bot started — polling every %ss", CHECK_INTERVAL)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
