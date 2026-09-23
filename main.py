"""
main.py — FastAPI (health endpoints) + PTB bot (long polling) + notifier loop.

Render free tier par ye ek hi process hai:
    web service :$PORT  ->  /healthz (GET + HEAD) UptimeRobot har 5 min ping karta hai
    bot polling        ->  background task, graceful shutdown + controlled retry

LIFECYCLE (v1.3 — shutdown fix):
    PTB ka `async with application` sirf initialize()/shutdown() karta hai —
    stop() KABHI nahi. Isliye ORDER ye khud maintain karte hain:
        initialize (async with) -> start -> start_polling
        -> STOP_EVENT -> updater.stop() -> application.stop()
        -> async with exit (shutdown) -> clean exit, NO RuntimeError
    CancelledError = shutdown signal, retry BILKUL nahi.
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
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

import config
import handlers
from database import get_db
from notifier import Notifier
from sources.aggregator import Aggregator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
# httpx har getUpdates ko INFO me URL (jisme BOT_TOKEN hota hai) print karta tha —
# na token leak ho, na har-second log spam. Warning se upar kuch nahi.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

log = logging.getLogger("main")

STARTED_AT = time.time()
# Python 3.10+ me Event() constructor loop-bound nahi hota — module level safe hai.
STOP_EVENT: asyncio.Event = asyncio.Event()

state: dict = {
    "bot": None,
    "bot_running": False,
    "aggregator": None,
    "notifier": None,
    "notifier_task": None,
    "last_error": None,
}


# ---------------------------------------------------------------------------
# bot lifecycle (graceful shutdown + controlled retry)
# ---------------------------------------------------------------------------
async def _sleep_or_stop(seconds: float) -> None:
    """Retry backoff sleep — par STOP_EVENT aaye to turant uth jaao (shutdown me mat so)."""
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(STOP_EVENT.wait(), timeout=seconds)


async def _ordered_stop(application) -> None:
    """
    PTB ka sahi shutdown ORDER (khud, cancel ke bina):
        updater.stop() -> application.stop()  -> (async with exit) shutdown()
    `stop()` ke bina `shutdown()` RuntimeError("still running") deta hai —
    yahi pehle crash ka root cause tha.
    """
    with contextlib.suppress(Exception):
        if application.updater is not None and application.updater.running:
            await application.updater.stop()
    with contextlib.suppress(Exception):
        if application.running:
            await application.stop()


async def run_bot_forever() -> None:
    """
    Auto-retry loop: network timeout/Conflict par controlled retry,
    graceful shutdown (STOP_EVENT ya CancelledError) par CLEAN exit — koi retry nahi.
    """
    if not config.BOT_TOKEN:
        log.warning("BOT_TOKEN set nahi hai — sirf web service chalegi (health endpoints up hain).")
        state["last_error"] = "BOT_TOKEN missing"
        return

    # PTB import yahan — taaki bina token ke bhi web service chale
    from telegram import Update
    from telegram.error import Conflict, InvalidToken, TelegramError
    from telegram.ext import ApplicationBuilder

    backoff = 10
    log.info("bot starting (poll interval 1s, drop_pending_updates=True)")

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

            # async with = initialize() ... shutdown(). stop() hum khud karte hain.
            async with application:
                await application.start()
                try:
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
                    log.info("bot started: @%s (polling)", me.username)

                    # notifier ko bot mil gaya. DHOKE-baazi guard: retry ke baad
                    # doosra notifier loop task bana to double-polling ho jaati.
                    nt = state.get("notifier_task")
                    if state["notifier"] is not None and (nt is None or nt.done()):
                        state["notifier"].bot = application.bot
                        state["notifier_task"] = asyncio.create_task(state["notifier"].loop())

                    # STOP_EVENT par clean wake-up (cancel ki zaroorat nahi)
                    await STOP_EVENT.wait()
                    log.info("graceful shutdown requested — polling band kar raha hoon")
                finally:
                    # CancelledError me BHI ye chalega -> app hamesha 'stopped' hoke
                    # shutdown me jaata hai -> "still running" kabhi nahi
                    await _ordered_stop(application)
            # async with exit ne shutdown() kar diya (running=False) — safe
            log.info("bot stopped")
        except asyncio.CancelledError:
            # SHUTDOWN signal hai, error nahi — retry BILKUL nahi
            log.info("bot stopped (shutdown cancel)")
            if application is not None:
                await _ordered_stop(application)
            return
        except Conflict:
            # Do instance ek hi token par chal rahe hain (purana Render/local)
            state["last_error"] = "Conflict: doosra getUpdates instance"
            log.error(
                "Telegram Conflict — do instance ek hi token par hain. "
                "Purana band karo (duplicate service/local process). 60s baad retry."
            )
            await _sleep_or_stop(60)
            if not STOP_EVENT.is_set():
                log.info("retrying (Conflict backoff)")
        except InvalidToken:
            state["last_error"] = "InvalidToken: BOT_TOKEN reject hua"
            log.error("BOT_TOKEN galat hai (logs me token kabhi nahi print karte). 5 min baad retry.")
            await _sleep_or_stop(300)
            if not STOP_EVENT.is_set():
                log.info("retrying (InvalidToken backoff)")
        except TelegramError as exc:
            state["last_error"] = f"TelegramError: {exc}"
            log.warning("Telegram error: %s — %ss baad retrying", exc, backoff)
            await _sleep_or_stop(backoff)
            if not STOP_EVENT.is_set():
                log.info("retrying (TelegramError backoff)")
        except Exception as exc:  # noqa: BLE001 - unexpected error par bhi bot marega nahi
            state["last_error"] = f"{type(exc).__name__}: {exc}"
            log.exception("unexpected error — %ss baad retrying", backoff)
            await _sleep_or_stop(backoff)
            if not STOP_EVENT.is_set():
                log.info("retrying (unexpected error backoff)")
        finally:
            state["bot_running"] = False
            backoff = min(backoff * 2, 120)

    log.info("bot stopped (stop event)")


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

    log.info("Anime Dub Bot %s start — poll har %d min, db=%s", config.VERSION, config.POLL_MINUTES, db.path)
    task = asyncio.create_task(run_bot_forever())
    try:
        yield
    finally:
        # 1) bot ko BINA cancel ke clean stop ka mauka do (1-3s me nikal aata hai)
        STOP_EVENT.set()
        try:
            await asyncio.wait_for(task, timeout=12.0)
        except asyncio.TimeoutError:
            log.warning("bot 12s me stop nahi hua — force cancel")
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        except asyncio.CancelledError:
            # lifespan task khud cancel hua (hard shutdown) — best-effort cleanup
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        # 2) notifier loop band
        nt = state.get("notifier_task")
        if nt is not None:
            nt.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await nt
        # 3) network clients band
        with contextlib.suppress(Exception):
            await aggregator.anilist.aclose()
        with contextlib.suppress(Exception):
            await aggregator.yt.aclose()
        log.info("shutdown complete")


app = FastAPI(title="Anime Dub Bot", version=config.VERSION_LONG, lifespan=lifespan)


# ---------------------------------------------------------------------------
# Health endpoints — GET + HEAD dono (UptimeRobot HEAD bhejta hai; FastAPI ke
# @app.get sirf GET register karta hai -> HEAD 405 -> monitor "Down" dikhta tha).
# HEAD par khaali 200 body ke saath — lightweight, DB/bot ko touch bhi nahi karte.
# ---------------------------------------------------------------------------
@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def root(request: Request):
    if request.method == "HEAD":
        return Response(status_code=200)
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Anime Dub Bot</title>
<style>body{{font-family:system-ui,sans-serif;background:#0f1115;color:#e6e6e6;padding:40px;line-height:1.6}}
code{{background:#1b1f27;padding:2px 6px;border-radius:4px}} a{{color:#7cc4ff}}</style></head>
<body>
<h1>🎬 Anime Dub Bot — {config.VERSION}</h1>
<p>Hindi anime dub tracker. Ye web service sirf health endpoints ke liye hai
(Render free tier ko awake rakhne ke liye). Bot Telegram par chalta hai.</p>
<p><a href="/healthz">/healthz</a> &middot; <a href="/stats">/stats</a></p>
<p>UptimeRobot / cron-job.org se <code>/healthz</code> ko har 5 minute ping karo (GET ya HEAD).</p>
</body></html>"""
    )


