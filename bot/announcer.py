"""Decides which followed events need a ping right now."""

from __future__ import annotations

from datetime import datetime, timedelta

from .schedule import EVENTS, Event, occurrences
from .storage import GuildConfig


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


def format_ping(events: list[Event], start: datetime, role_id: int | None) -> str:
    """One message for everything starting at `start`, e.g. three Executors at 22:30."""
    ts = int(start.timestamp())
    mention = f"<@&{role_id}> " if role_id else ""
    names = ", ".join(f"**{e.name}**" for e in events)
    verb = "spawns" if all(e.category == "boss" for e in events) else "starts"
    if len(events) > 1:
        verb = verb[:-1]
    details = sorted({("📍 " if e.category == "boss" else "") + e.description for e in events})
    return f"{mention}{names} {verb} <t:{ts}:R> (<t:{ts}:t>).\n" + "\n".join(f"-# {d}" for d in details)
