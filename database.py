"""SQLite database — users, follows aur API cache."""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self._init_tables()

    def _init_tables(self):
        with self.lock, self.conn:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id    INTEGER PRIMARY KEY,
                    first_name TEXT,
                    joined_at  TEXT
                );
                CREATE TABLE IF NOT EXISTS follows (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id      INTEGER NOT NULL,
                    anilist_id  INTEGER NOT NULL,
                    slug        TEXT,
                    title       TEXT NOT NULL,
                    langs       TEXT NOT NULL DEFAULT '["jp","en","hi"]',
                    last_counts TEXT NOT NULL DEFAULT '{}',
                    created_at  TEXT,
                    UNIQUE(user_id, anilist_id)
                );
                CREATE TABLE IF NOT EXISTS cache (
                    key        TEXT PRIMARY KEY,
                    payload    TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lang_state (
                    anilist_id  INTEGER NOT NULL,
                    lang        TEXT NOT NULL,
                    last_ep     INTEGER NOT NULL DEFAULT 0,
                    last_seen_at TEXT,
                    PRIMARY KEY(anilist_id, lang)
                );
                CREATE TABLE IF NOT EXISTS manual_fixes (
                    anilist_id  INTEGER NOT NULL,
                    lang        TEXT NOT NULL,
                    count       INTEGER NOT NULL,
                    updated_at  TEXT,
                    PRIMARY KEY(anilist_id, lang)
                );
                """
            )

    # ---------- users ----------
    def upsert_user(self, user_id: int, first_name: str):
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO users(user_id, first_name, joined_at) VALUES(?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET first_name=excluded.first_name",
                (user_id, first_name, utcnow_iso()),
            )

    # ---------- follows ----------
    def add_follow(self, user_id: int, anilist_id: int, title: str, slug: str,
                   langs: list, counts: dict) -> bool:
        """True return karta hai agar naya follow bana, False agar pehle se tha."""
        with self.lock, self.conn:
            cur = self.conn.execute(
                "SELECT 1 FROM follows WHERE user_id=? AND anilist_id=?",
                (user_id, anilist_id),
            )
            if cur.fetchone():
                return False
            self.conn.execute(
                "INSERT INTO follows(user_id, anilist_id, slug, title, langs, last_counts, created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (user_id, anilist_id, slug, title,
                 json.dumps(langs), json.dumps(counts), utcnow_iso()),
            )
            return True

    def remove_follow(self, user_id: int, anilist_id: int) -> bool:
        with self.lock, self.conn:
            cur = self.conn.execute(
                "DELETE FROM follows WHERE user_id=? AND anilist_id=?",
                (user_id, anilist_id),
            )
            return cur.rowcount > 0

    def get_follow(self, user_id: int, anilist_id: int):
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM follows WHERE user_id=? AND anilist_id=?",
                (user_id, anilist_id),
            ).fetchone()
        return self._follow_row(row)

    def list_follows(self, user_id: int):
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM follows WHERE user_id=? ORDER BY id DESC",
                (user_id,),
            ).fetchall()
        return [self._follow_row(r) for r in rows]

    def all_follows(self):
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM follows ORDER BY id"
            ).fetchall()
        return [self._follow_row(r) for r in rows]

    def update_counts(self, user_id: int, anilist_id: int, counts: dict):
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE follows SET last_counts=? WHERE user_id=? AND anilist_id=?",
                (json.dumps(counts), user_id, anilist_id),
            )

    @staticmethod
    def _follow_row(row):
        if row is None:
            return None
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "anilist_id": row["anilist_id"],
            "slug": row["slug"],
            "title": row["title"],
            "langs": json.loads(row["langs"]),
            "last_counts": json.loads(row["last_counts"]),
            "created_at": row["created_at"],
        }

    # ---------- cache ----------
    def cache_get(self, key: str, max_age: int):
        with self.lock:
            row = self.conn.execute(
                "SELECT payload, updated_at FROM cache WHERE key=?", (key,)
            ).fetchone()
        if not row:
            return None
        age = (datetime.now(timezone.utc)
               - datetime.fromisoformat(row["updated_at"])).total_seconds()
        if age > max_age:
            return None
        return json.loads(row["payload"])

    def cache_set(self, key: str, payload: dict):
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO cache(key, payload, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, "
                "updated_at=excluded.updated_at",
                (key, json.dumps(payload), utcnow_iso()),
            )

    # ---------- lang_state (release tracking — next episode estimate ke liye) ----------
    def set_lang_state(self, anilist_id: int, lang: str, last_ep: int,
                       seen_at: str | None = None):
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO lang_state(anilist_id, lang, last_ep, last_seen_at) "
                "VALUES(?,?,?,?) "
                "ON CONFLICT(anilist_id, lang) DO UPDATE SET "
                "last_ep=excluded.last_ep, last_seen_at=excluded.last_seen_at",
                (anilist_id, lang, last_ep, seen_at or utcnow_iso()),
            )

    def get_lang_states(self, anilist_id: int) -> dict:
        """{lang: {"last_ep": n, "last_seen_at": iso}}"""
        with self.lock:
            rows = self.conn.execute(
                "SELECT lang, last_ep, last_seen_at FROM lang_state WHERE anilist_id=?",
                (anilist_id,),
            ).fetchall()
        return {r["lang"]: {"last_ep": r["last_ep"],
                           "last_seen_at": r["last_seen_at"]} for r in rows}

    # ---------- manual fixes (/setep command) ----------
    def set_manual_fix(self, anilist_id: int, lang: str, count: int):
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO manual_fixes(anilist_id, lang, count, updated_at) "
                "VALUES(?,?,?,?) "
                "ON CONFLICT(anilist_id, lang) DO UPDATE SET "
                "count=excluded.count, updated_at=excluded.updated_at",
                (anilist_id, lang, count, utcnow_iso()),
            )

    def get_manual_fixes(self, anilist_id: int) -> dict:
        """{lang: count}"""
        with self.lock:
            rows = self.conn.execute(
                "SELECT lang, count FROM manual_fixes WHERE anilist_id=?",
                (anilist_id,),
            ).fetchall()
        return {r["lang"]: r["count"] for r in rows}

    def clear_manual_fix(self, anilist_id: int, lang: str | None = None) -> int:
        with self.lock, self.conn:
            if lang:
                cur = self.conn.execute(
                    "DELETE FROM manual_fixes WHERE anilist_id=? AND lang=?",
                    (anilist_id, lang))
            else:
                cur = self.conn.execute(
                    "DELETE FROM manual_fixes WHERE anilist_id=?", (anilist_id,))
            return cur.rowcount
