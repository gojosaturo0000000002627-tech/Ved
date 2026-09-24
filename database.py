"""
database.py — SQLite (data/bot.db): users / follows / cache / lang_state / manual_fix

Ek hi connection + RLock: bot ka load chhota hai aur saare queries local file par hain,
isliye sync sqlite3 hi sabse simple + safe hai.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

import config

log = logging.getLogger("database")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY,
    username   TEXT,
    first_seen INTEGER NOT NULL,
    last_seen  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS follows (
    user_id     INTEGER NOT NULL,
    anime_id    INTEGER NOT NULL,
    title       TEXT NOT NULL,
    langs       TEXT NOT NULL DEFAULT '["hi"]',
    jp_count    INTEGER,
    en_count    INTEGER,
    hi_count    INTEGER,
    total_eps   INTEGER,
    watch_url   TEXT,
    platform    TEXT,
    seen        INTEGER NOT NULL DEFAULT 1,   -- 0 = pehla poll pending (baseline lena hai)
    season_no   INTEGER,                      -- counts kis season ke hain
    updated_at  INTEGER NOT NULL,
    PRIMARY KEY (user_id, anime_id)
);

CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    expires_at INTEGER NOT NULL          -- 0 = kabhi expire nahi (YT handle success)
);

CREATE TABLE IF NOT EXISTS lang_state (
    user_id    INTEGER NOT NULL,
    anime_id   INTEGER NOT NULL,
    langs      TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, anime_id)
);

CREATE TABLE IF NOT EXISTS manual_fix (
    anime_id   INTEGER NOT NULL,
    lang       TEXT NOT NULL,            -- jp | en | hi
    count      INTEGER NOT NULL,
    note       TEXT,
    set_by     INTEGER,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (anime_id, lang)
);

CREATE INDEX IF NOT EXISTS idx_follows_anime ON follows(anime_id);
CREATE INDEX IF NOT EXISTS idx_cache_exp ON cache(expires_at);
"""

NEGATIVE = "__none__"      # YouTube negative-cache marker
LANGS = ("jp", "en", "hi")
LANG_LABEL = {"jp": "Japanese audio", "en": "English dub", "hi": "Hindi dub"}