@app.api_route("/healthz", methods=["GET", "HEAD"], include_in_schema=False)
async def healthz(request: Request):
    if request.method == "HEAD":
        return Response(status_code=200)
    # Bot polling se BILKUL independent — sirf process + local SQLite (fail-safe)
    follows = None
    try:
        follows = get_db().follow_count()
    except Exception:  # noqa: BLE001 - db down ho to bhi process alive report karo
        pass
    payload = {
        "status": "ok",
        "version": config.VERSION,
        "version_long": config.VERSION_LONG,
        "bot_running": bool(state["bot_running"]),
        "bot_configured": bool(config.BOT_TOKEN),
        "poll_minutes": config.POLL_MINUTES,
        "follows": follows,
        "uptime_seconds": int(time.time() - STARTED_AT),
        "ist_now": config.ts_ist(config.now_ist()),
        "last_error": state["last_error"],
    }
    return JSONResponse(payload)


@app.api_route("/stats", methods=["GET", "HEAD"], include_in_schema=False)
async def stats(request: Request):
    if request.method == "HEAD":
        return Response(status_code=200)
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
    """Render: `python main.py` -> uvicorn 0.0.0.0:$PORT (start command unchanged)"""
    port = int(os.environ.get("PORT", config.PORT))
    log.info("Web service %s:%d par (healthCheckPath=%s)", config.HOST, port, config.HEALTH_PATH)
    uvicorn_config = uvicorn.Config(app, host=config.HOST, port=port, log_level="info", timeout_graceful_shutdown=20)
    server = uvicorn.Server(uvicorn_config)

    # Render SIGTERM bhejta hai (deploy/restart) -> pehle STOP_EVENT (bot clean stop),
    # phir uvicorn ka should_exit (lifespan finally bhi chalta hai)
    def _stop(*_args) -> None:
        STOP_EVENT.set()
        server.should_exit = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, ValueError):
            signal.signal(sig, _stop)

    server.run()


if __name__ == "__main__":
    main()
