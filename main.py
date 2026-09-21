"""Anime Dub Bot — main entrypoint.

Ek hi process mein:
  1. FastAPI web service (Render health check ke liye) — / aur /healthz
  2. Telegram bot (long polling)
  3. Notification engine (episode checker loop)

Run: python main.py
"""
import asyncio
import sys

import uvicorn
from fastapi import FastAPI
from telegram import Update
from telegram.ext import Application, ContextTypes

import config
import handlers
import notifier
from database import Database

api = FastAPI(title="Anime Dub Bot")
_db: Database | None = None


@api.get("/")
async def index():
    return {"bot": "Anime Dub Bot", "status": "running"}


@api.get("/healthz")
async def healthz():
    return {"status": "ok"}


@api.get("/stats")
async def stats():
    if _db is None:
        return {"status": "starting"}
    follows = _db.all_follows()
    users = len({f["user_id"] for f in follows})
    return {"status": "ok", "follows": len(follows), "users": users,
            "poll_minutes": config.POLL_MINUTES}


async def run_bot():
    global _db
    if not config.BOT_TOKEN:
        print("FATAL: BOT_TOKEN set nahi hai! .env ya Render env vars dekho.")
        sys.exit(1)

    _db = Database(config.DB_PATH)

    app = (Application.builder()
           .token(config.BOT_TOKEN)
           .build())
    app.bot_data["db"] = _db
    handlers.register_handlers(app, _db)

    await app.initialize()
    await app.start()
    await app.updater.start_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )
    print("[bot] Telegram polling started")

    # Notification engine bhi saath mein
    asyncio.create_task(notifier.notifier_loop(app))

    # Hamesha chalta rahe
    await asyncio.Event().wait()


async def run_web():
    uvicorn_config = uvicorn.Config(
        api, host="0.0.0.0", port=config.PORT, log_level="warning")
    server = uvicorn.Server(uvicorn_config)
    await server.serve()


async def main():
    web_task = asyncio.create_task(run_web())
    bot_task = asyncio.create_task(run_bot())
    done, pending = await asyncio.wait(
        [web_task, bot_task], return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    for t in done:
        exc = t.exception()
        if exc:
            print(f"FATAL: {exc}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot band ho gaya.")
