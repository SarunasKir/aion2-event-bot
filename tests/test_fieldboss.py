import asyncio
import json
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

from bot import fieldboss
from bot.announcer import FIELD_LEAD_KEY, due_field, format_field_ping
from bot.schedule import REGIONS
from bot.storage import GuildConfig, Storage

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)  # 21:00 KST


def test_to_timers_countdown_and_clock():
    items = [
        {"boss": "Silent Dartan", "zone": "Altgard", "countdown_seconds": 3600, "clock_time": None},
        {"boss": "High Commander Lagta", "zone": "", "countdown_seconds": None, "clock_time": "21:30"},
        {"boss": "Soul Ruler Kashapa", "zone": "", "countdown_seconds": None, "clock_time": "20:00"},
        {"boss": "", "zone": "", "countdown_seconds": 60, "clock_time": None},
        {"boss": "No Time", "zone": "", "countdown_seconds": None, "clock_time": None},
    ]
    timers = fieldboss.to_timers(items, "KR", NOW)
    assert [(t.boss, t.spawn_at) for t in timers] == [
        ("Silent Dartan", NOW + timedelta(hours=1)),
        ("High Commander Lagta", NOW + timedelta(minutes=30)),  # 21:30 KST today
        ("Soul Ruler Kashapa", NOW + timedelta(hours=23)),  # 20:00 KST already passed, so tomorrow
    ]


@pytest.mark.parametrize(
    "text, delta",
    [("1h 20m", timedelta(hours=1, minutes=20)), ("45m", timedelta(minutes=45)),
     ("1:23:45", timedelta(hours=1, minutes=23, seconds=45)), ("at 21:30", timedelta(minutes=30)),
     ("2h", timedelta(hours=2))],
)
def test_parse_when(text, delta):
    assert fieldboss.parse_when(text, "KR", NOW) == NOW + delta


@pytest.mark.parametrize("text", ["soon", "", "0m", "at 25:00"])
def test_parse_when_rejects(text):
    assert fieldboss.parse_when(text, "KR", NOW) is None


def test_field_timers_ping_once_and_replace(tmp_path):
    s = Storage(str(tmp_path / "f.db"))
    cfg = GuildConfig(1, 10, 20, "KR", 10)
    spawn = NOW + timedelta(minutes=10)
    s.set_field_timer(1, "Silent Dartan", "Altgard", int(spawn.timestamp()))
    s.set_field_timer(1, "Silent Dartan", "Altgard", int((spawn + timedelta(hours=2)).timestamp()))
    assert len(s.field_timers(1)) == 1  # one pending timer per boss
    assert due_field(cfg, s.field_timers(1), NOW) == []
    s.set_field_timer(1, "Silent Dartan", "Altgard", int(spawn.timestamp()))
    hits = due_field(cfg, s.field_timers(1), NOW)
    assert [(b, t) for b, _, t in hits] == [("Silent Dartan", spawn)]
    # A separate ping time for field bosses
    assert due_field(cfg, s.field_timers(1), NOW - timedelta(minutes=10), {FIELD_LEAD_KEY: 20})
    msg = format_field_ping("Silent Dartan", "Altgard", spawn, cfg)
    assert msg.startswith("<@&20> **Silent Dartan** spawns <t:") and "Altgard" in msg
    assert s.remove_field_timer(1, "silent dartan")
    assert s.field_timers(1) == []


def test_read_screenshot_rejects_before_calling_api(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    for data, mime in [(b"x", "text/plain"), (b"x" * (fieldboss.MAX_IMAGE_BYTES + 1), "image/png"), (b"x", "image/png")]:
        with pytest.raises(fieldboss.ReadError):
            asyncio.run(fieldboss.read_screenshot(data, mime))


def test_read_screenshot_request_and_parse(monkeypatch):
    """Checks the request we send and how the reply is parsed, with the API client faked."""
    import anthropic

    sent = {}

    class FakeMessages:
        async def create(self, **kwargs):
            sent.update(kwargs)
            payload = {"bosses": [{"boss": "Silent Dartan", "zone": "Altgard", "countdown_seconds": 90, "clock_time": None}]}
            return types.SimpleNamespace(
                stop_reason="end_turn", content=[types.SimpleNamespace(type="text", text=json.dumps(payload))]
            )

    class FakeClient:
        def __init__(self, *a, **k):
            self.beta = types.SimpleNamespace(messages=FakeMessages())

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeClient)
    items = asyncio.run(fieldboss.read_screenshot(b"\x89PNG...", "image/png"))
    assert items[0]["boss"] == "Silent Dartan"
    assert sent["model"] == fieldboss.MODEL
    assert sent["fallbacks"] == "default"
    assert sent["output_config"]["format"]["schema"] == fieldboss.SCHEMA
    assert sent["messages"][0]["content"][0]["type"] == "image"
