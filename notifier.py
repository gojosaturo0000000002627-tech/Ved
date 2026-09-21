"""Notification engine — followed anime ke naye episodes check karta hai.

Har POLL_MINUTES mein:
  1. Sab follows uthata hai
  2. Har anime ki fresh info laata hai (force refresh)
  3. Agar kisi language mein episode count badha hai -> notification bhejta hai
  4. Release history record karta hai (next episode estimate ke liye)
"""
import asyncio
from datetime import datetime, timezone

import config
import formatter
import texts
from sources import aggregator


async def check_all(app):
    """Ek round: sab followed anime check karo + notify karo."""
    db = app.bot_data["db"]
    follows = db.all_follows()
    if not follows:
        return

    now_iso = datetime.now(timezone.utc).isoformat()

    # Ek hi anime dobara fetch na ho
    infos = {}
    for f in follows:
        aid = f["anilist_id"]
        if aid not in infos:
            try:
                infos[aid] = await aggregator.get_anime_info(
                    aid, title_hint=f["title"], force=True, db=db)
            except Exception as e:
                print(f"[notifier] info fetch fail {f['title']}: {e}")

    for f in follows:
        info = infos.get(f["anilist_id"])
        if not info:
            continue
        new_counts = aggregator.lang_counts(info)

        # Release history record karo — future next-episode estimates ke liye
        for lang, cnt in new_counts.items():
            if cnt and cnt > 0:
                try:
                    db.set_lang_state(f["anilist_id"], lang, cnt, now_iso)
                except Exception:
                    pass

        old_counts = f["last_counts"] or {}
        for lang in f["langs"]:
            old = old_counts.get(lang) or 0
            new = new_counts.get(lang) or 0
            if new > old:
                try:
                    msg = formatter.format_notification(
                        info, lang, new, info.get("total_episodes"))
                    # Watch button — seedha platform link khule
                    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                    url = formatter.notification_watch_url(info, lang)
                    reply = None
                    if url:
                        reply = InlineKeyboardMarkup([[InlineKeyboardButton(
                            "▶️ Watch", url=url)]])
                    await app.bot.send_message(
                        chat_id=f["user_id"], text=msg, parse_mode="HTML",
                        reply_markup=reply)
                except Exception as e:
                    print(f"[notifier] send fail {f['title']} {lang}: {e}")
        db.update_counts(f["user_id"], f["anilist_id"], new_counts)


async def notifier_loop(app):
    print(f"[notifier] started — har {config.POLL_MINUTES} minute check hoga")
    # Startup pe thoda ruk jao (bot pehle up aa jaaye)
    await asyncio.sleep(30)
    while True:
        try:
            await check_all(app)
        except Exception as e:
            print(f"[notifier] round failed: {e}")
        await asyncio.sleep(max(1, config.POLL_MINUTES) * 60)
