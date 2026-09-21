"""Anime Dub Bot — main entrypoint.

Ek hi process mein:
  1. FastAPI web service (Render health check ke liye) — / aur /healthz
  2. Telegram bot (long polling) — network error aaye to AUTO-RETRY
  3. Notification engine (episode checker loop)

Run: python main.py
"""
import asyncio
import os
import sys

import uvicorn
from fastapi import FastAPI
from telegram import Update
from telegram.ext import Application

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


def _get_db() -> Database:
    global _db
    if _db is None:
        _db = Database(config.DB_PATH)
    return _db


async def run_bot():
    """Telegram bot — kabhi mat ruko.

    Network timeout / koi bhi error aaye to cleanup karke retry karo.
    Sirf web service zinda rahega, bot khud wapas aa jayega.
    """
    if not config.BOT_TOKEN:
        print("FATAL: BOT_TOKEN set nahi hai! Render ke Environment dekho.")
        sys.exit(1)

    retry = 0
    while True:
        app = None
        notifier_task = None
        try:
            db = _get_db()
            app = (Application.builder()
                   .token(config.BOT_TOKEN)
                   .build())
            app.bot_data["db"] = db
            handlers.register_handlers(app, db)

            await app.initialize()
            await app.start()
            await app.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
            )
            print("[bot] Telegram polling started")
            retry = 0
            notifier_task = asyncio.create_task(notifier.notifier_loop(app))

            # Bot zinda hai? Periodic check
            while True:
                await asyncio.sleep(30)
                if not app.updater or not app.updater.running:
                    raise RuntimeError("Telegram polling band ho gaya")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            retry += 1
            wait = min(15 * retry, 120)
            print(f"[bot] ERROR: {e} — {wait}s baad retry "
                  f"(attempt {retry})")
            # Notifier bhi band karo (purana app se juda hai)
            if notifier_task and not notifier_task.done():
                notifier_task.cancel()
            # Saaf saafai
            try:
                if app:
                    if app.updater and app.updater.running:
                        await app.updater.stop()
                    if app.running:
                        await app.stop()
                    await app.shutdown()
            except Exception as cleanup_err:
                print(f"[bot] cleanup note: {cleanup_err}")
            await asyncio.sleep(wait)


async def run_web():
    uvicorn_config = uvicorn.Config(
        api, host="0.0.0.0", port=config.PORT, log_level="warning")
    server = uvicorn.Server(uvicorn_config)
    await server.serve()


async def main():
    # Web service — health check ke liye hamesha zinda
    web_task = asyncio.create_task(run_web())

    def _web_failed(task):
        exc = task.exception() if not task.cancelled() else None
        print(f"FATAL: web service crash: {exc}")
        os._exit(1)  # Render khud restart kar dega

    web_task.add_done_callback(_web_failed)

    # Bot — retry-loop ke saath (bot gir bhi jaye to web zinda rahega)
    asyncio.create_task(run_bot())

    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot band ho gaya.")
