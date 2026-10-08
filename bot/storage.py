"""SQLite storage for per-server settings, followed events and sent pings."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id     INTEGER PRIMARY KEY,
    channel_id   INTEGER,
    role_id      INTEGER,
    region       TEXT    NOT NULL DEFAULT 'GLOBAL',
    lead_minutes INTEGER NOT NULL DEFAULT 10
);
CREATE TABLE IF NOT EXISTS subscriptions (
    guild_id  INTEGER NOT NULL,
    event_key TEXT    NOT NULL,
    PRIMARY KEY (guild_id, event_key)
);
CREATE TABLE IF NOT EXISTS sent (
    guild_id  INTEGER NOT NULL,
    event_key TEXT    NOT NULL,
    starts_at INTEGER NOT NULL,
    PRIMARY KEY (guild_id, event_key, starts_at)
);
"""


@dataclass
class GuildConfig:
    guild_id: int
    channel_id: int | None
    role_id: int | None
    region: str
    lead_minutes: int


class Storage:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        self.db.commit()

    def get_config(self, guild_id: int) -> GuildConfig | None:
        row = self.db.execute(
            "SELECT guild_id, channel_id, role_id, region, lead_minutes "
            "FROM guild_config WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()
        return GuildConfig(*row) if row else None

    def all_configs(self) -> list[GuildConfig]:
        rows = self.db.execute(
            "SELECT guild_id, channel_id, role_id, region, lead_minutes FROM guild_config "
            "WHERE channel_id IS NOT NULL"
        ).fetchall()
        return [GuildConfig(*r) for r in rows]

    def save_config(self, cfg: GuildConfig) -> None:
        self.db.execute(
            "INSERT INTO guild_config (guild_id, channel_id, role_id, region, lead_minutes) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET "
            "channel_id = excluded.channel_id, role_id = excluded.role_id, "
            "region = excluded.region, lead_minutes = excluded.lead_minutes",
            (cfg.guild_id, cfg.channel_id, cfg.role_id, cfg.region, cfg.lead_minutes),
        )
        self.db.commit()

    def followed(self, guild_id: int) -> list[str]:
        rows = self.db.execute(
            "SELECT event_key FROM subscriptions WHERE guild_id = ? ORDER BY event_key",
            (guild_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def follow(self, guild_id: int, event_key: str) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO subscriptions (guild_id, event_key) VALUES (?, ?)",
            (guild_id, event_key),
        )
        self.db.commit()

    def unfollow(self, guild_id: int, event_key: str) -> None:
        self.db.execute(
            "DELETE FROM subscriptions WHERE guild_id = ? AND event_key = ?",
            (guild_id, event_key),
        )
        self.db.commit()

    def mark_sent(self, guild_id: int, event_key: str, starts_at: int) -> bool:
        """Record a ping. Returns False if this occurrence was already announced."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO sent (guild_id, event_key, starts_at) VALUES (?, ?, ?)",
            (guild_id, event_key, starts_at),
        )
        self.db.commit()
        return cur.rowcount == 1

    def prune_sent(self, before_ts: int) -> None:
        self.db.execute("DELETE FROM sent WHERE starts_at < ?", (before_ts,))
        self.db.commit()
