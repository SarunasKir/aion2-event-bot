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
    time_display TEXT    NOT NULL DEFAULT 'both',
    quiet_start  TEXT,
    quiet_end    TEXT,
    start_ping   INTEGER NOT NULL DEFAULT 0,
    digest       INTEGER NOT NULL DEFAULT 0,
    delete_after INTEGER
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
CREATE TABLE IF NOT EXISTS field_timers (
    guild_id INTEGER NOT NULL,
    boss     TEXT    NOT NULL,
    zone     TEXT    NOT NULL DEFAULT '',
    spawn_at INTEGER NOT NULL,
    PRIMARY KEY (guild_id, boss)
);
CREATE TABLE IF NOT EXISTS routes (
    guild_id   INTEGER NOT NULL,
    category   TEXT    NOT NULL,
    channel_id INTEGER,
    role_id    INTEGER,
    PRIMARY KEY (guild_id, category)
);
CREATE TABLE IF NOT EXISTS field_respawn (
    guild_id INTEGER NOT NULL,
    boss     TEXT    NOT NULL,
    minutes  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, boss)
);
CREATE TABLE IF NOT EXISTS posted (
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    delete_at  INTEGER NOT NULL,
    PRIMARY KEY (channel_id, message_id)
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


_CONFIG_COLUMNS = {
    "time_display": "TEXT NOT NULL DEFAULT 'both'",
    "quiet_start": "TEXT",
    "quiet_end": "TEXT",
    "start_ping": "INTEGER NOT NULL DEFAULT 0",
    "digest": "INTEGER NOT NULL DEFAULT 0",
    "delete_after": "INTEGER",
}
_FIELDS = "guild_id, channel_id, role_id, region, lead_minutes, " + ", ".join(_CONFIG_COLUMNS)


@dataclass
class GuildConfig:
    guild_id: int
    channel_id: int | None
    role_id: int | None
    region: str
    lead_minutes: int
    time_display: str = "both"  # "local", "server" or "both"
    quiet_start: str | None = None  # "HH:MM" server time, or None for no quiet hours
    quiet_end: str | None = None
    start_ping: bool = False  # also ping when the event starts
    digest: bool = False  # post a daily summary after the daily reset
    delete_after: int | None = None  # minutes after the start to delete pings


def _config(row) -> GuildConfig:
    cfg = GuildConfig(*row)
    cfg.start_ping, cfg.digest = bool(cfg.start_ping), bool(cfg.digest)
    return cfg


class Storage:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        # Add settings columns that databases from older versions don't have yet.
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(guild_config)")}
        for name, ddl in _CONFIG_COLUMNS.items():
            if name not in columns:
                self.db.execute(f"ALTER TABLE guild_config ADD COLUMN {name} {ddl}")
        self.db.commit()

    def get_config(self, guild_id: int) -> GuildConfig | None:
        row = self.db.execute(f"SELECT {_FIELDS} FROM guild_config WHERE guild_id = ?", (guild_id,)).fetchone()
        return _config(row) if row else None

    def all_configs(self) -> list[GuildConfig]:
        rows = self.db.execute(f"SELECT {_FIELDS} FROM guild_config WHERE channel_id IS NOT NULL").fetchall()
        return [_config(r) for r in rows]

    def save_config(self, cfg: GuildConfig) -> None:
        names = _FIELDS.split(", ")
        values = [getattr(cfg, n) for n in names]
        values = [int(v) if isinstance(v, bool) else v for v in values]
        updates = ", ".join(f"{n} = excluded.{n}" for n in names[1:])
        self.db.execute(
            f"INSERT INTO guild_config ({_FIELDS}) VALUES ({', '.join('?' * len(names))}) "
            f"ON CONFLICT(guild_id) DO UPDATE SET {updates}",
            values,
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

    def set_field_timer(self, guild_id: int, boss: str, zone: str, spawn_at: int) -> None:
        """Add or replace the timer for a field boss (one pending spawn per boss)."""
        self.db.execute(
            "INSERT INTO field_timers (guild_id, boss, zone, spawn_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(guild_id, boss) DO UPDATE SET zone = excluded.zone, spawn_at = excluded.spawn_at",
            (guild_id, boss, zone, spawn_at),
        )
        self.db.commit()

    def field_timers(self, guild_id: int) -> list[tuple[str, str, int]]:
        """(boss, zone, spawn_at) for a server, soonest first."""
        return self.db.execute(
            "SELECT boss, zone, spawn_at FROM field_timers WHERE guild_id = ? ORDER BY spawn_at",
            (guild_id,),
        ).fetchall()

    def remove_field_timer(self, guild_id: int, boss: str) -> bool:
        cur = self.db.execute(
            "DELETE FROM field_timers WHERE guild_id = ? AND lower(boss) = lower(?)", (guild_id, boss)
        )
        self.db.commit()
        return cur.rowcount > 0

    def prune_field_timers(self, before_ts: int) -> None:
        self.db.execute("DELETE FROM field_timers WHERE spawn_at < ?", (before_ts,))
        self.db.commit()

    def route(self, guild_id: int, category: str) -> tuple[int | None, int | None]:
        """(channel_id, role_id) set for a category, or (None, None) to use the server defaults."""
        row = self.db.execute(
            "SELECT channel_id, role_id FROM routes WHERE guild_id = ? AND category = ?", (guild_id, category)
        ).fetchone()
        return (row[0], row[1]) if row else (None, None)

    def routes(self, guild_id: int) -> dict[str, tuple[int | None, int | None]]:
        rows = self.db.execute(
            "SELECT category, channel_id, role_id FROM routes WHERE guild_id = ?", (guild_id,)
        ).fetchall()
        return {c: (ch, r) for c, ch, r in rows}

    def set_route(self, guild_id: int, category: str, channel_id: int | None, role_id: int | None) -> None:
        if channel_id is None and role_id is None:
            self.db.execute("DELETE FROM routes WHERE guild_id = ? AND category = ?", (guild_id, category))
        else:
            self.db.execute(
                "INSERT INTO routes (guild_id, category, channel_id, role_id) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(guild_id, category) DO UPDATE SET "
                "channel_id = excluded.channel_id, role_id = excluded.role_id",
                (guild_id, category, channel_id, role_id),
            )
        self.db.commit()

    def respawn_minutes(self, guild_id: int, boss: str) -> int | None:
        row = self.db.execute(
            "SELECT minutes FROM field_respawn WHERE guild_id = ? AND boss = lower(?)", (guild_id, boss)
        ).fetchone()
        return row[0] if row else None

    def set_respawn_minutes(self, guild_id: int, boss: str, minutes: int) -> None:
        self.db.execute(
            "INSERT INTO field_respawn (guild_id, boss, minutes) VALUES (?, lower(?), ?) "
            "ON CONFLICT(guild_id, boss) DO UPDATE SET minutes = excluded.minutes",
            (guild_id, boss, minutes),
        )
        self.db.commit()

    def add_posted(self, channel_id: int, message_id: int, delete_at: int) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO posted (channel_id, message_id, delete_at) VALUES (?, ?, ?)",
            (channel_id, message_id, delete_at),
        )
        self.db.commit()

    def posted_due(self, now_ts: int) -> list[tuple[int, int]]:
        return self.db.execute(
            "SELECT channel_id, message_id FROM posted WHERE delete_at <= ?", (now_ts,)
        ).fetchall()

    def remove_posted(self, channel_id: int, message_id: int) -> None:
        self.db.execute("DELETE FROM posted WHERE channel_id = ? AND message_id = ?", (channel_id, message_id))
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

    def was_sent(self, guild_id: int, event_key: str, starts_at: int) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM sent WHERE guild_id = ? AND event_key = ? AND starts_at = ?",
            (guild_id, event_key, starts_at),
        ).fetchone()
        return row is not None

    def unmark_sent(self, guild_id: int, event_key: str, starts_at: int) -> None:
        """Forget a ping that failed to post, so it is tried again."""
        self.db.execute(
            "DELETE FROM sent WHERE guild_id = ? AND event_key = ? AND starts_at = ?",
            (guild_id, event_key, starts_at),
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