class Database:
    def __init__(self, path: str | None = None):
        self.path = path or config.DB_PATH
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript("PRAGMA journal_mode=WAL;")
            self._conn.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """Purane DB me naye columns — duplicate column error ko chupchaap ignore karo."""
        for stmt in (
            "ALTER TABLE follows ADD COLUMN seen INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE follows ADD COLUMN season_no INTEGER",
        ):
            try:
                self._conn.execute(stmt)
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    log.warning("migrate skip: %s", exc)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ users
    def touch_user(self, user_id: int, username: str | None = None) -> None:
        now = int(time.time())
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO users(user_id, username, first_seen, last_seen) VALUES(?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     username=COALESCE(excluded.username, users.username),
                     last_seen=excluded.last_seen""",
                (user_id, username, now, now),
            )

    def count_users(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    # ---------------------------------------------------------------- follows
    def add_follow(
        self,
        user_id: int,
        anime_id: int,
        title: str,
        langs: list[str],
        jp_count: int | None = None,
        en_count: int | None = None,
        hi_count: int | None = None,
        total_eps: int | None = None,
        watch_url: str | None = None,
        platform: str | None = None,
        season_no: int | None = None,
    ) -> None:
        langs = [l for l in langs if l in LANGS] or ["hi"]
        # Counts follow-time par mil gaye = baseline set. Sab NULL = pehla poll baseline lega.
        seen = 1 if (jp_count is not None or en_count is not None or hi_count is not None) else 0
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO follows(user_id, anime_id, title, langs, jp_count, en_count, hi_count,
                                       total_eps, watch_url, platform, seen, season_no, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(user_id, anime_id) DO UPDATE SET
                     title=excluded.title, langs=excluded.langs,
                     jp_count=excluded.jp_count, en_count=excluded.en_count, hi_count=excluded.hi_count,
                     total_eps=excluded.total_eps, watch_url=excluded.watch_url,
                     platform=excluded.platform, seen=excluded.seen,
                     season_no=excluded.season_no, updated_at=excluded.updated_at""",
                (user_id, anime_id, title, json.dumps(langs), jp_count, en_count, hi_count,
                 total_eps, watch_url, platform, seen, season_no, int(time.time())),
            )

    def remove_follow(self, user_id: int, anime_id: int) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM follows WHERE user_id=? AND anime_id=?", (user_id, anime_id))
            self._conn.execute("DELETE FROM lang_state WHERE user_id=? AND anime_id=?", (user_id, anime_id))
            return cur.rowcount > 0

    def get_follow(self, user_id: int, anime_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM follows WHERE user_id=? AND anime_id=?", (user_id, anime_id)
            ).fetchone()
        return self._row_to_follow(row) if row else None

    def get_follows(self, user_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM follows WHERE user_id=? ORDER BY updated_at DESC", (user_id,)
            ).fetchall()
        return [self._row_to_follow(r) for r in rows]

    def all_follows(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM follows ORDER BY anime_id").fetchall()
        return [self._row_to_follow(r) for r in rows]

    def followed_anime_ids(self) -> list[int]:
        with self._lock:
            rows = self._conn.execute("SELECT DISTINCT anime_id FROM follows").fetchall()
        return [int(r[0]) for r in rows]

    def follow_count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM follows").fetchone()[0])

    def users_for_anime(self, anime_id: int, lang: str | None = None) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM follows WHERE anime_id=?", (anime_id,)).fetchall()
        out = [self._row_to_follow(r) for r in rows]
        if lang:
            out = [f for f in out if lang in f["langs"]]
        return out

    def update_counts(
        self,
        user_id: int,
        anime_id: int,
        jp_count: int | None = None,
        en_count: int | None = None,
        hi_count: int | None = None,
        total_eps: int | None = None,
        watch_url: str | None = None,
        platform: str | None = None,
        season_no: int | None = None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """UPDATE follows SET jp_count=?, en_count=?, hi_count=?, total_eps=?,
                          watch_url=COALESCE(?, watch_url), platform=COALESCE(?, platform),
                          seen=1, season_no=COALESCE(?, season_no),
                          updated_at=?
                   WHERE user_id=? AND anime_id=?""",
                (jp_count, en_count, hi_count, total_eps, watch_url, platform,
                 season_no, int(time.time()), user_id, anime_id),
            )

    @staticmethod
    def _row_to_follow(row: sqlite3.Row) -> dict:
        try:
            langs = json.loads(row["langs"] or "[]")
        except json.JSONDecodeError:
            langs = []
        return {
            "user_id": int(row["user_id"]),
            "anime_id": int(row["anime_id"]),
            "title": row["title"],
            "langs": langs,
            "jp_count": row["jp_count"],
            "en_count": row["en_count"],
            "hi_count": row["hi_count"],
            "total_eps": row["total_eps"],
            "watch_url": row["watch_url"],
            "platform": row["platform"],
            "seen": bool(row["seen"]),
            "season_no": row["season_no"],
            "updated_at": int(row["updated_at"] or 0),
        }

    # ------------------------------------------------------------- lang_state
    def set_lang_state(self, user_id: int, anime_id: int, langs: list[str]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO lang_state(user_id, anime_id, langs, updated_at) VALUES(?,?,?,?)
                   ON CONFLICT(user_id, anime_id) DO UPDATE SET langs=excluded.langs, updated_at=excluded.updated_at""",
                (user_id, anime_id, json.dumps(langs), int(time.time())),
            )

    def get_lang_state(self, user_id: int, anime_id: int) -> list[str] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT langs FROM lang_state WHERE user_id=? AND anime_id=?", (user_id, anime_id)
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0] or "[]")
        except json.JSONDecodeError:
            return []

    def clear_lang_state(self, user_id: int, anime_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM lang_state WHERE user_id=? AND anime_id=?", (user_id, anime_id))

    # ------------------------------------------------------------------ cache
    def cache_get(self, key: str) -> str | None:
        """Value ya None. Negative entry ('__none__') ke liye bhi None aata hai —
        wo skip karna hai ya nahi, wo expired_negative() batata hai."""
        now = int(time.time())
        with self._lock:
            row = self._conn.execute("SELECT value, expires_at FROM cache WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        if row["expires_at"] and row["expires_at"] < now:
            with self._lock, self._conn:
                self._conn.execute("DELETE FROM cache WHERE key=?", (key,))
            return None
        if row["value"] == NEGATIVE:
            return None
        return row["value"]

    def cache_set(self, key: str, value: str | None, ttl: int | None = None) -> None:
        expires = 0 if ttl is None else int(time.time()) + int(ttl)
        value = NEGATIVE if value is None else value
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO cache(key, value, expires_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, expires_at=excluded.expires_at",
                (key, value, expires),
            )

    def expired_negative(self, key: str) -> bool:
        """YouTube negative-cache: entry hai aur abhi valid hai -> True (skip karo)."""
        now = int(time.time())
        with self._lock:
            row = self._conn.execute("SELECT value, expires_at FROM cache WHERE key=?", (key,)).fetchone()
        if not row:
            return False
        if row["value"] != "__none__":
            return False
        return bool(row["expires_at"]) and row["expires_at"] >= now

    def prune_cache(self) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM cache WHERE expires_at>0 AND expires_at<?", (int(time.time()),))
            return cur.rowcount

    # ------------------------------------------------------------- card cache
    def get_card(self, anime_id: int) -> str | None:
        return self.cache_get(f"card:{anime_id}")

    def set_card(self, anime_id: int, payload: str) -> None:
        self.cache_set(f"card:{anime_id}", payload, config.CARD_CACHE_TTL)

    # ----------------------------------------------------------- manual_fix
    def set_manual_fix(self, anime_id: int, lang: str, count: int, note: str = "", set_by: int | None = None) -> None:
        if lang not in LANGS:
            raise ValueError(f"lang {lang} galat hai (jp|en|hi)")
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO manual_fix(anime_id, lang, count, note, set_by, updated_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(anime_id, lang) DO UPDATE SET count=excluded.count, note=excluded.note,
                     set_by=excluded.set_by, updated_at=excluded.updated_at""",
                (anime_id, lang, int(count), note, set_by, int(time.time())),
            )

    def delete_manual_fix(self, anime_id: int, lang: str | None = None) -> None:
        with self._lock, self._conn:
            if lang:
                self._conn.execute("DELETE FROM manual_fix WHERE anime_id=? AND lang=?", (anime_id, lang))
            else:
                self._conn.execute("DELETE FROM manual_fix WHERE anime_id=?", (anime_id,))

    def get_manual_fix(self, anime_id: int) -> dict:
        with self._lock:
            rows = self._conn.execute("SELECT lang, count FROM manual_fix WHERE anime_id=?", (anime_id,)).fetchall()
        return {r["lang"]: int(r["count"]) for r in rows}

    def all_manual_fixes(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM manual_fix ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ stats
    def stats(self) -> dict:
        return {
            "follows": self.follow_count(),
            "users": self.count_users(),
            "followed_anime": len(self.followed_anime_ids()),
            "manual_fixes": len(self.all_manual_fixes()),
        }


_db: Database | None = None


def get_db() -> Database:
    """Lazy singleton — tests apna path dekar khud Database bana sakte hain."""
    global _db
    if _db is None:
        _db = Database()
    return _db


def set_db(instance: Database | None) -> None:
    """Test/DI ke liye singleton replace karo."""
    global _db
    _db = instance
