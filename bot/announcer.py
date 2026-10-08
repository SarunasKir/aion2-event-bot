"""Decides which followed events need a ping right now."""

from __future__ import annotations

from datetime import datetime, timedelta

from .schedule import EVENTS, Event, occurrences
from .storage import GuildConfig


def due(cfg: GuildConfig, followed: list[str], now: datetime) -> list[tuple[Event, datetime]]:
    """Followed events starting within the next `lead_minutes`.

    The caller de-duplicates through Storage.mark_sent, so a check that runs
    every few seconds pings each occurrence once. If the bot comes online
    inside the lead window it still pings, just with less notice. The window
    reaches one minute into the past so a lead time of 0 ("at start") works.
    """
    end = now + timedelta(minutes=cfg.lead_minutes)
    begin = now - timedelta(minutes=1)
    result = []
    for key in followed:
        event = EVENTS.get(key)
        if event is None:
            continue
        for start in occurrences(event, cfg.region, begin, end):
            result.append((event, start))
    result.sort(key=lambda pair: pair[1])
    return result


def format_ping(event: Event, start: datetime, role_id: int | None) -> str:
    ts = int(start.timestamp())
    mention = f"<@&{role_id}> " if role_id else ""
    return (
        f"{mention}**{event.name}** starts <t:{ts}:R> (<t:{ts}:t>).\n"
        f"-# {event.description}"
    )
