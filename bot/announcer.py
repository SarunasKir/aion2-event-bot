"""Decides which followed events need a ping right now."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import re

from .schedule import EVENTS, INFO_URLS, REGIONS, Event, occurrences
from .storage import GuildConfig

_DEFAULT = object()


def due(
    cfg: GuildConfig,
    followed: list[str],
    now: datetime,
    lead_overrides: dict[str, int] | None = None,
) -> list[tuple[Event, datetime]]:
    """Followed events starting within their lead time.

    Each event uses its entry in `lead_overrides`, or else the server's
    `lead_minutes`.

    The caller de-duplicates through Storage.mark_sent, so a check that runs
    every few seconds pings each occurrence once. If the bot comes online
    inside the lead window it still pings, just with less notice. The window
    reaches one minute into the past so a lead time of 0 ("at start") works.
    """
    lead_overrides = lead_overrides or {}
    begin = now - timedelta(minutes=1)
    result = []
    for key in followed:
        event = EVENTS.get(key)
        if event is None:
            continue
        end = now + timedelta(minutes=lead_overrides.get(key, cfg.lead_minutes))
        for start in occurrences(event, cfg.region, begin, end):
            result.append((event, start))
    result.sort(key=lambda pair: pair[1])
    return result


TIME_DISPLAYS = {
    "local": "Local time (each reader's own timezone)",
    "server": "Server time (game clock)",
    "both": "Both local and server time",
}


def format_time(start: datetime, region: str, mode: str) -> str:
    """Clock time of `start` as local time, game server time, or both."""
    local = f"<t:{int(start.timestamp())}:t> your time"
    server_tz = REGIONS[region]
    server = f"{start.astimezone(server_tz):%H:%M} server time ({server_tz.tzname(None)})"
    if mode == "local":
        return local
    if mode == "server":
        return server
    return f"{local} · {server}"


def format_ping(events: list[Event], start: datetime, cfg: GuildConfig, role_id=_DEFAULT) -> str:
    """One message for everything starting at `start`, e.g. three Executors at 22:30."""
    role_id = cfg.role_id if role_id is _DEFAULT else role_id
    ts = int(start.timestamp())
    mention = f"<@&{role_id}> " if role_id else ""
    names = ", ".join(f"**{e.name}**" for e in events)
    verb = "spawns" if all(e.category == "boss" for e in events) else "starts"
    if len(events) > 1:
        verb = verb[:-1]
    when = format_time(start, cfg.region, cfg.time_display)
    duration = max(e.duration_minutes for e in events)
    if duration:
        when += f" · ends <t:{ts + duration * 60}:t>"
    details = sorted({("📍 " if e.category == "boss" else "") + e.description for e in events})
    urls = sorted({INFO_URLS[e.category] for e in events})
    links = " · ".join(f"[More info](<{u}>)" for u in urls)
    return f"{mention}{names} {verb} <t:{ts}:R>\n🕒 {when}\n" + "\n".join(f"-# {d}" for d in details) + f"\n-# {links}"


def format_start_ping(names: list[str], cfg: GuildConfig, role_id=_DEFAULT) -> str:
    """The second, short ping when things start."""
    role_id = cfg.role_id if role_id is _DEFAULT else role_id
    mention = f"<@&{role_id}> " if role_id else ""
    joined = ", ".join(f"**{n}**" for n in names)
    return f"{mention}🔔 {joined} {'is' if len(names) == 1 else 'are'} starting now!"


def parse_clock(text: str) -> str | None:
    """'7:05' -> '07:05'; None if not a valid 24-hour time."""
    m = re.fullmatch(r"\s*([01]?\d|2[0-3]):([0-5]\d)\s*", text or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def in_quiet_hours(cfg: GuildConfig, when: datetime) -> bool:
    """True if `when` falls in the server's quiet hours (game server time). Ranges may cross midnight."""
    if not cfg.quiet_start or not cfg.quiet_end:
        return False
    local = when.astimezone(REGIONS[cfg.region])
    minute = local.hour * 60 + local.minute
    sh, sm = map(int, cfg.quiet_start.split(":"))
    eh, em = map(int, cfg.quiet_end.split(":"))
    start, end = sh * 60 + sm, eh * 60 + em
    if start == end:
        return False
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def next_reset(cfg: GuildConfig, after: datetime) -> datetime | None:
    from .schedule import next_occurrence

    return next_occurrence(EVENTS["daily_reset"], cfg.region, after)


def format_digest(
    cfg: GuildConfig,
    followed: list[str],
    field_timers: list[tuple[str, str, int]],
    now: datetime,
) -> str:
    """Summary of the next 24 hours of followed events, bosses and field boss timers."""
    end = now + timedelta(hours=24)
    rows: list[tuple[datetime, str]] = []
    for key in followed:
        event = EVENTS.get(key)
        if event is None:
            continue
        for start in occurrences(event, cfg.region, now, end):
            rows.append((start, event.name))
    for boss, zone, ts in field_timers:
        start = datetime.fromtimestamp(ts, timezone.utc)
        if now < start <= end:
            rows.append((start, f"{boss} (field boss)"))
    rows.sort()
    lines = ["📋 **Today's schedule** (next 24 hours)"]
    if not rows:
        lines.append("Nothing followed is coming up.")
    # Merge rows at the same time, e.g. three Executors at 22:30
    merged: dict[datetime, list[str]] = {}
    for start, name in rows:
        merged.setdefault(start, []).append(name)
    for start, names in list(merged.items())[:40]:
        lines.append(f"• {format_time(start, cfg.region, cfg.time_display)}: {', '.join(names)}")
    if len(merged) > 40:
        lines.append(f"…and {len(merged) - 40} more. See /events.")
    text = "\n".join(lines)
    return text[:1990]


FIELD_LEAD_KEY = "field_bosses"


def due_field(
    cfg: GuildConfig,
    timers: list[tuple[str, str, int]],
    now: datetime,
    lead_overrides: dict[str, int] | None = None,
) -> list[tuple[str, str, datetime]]:
    """Field boss timers inside the ping window: (boss, zone, spawn time)."""
    lead = (lead_overrides or {}).get(FIELD_LEAD_KEY, cfg.lead_minutes)
    begin, end = now - timedelta(minutes=1), now + timedelta(minutes=lead)
    out = []
    for boss, zone, ts in timers:
        start = datetime.fromtimestamp(ts, timezone.utc)
        if begin < start <= end:
            out.append((boss, zone, start))
    return out


def format_field_ping(boss: str, zone: str, start: datetime, cfg: GuildConfig, role_id=_DEFAULT) -> str:
    role_id = cfg.role_id if role_id is _DEFAULT else role_id
    ts = int(start.timestamp())
    mention = f"<@&{role_id}> " if role_id else ""
    where = f"\n-# 📍 {zone} · field boss" if zone else "\n-# Field boss"
    return f"{mention}**{boss}** spawns <t:{ts}:R>\n🕒 {format_time(start, cfg.region, cfg.time_display)}{where}"
