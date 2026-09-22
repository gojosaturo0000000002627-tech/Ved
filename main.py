"""
main.py — FastAPI (health endpoints) + PTB bot (long polling) + notifier loop.

Render free tier par ye ek hi process hai:
    web service :8080  ->  /healthz ko UptimeRobot har 5 min ping karta hai
    bot polling        ->  background task, auto-retry ke saath (kabhi marta nahi)
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

import config
import handlers
from database import get_db
from notifier import Notifier
from sources.aggregator import Aggregator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("main")

STARTED_AT = time.time()
STOP_EVENT: asyncio.Event | None = None

state: dict = {
    "bot": None,
    "bot_running": False,
    "aggregator": None,
    "notifier": None,
    "last_error": None,
}


# ---------------------------------------------------------------------------
# bot lifecycle (auto-retry)
# ---------------------------------------------------------------------------
async def run_bot_forever() -> None:
    """
    Auto-retry startup loop: network timeout, Conflict, kuch bhi ho —
    bot log karke dobara koshish karta rahega.
    """
    global STOP_EVENT
    STOP_EVENT = STOP_EVENT or asyncio.Event()

    if not config.BOT_TOKEN:
        log.warning("BOT_TOKEN set nahi hai — sirf web service chalega (health endpoints up hain).")
        state["last_error"] = "BOT_TOKEN missing"
        return

    # PTB import yahan karte hain taaki bina token ke bhi web service chale
    from telegram import Update
    from telegram.error import Conflict, InvalidToken, TelegramError
    from telegram.ext import ApplicationBuilder

    backoff = 10
    while not STOP_EVENT.is_set():
        application = None
        try:
            application = (
                ApplicationBuilder()
                .token(config.BOT_TOKEN)
                .concurrent_updates(True)
                .build()
            )
            handlers.register(application)
            application.bot_data["aggregator"] = state["aggregator"]
            application.bot_data["notifier"] = state["notifier"]

            async with application:
                await application.start()
                await application.updater.start_polling(
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=True,
                    poll_interval=1.0,
                    timeout=30,
                )
                me = await application.bot.get_me()
                state["bot"] = application.bot
                state["bot_running"] = True
                state["last_error"] = None
                backoff = 10
                log.info("Bot chal raha hai: @%s (polling)", me.username)

                # notifier ko bot mil gaya, ab wo bhi chalu
                if state["notifier"] is not None:
                    state["notifier"].bot = application.bot
                    asyncio.create_task(state["notifier"].loop())

                await STOP_EVENT.wait()
        except Conflict as exc:
            # Do instance ek hi token par chal rahe hain
            state["last_error"] = f"Conflict: {exc}"
            log.error(
                "Telegram Conflict — do instance ek hi token par chal rahe hain. "
                "Purana instance band karo (Render par duplicate service ya local process). 60s baad retry."
            )
            await asyncio.sleep(60)
        except InvalidToken as exc:
            state["last_error"] = f"InvalidToken: {exc}"
            log.error("BOT_TOKEN galat hai: %s", exc)
            await asyncio.sleep(300)
        except TelegramError as exc:
            state["last_error"] = f"TelegramError: {exc}"
            log.warning("Telegram error, %ss baad retry: %s", backoff, exc)
            await asyncio.sleep(backoff)
        except Exception as exc:  # noqa: BLE001
            state["last_error"] = f"{type(exc).__name__}: {exc}"
            log.exception("Bot startup fail, %ss baad retry", backoff)
            await asyncio.sleep(backoff)
        finally:
            state["bot_running"] = False
            if application is not None:
                with contextlib.suppress(Exception):
                    await application.stop()
            backoff = min(backoff * 2, 120)


# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    db = get_db()
    aggregator = Aggregator(cache=db)
    notifier = Notifier(aggregator=aggregator, db=db)
    state["aggregator"] = aggregator
    state["notifier"] = notifier

    task = asyncio.create_task(run_bot_forever())
    log.info("Anime Dub Bot %s start — poll har %d min, db=%s", config.VERSION, config.POLL_MINUTES, db.path)
    try:
        yield
    finally:
        if STOP_EVENT is not None:
            STOP_EVENT.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        with contextlib.suppress(Exception):
            await aggregator.anilist.aclose()
        with contextlib.suppress(Exception):
            await aggregator.yt.aclose()


app = FastAPI(title="Anime Dub Bot", version=config.VERSION_LONG, lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def root() -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Anime Dub Bot</title>
<style>body{{font-family:system-ui,sans-serif;background:#0f1115;color:#e6e6e6;padding:40px;line-height:1.6}}
code{{background:#1b1f27;padding:2px 6px;border-radius:4px}} a{{color:#7cc4ff}}</style></head>
<body>
<h1>🎬 Anime Dub Bot — {config.VERSION}</h1>
<p>Hindi anime dub tracker. Ye web service sirf health endpoints ke liye hai
(Render free tier ko awake rakhne ke liye). Bot Telegram par chalta hai.</p>
<p><a href="/healthz">/healthz</a> &middot; <a href="/stats">/stats</a></p>
<p>UptimeRobot / cron-job.org se <code>/healthz</code> ko har 5 minute ping karo.</p>
</body></html>"""


@app.get("/healthz")
async def healthz() -> JSONResponse:
    db = get_db()
    payload = {
        "status": "ok",
        "version": config.VERSION,
        "version_long": config.VERSION_LONG,
        "bot_running": bool(state["bot_running"]),
        "bot_configured": bool(config.BOT_TOKEN),
        "poll_minutes": config.POLL_MINUTES,
        "follows": db.follow_count(),
        "uptime_seconds": int(time.time() - STARTED_AT),
        "ist_now": config.ts_ist(config.now_ist()),
        "last_error": state["last_error"],
    }
    return JSONResponse(payload)


@app.get("/stats")
async def stats() -> JSONResponse:
    db = get_db()
    agg = state["aggregator"]
    payload = {
        "version": config.VERSION,
        "version_long": config.VERSION_LONG,
        "status": "ok",
        "follows": db.follow_count(),
        "users": db.count_users(),
        "followed_anime": len(db.followed_anime_ids()),
        "manual_fixes": len(db.all_manual_fixes()),
        "poll_minutes": config.POLL_MINUTES,
        "bot_running": bool(state["bot_running"]),
        "notifier_sent": state["notifier"].sent_total if state["notifier"] else 0,
        "notifier_failed": state["notifier"].failed_total if state["notifier"] else 0,
        "notifier_last_run": (
            config.ts_ist(state["notifier"].last_run) if state["notifier"] and state["notifier"].last_run else None
        ),
        "anilist_requests": agg.anilist.requests_made if agg else 0,
        "aninidhi_cached_records": agg.aninidhi.cached_count() if agg else 0,
        "ist_now": config.ts_ist(config.now_ist()),
    }
    return JSONResponse(payload)


def main() -> None:
    """Render: `python main.py` -> uvicorn 0.0.0.0:$PORT"""
    port = int(os.environ.get("PORT", config.PORT))
    log.info("Web service %s:%d par (healthCheckPath=%s)", config.HOST, port, config.HEALTH_PATH)
    uvicorn_config = uvicorn.Config(app, host=config.HOST, port=port, log_level="info", timeout_graceful_shutdown=15)
    server = uvicorn.Server(uvicorn_config)

    # graceful shutdown (Render deploy par SIGTERM aata hai)
    def _stop(*_args) -> None:
        if STOP_EVENT is not None:
            STOP_EVENT.set()
        server.should_exit = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, ValueError):
            signal.signal(sig, _stop)

    server.run()


if __name__ == "__main__":
    main()
