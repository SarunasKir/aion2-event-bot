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
    lead_minutes INTEGER NOT NULL DEFAULT 10,
    time_display TEXT    NOT NULL DEFAULT 'both'
);
CREATE TABLE IF NOT EXISTS subscriptions (
    guild_id  INTEGER NOT NULL,
    event_key TEXT    NOT NULL,
    PRIMARY KEY (guild_id, event_key)
);
CREATE TABLE IF NOT EXISTS lead_overrides (
    guild_id     INTEGER NOT NULL,
    event_key    TEXT    NOT NULL,
    lead_minutes INTEGER NOT NULL,
    PRIMARY KEY (guild_id, event_key)
);
CREATE TABLE IF NOT EXISTS meta (
    name  TEXT PRIMARY KEY,
    value TEXT NOT NULL
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
    time_display: str = "both"  # "local", "server" or "both"


class Storage:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(guild_config)")}
        if "time_display" not in columns:  # databases created before this setting existed
            self.db.execute("ALTER TABLE guild_config ADD COLUMN time_display TEXT NOT NULL DEFAULT 'both'")
        self.db.commit()

    def get_config(self, guild_id: int) -> GuildConfig | None:
        row = self.db.execute(
            "SELECT guild_id, channel_id, role_id, region, lead_minutes, time_display "
            "FROM guild_config WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()
        return GuildConfig(*row) if row else None

    def all_configs(self) -> list[GuildConfig]:
        rows = self.db.execute(
            "SELECT guild_id, channel_id, role_id, region, lead_minutes, time_display FROM guild_config "
            "WHERE channel_id IS NOT NULL"
        ).fetchall()
        return [GuildConfig(*r) for r in rows]

    def save_config(self, cfg: GuildConfig) -> None:
        self.db.execute(
            "INSERT INTO guild_config (guild_id, channel_id, role_id, region, lead_minutes, time_display) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET "
            "channel_id = excluded.channel_id, role_id = excluded.role_id, "
            "region = excluded.region, lead_minutes = excluded.lead_minutes, "
            "time_display = excluded.time_display",
            (cfg.guild_id, cfg.channel_id, cfg.role_id, cfg.region, cfg.lead_minutes, cfg.time_display),
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

    def lead_overrides(self, guild_id: int) -> dict[str, int]:
        rows = self.db.execute(
            "SELECT event_key, lead_minutes FROM lead_overrides WHERE guild_id = ?",
            (guild_id,),
        ).fetchall()
        return dict(rows)

    def set_lead_override(self, guild_id: int, event_key: str, minutes: int | None) -> None:
        """Set how early to ping for one event; None goes back to the server default."""
        if minutes is None:
            self.db.execute(
                "DELETE FROM lead_overrides WHERE guild_id = ? AND event_key = ?",
                (guild_id, event_key),
            )
        else:
            self.db.execute(
                "INSERT INTO lead_overrides (guild_id, event_key, lead_minutes) VALUES (?, ?, ?) "
                "ON CONFLICT(guild_id, event_key) DO UPDATE SET lead_minutes = excluded.lead_minutes",
                (guild_id, event_key, minutes),
            )
        self.db.commit()

    def get_meta(self, name: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE name = ?", (name,)).fetchone()
        return row[0] if row else None

    def set_meta(self, name: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO meta (name, value) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
            (name, value),
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
