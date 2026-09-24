"""SQLite: watchlist, competition sessions, light research cache."""
from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    user_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    label TEXT,
    payload_json TEXT,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS comp_sessions (
    session_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    subject_json TEXT,
    mode TEXT,
    shown_json TEXT,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS research_cache (
    cache_key TEXT PRIMARY KEY,
    payload_json TEXT,
    updated_at INTEGER NOT NULL
);
"""


class DB:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()
            self.conn = None

    @property
    def c(self) -> aiosqlite.Connection:
        assert self.conn
        return self.conn

    async def watch(self, user_id: int, key: str, label: str, payload: dict) -> None:
        await self.c.execute(
            """INSERT INTO watches(user_id,key,label,payload_json,created_at) VALUES(?,?,?,?,?)
               ON CONFLICT(user_id,key) DO UPDATE SET label=excluded.label, payload_json=excluded.payload_json""",
            (user_id, key, label, json.dumps(payload), int(time.time())),
        )
        await self.c.commit()

    async def unwatch(self, user_id: int, key: str) -> None:
        await self.c.execute("DELETE FROM watches WHERE user_id=? AND key=?", (user_id, key))
        await self.c.commit()

    async def watchlist(self, user_id: int) -> list[dict[str, Any]]:
        cur = await self.c.execute(
            "SELECT * FROM watches WHERE user_id=? ORDER BY created_at DESC", (user_id,)
        )
        return [dict(r) for r in await cur.fetchall()]

    async def save_comp_session(
        self, session_id: str, user_id: int, subject: dict, mode: str, shown: list[str]
    ) -> None:
        await self.c.execute(
            """INSERT INTO comp_sessions(session_id,user_id,subject_json,mode,shown_json,created_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(session_id) DO UPDATE SET
                 mode=excluded.mode, shown_json=excluded.shown_json""",
            (session_id, user_id, json.dumps(subject), mode, json.dumps(shown), int(time.time())),
        )
        await self.c.commit()

    async def get_comp_session(self, session_id: str) -> dict[str, Any] | None:
        cur = await self.c.execute("SELECT * FROM comp_sessions WHERE session_id=?", (session_id,))
        row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["subject"] = json.loads(d.get("subject_json") or "{}")
        d["shown"] = json.loads(d.get("shown_json") or "[]")
        return d

    async def cache_set(self, key: str, payload: dict) -> None:
        await self.c.execute(
            """INSERT INTO research_cache(cache_key,payload_json,updated_at) VALUES(?,?,?)
               ON CONFLICT(cache_key) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
            (key, json.dumps(payload), int(time.time())),
        )
        await self.c.commit()

    async def cache_get(self, key: str, max_age: int = 3600) -> dict | None:
        cur = await self.c.execute("SELECT * FROM research_cache WHERE cache_key=?", (key,))
        row = await cur.fetchone()
        if not row:
            return None
        if int(time.time()) - int(row["updated_at"]) > max_age:
            return None
        return json.loads(row["payload_json"] or "{}")
