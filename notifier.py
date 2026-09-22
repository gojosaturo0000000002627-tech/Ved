"""
notifier.py — notification engine.

Har POLL_MINUTES (default 20) minute me:
  1. saare followed anime ko FORCE refresh karo (cache bypass)
  2. jis bhi language ka count badha ho -> notification bhejo
  3. naya count DB me save karo

Top-level counts CURRENT season ke hote hain (aggregator ka rule), isliye
nayi season ke episodes bhi notification trigger karte hain.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import config
import formatter
import texts
from database import LANG_LABEL, Database, get_db
from sources.aggregator import Aggregator, CardData

log = logging.getLogger("notifier")


class Notifier:
    def __init__(self, aggregator: Aggregator, db: Database | None = None, bot=None):
        self.aggregator = aggregator
        self.db = db or get_db()
        self.bot = bot
        self.last_run: datetime | None = None
        self.sent_total = 0
        self.failed_total = 0

    # ------------------------------------------------------------------ loop
    async def loop(self) -> None:
        """Forever loop — exception aaye to bhi mar-na nahi."""
        interval = max(60, config.POLL_MINUTES * 60)
        log.info("Notifier shuru: har %d minute", config.POLL_MINUTES)
        while True:
            try:
                await self.run_once()
            except Exception:  # noqa: BLE001
                log.exception("notifier run fail")
            await asyncio.sleep(interval)

    # -------------------------------------------------------------- one pass
    async def run_once(self, force: bool = True) -> list[dict]:
        """Ek pura pass. Return: bheje gaye notifications (tests isi ko assert karte hain)."""
        follows = self.db.all_follows()
        sent: list[dict] = []
        if not follows:
            self.last_run = config.now_ist()
            return sent

        # anime-wise group karo taaki ek anime ka card ek hi baar bane
        by_anime: dict[int, list[dict]] = {}
        for f in follows:
            by_anime.setdefault(f["anime_id"], []).append(f)

        for anime_id, rows in by_anime.items():
            try:
                data = await asyncio.wait_for(
                    self.aggregator.build_card(anime_id, force=force),
                    timeout=config.CARD_BUILD_TIMEOUT,
                )
            except asyncio.TimeoutError:
                log.warning("notifier: card build timeout (%s)", anime_id)
                continue
            except Exception:  # noqa: BLE001
                log.exception("notifier: card build fail (%s)", anime_id)
                continue
            if data is None:
                continue
            for row in rows:
                sent.extend(await self._process_row(row, data))

        self.last_run = config.now_ist()
        return sent

    # ------------------------------------------------------------- per follow
    async def _process_row(self, row: dict, data: CardData) -> list[dict]:
        sent: list[dict] = []
        counts = {"jp": data.jp, "en": data.en, "hi": data.hi}
        new_counts = {
            "jp": counts["jp"],
            "en": counts["en"],
            "hi": counts["hi"],
        }
        for lang in row["langs"]:
            if lang not in ("jp", "en", "hi"):
                continue
            new = counts[lang]
            old = row.get(f"{lang}_count")
            if new is None:
                continue  # data nahi hai -> guess nahi karenge
            if old is None:
                # pehli baar baseline set ho raha hai, notification nahi
                continue
            if new <= old:
                continue
            episode = new
            platform = self._platform_for(lang, data)
            text = formatter.format_notification(
                title=data.title,
                episode=episode,
                lang_label=LANG_LABEL[lang],
                platform=platform,
                count=new,
                total=data.planned_total,
                when=config.now_ist(),
            )
            ok = await self._send(row["user_id"], text, data, lang, platform)
            if ok:
                sent.append(
                    {
                        "user_id": row["user_id"],
                        "anime_id": data.anime_id,
                        "lang": lang,
                        "episode": episode,
                        "text": text,
                        "platform": platform,
                    }
                )
        self.db.update_counts(
            user_id=row["user_id"],
            anime_id=data.anime_id,
            jp_count=new_counts["jp"],
            en_count=new_counts["en"],
            hi_count=new_counts["hi"],
            total_eps=data.planned_total,
            watch_url=data.watch_url,
            platform=(data.hi_platforms or [data.watch_platform] or [None])[0],
        )
        return sent

    @staticmethod
    def _platform_for(lang: str, data: CardData) -> str | None:
        """Sirf wahi platform dikhao jo us language ka dub actually deta hai."""
        if lang == "hi":
            return data.hi_platforms[0] if data.hi_platforms else None
        if lang == "en":
            return data.watch_platform
        return data.watch_platform or (data.streaming_platforms[0] if data.streaming_platforms else None)

    async def _send(self, user_id: int, text: str, data: CardData, lang: str, platform: str | None) -> bool:
        if self.bot is None:
            return False
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        kb = None
        url = data.watch_url
        if lang == "jp" and data.streaming_platforms:
            url = data.watch_url
        if url and platform:
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton(texts.BTN_WATCH.format(platform=platform)[:60], url=url)]]
            )
        try:
            await self.bot.send_message(chat_id=user_id, text=text, parse_mode="HTML", reply_markup=kb)
            self.sent_total += 1
            return True
        except Exception as exc:  # noqa: BLE001
            self.failed_total += 1
            log.warning(texts.NOTIFY_FAIL.format(user_id=user_id, error=exc))
            return False
