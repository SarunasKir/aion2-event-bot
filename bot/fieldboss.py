"""Field boss timers: read from a screenshot with Claude, or typed in by hand.

Zone field bosses have no fixed schedule. Players see a respawn countdown (or a
spawn time) in game, so an admin posts a screenshot and the bot turns it into
one-off timers that ping before each spawn.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from .schedule import REGIONS

log = logging.getLogger("aion2bot.fieldboss")

MODEL = os.getenv("FIELD_BOSS_MODEL", "claude-opus-5-5")
MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

# Field bosses aion2hub lists by zone (https://aion2hub.com/tools/world-bosses).
KNOWN_FIELD_BOSSES = [
    "Silent Dartan", "High Commander Lagta", "Soul Ruler Kashapa",
    "Deputy Commander Vivajra", "Third Unit Commander Karkoti",
    "Deputy Commander Sarvakha", "Third Unit Commander Minasara",
]

PROMPT = f"""This is a screenshot from the game Aion 2 showing field boss timers.

List every boss that has a spawn countdown or a spawn time. For each one give:
- boss: the boss name exactly as shown. Known names include: {", ".join(KNOWN_FIELD_BOSSES)}.
- zone: the zone or map name if shown, else an empty string.
- countdown_seconds: if a countdown is shown (for example "1:23:45", "45m", "2h 10m"), the total seconds left; else null.
- clock_time: if a time of day is shown instead (for example "21:30"), that time as HH:MM in 24-hour format; else null.

Skip bosses that are already up/alive or show no time. If the image shows no boss timers, return an empty list."""

SCHEMA = {
    "type": "object",
    "properties": {
        "bosses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "boss": {"type": "string"},
                    "zone": {"type": "string"},
                    "countdown_seconds": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                    "clock_time": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
                "required": ["boss", "zone", "countdown_seconds", "clock_time"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["bosses"],
    "additionalProperties": False,
}


@dataclass
class FieldTimer:
    boss: str
    zone: str
    spawn_at: datetime  # UTC


class ReadError(Exception):
    """The screenshot could not be read; the message is safe to show in Discord."""


def is_configured() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def _clock_to_utc(clock: str, region: str, now: datetime) -> datetime | None:
    """Next occurrence of HH:MM server time after `now`."""
    m = re.fullmatch(r"\s*([01]?\d|2[0-3]):([0-5]\d)\s*", clock or "")
    if not m:
        return None
    local_now = now.astimezone(REGIONS[region])
    t = local_now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    if t <= local_now:
        t += timedelta(days=1)
    return t.astimezone(now.tzinfo)


def to_timers(items: list[dict], region: str, taken_at: datetime) -> list[FieldTimer]:
    """Turn the model's list into timers. Countdowns count from when the screenshot was posted."""
    timers = []
    for item in items:
        boss = (item.get("boss") or "").strip()
        if not boss:
            continue
        seconds = item.get("countdown_seconds")
        if isinstance(seconds, int) and 0 < seconds <= 7 * 24 * 3600:
            spawn = taken_at + timedelta(seconds=seconds)
        else:
            spawn = _clock_to_utc(item.get("clock_time") or "", region, taken_at)
        if spawn is not None:
            timers.append(FieldTimer(boss[:80], (item.get("zone") or "").strip()[:80], spawn))
    return timers


async def read_screenshot(image: bytes, media_type: str) -> list[dict]:
    """Ask Claude for the boss timers in a screenshot. Raises ReadError."""
    import anthropic

    if media_type not in IMAGE_TYPES:
        raise ReadError("Please upload a PNG, JPEG, WebP or GIF image.")
    if len(image) > MAX_IMAGE_BYTES:
        raise ReadError("That image is over 5 MB. Crop it to the boss timer area and try again.")
    if not is_configured():
        raise ReadError("Screenshot reading isn't set up: the bot needs ANTHROPIC_API_KEY. Use /fieldboss add instead.")

    client = anthropic.AsyncAnthropic()
    try:
        response = await client.beta.messages.create(
            model=MODEL,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": media_type,
                        "data": base64.standard_b64encode(image).decode("ascii"),
                    }},
                    {"type": "text", "text": PROMPT},
                ],
            }],
        )
    except anthropic.AuthenticationError:
        raise ReadError("The bot's ANTHROPIC_API_KEY was rejected. Ask the bot owner to check it.")
    except anthropic.RateLimitError:
        raise ReadError("Too many screenshots at once. Try again in a minute.")
    except anthropic.APIStatusError as e:
        log.error("Screenshot read failed: %s %s", e.status_code, e.message)
        raise ReadError("Couldn't read the screenshot right now. Try again later or use /fieldboss add.")
    except anthropic.APIConnectionError:
        raise ReadError("Couldn't reach the screenshot reader. Try again later or use /fieldboss add.")

    if response.stop_reason == "refusal":
        raise ReadError("The screenshot couldn't be processed. Try a tighter crop of the timer list.")
    if response.stop_reason == "max_tokens":
        raise ReadError("Too many timers in one screenshot. Crop it into smaller parts.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)["bosses"]
    except (ValueError, KeyError, TypeError):
        log.error("Unexpected screenshot reply: %r", text[:500])
        raise ReadError("Couldn't understand the screenshot. Try again or use /fieldboss add.")


_DURATION_RE = re.compile(r"^\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m(?:in)?)?\s*(?:(\d+)\s*s)?\s*$", re.I)


def parse_when(text: str, region: str, now: datetime) -> datetime | None:
    """Spawn time from '1h 20m', '45m', '1:23:45' (countdown), or 'at 21:30' (server time)."""
    text = text.strip().lower()
    if text.startswith("at "):
        return _clock_to_utc(text[3:], region, now)
    parts = text.split(":")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        h, m, s = map(int, parts)
        return now + timedelta(hours=h, minutes=m, seconds=s)
    m = _DURATION_RE.match(text)
    if m and any(m.groups()):
        h, mi, s = (int(g or 0) for g in m.groups())
        total = timedelta(hours=h, minutes=mi, seconds=s)
        return now + total if total.total_seconds() > 0 else None
    return None
