"""Routing, quiet hours, start pings, digest, auto-delete, details, health."""

import asyncio
import time
import types
from datetime import datetime, timedelta, timezone

from bot import fieldboss, health
from bot.announcer import format_digest, format_ping, in_quiet_hours, next_reset, parse_clock
from bot.main import EventBot
from bot.schedule import EVENTS, REGIONS
from bot.storage import GuildConfig, Storage

KR = REGIONS["KR"]


class Chan:
    def __init__(self, cid):
        self.id, self.mention, self.sent = cid, f"<#{cid}>", []

    def permissions_for(self, m):
        return types.SimpleNamespace(send_messages=True)

    async def send(self, text, **kw):
        self.sent.append((text, kw))
        return types.SimpleNamespace(id=1000 + len(self.sent))


def bot_with(tmp_path, cfg, follow=(), channels=(10,)):
    s = Storage(str(tmp_path / "x.db"))
    s.save_config(cfg)
    for k in follow:
        s.follow(cfg.guild_id, k)
    chans = {c: Chan(c) for c in channels}
    guild = types.SimpleNamespace(id=1, name="G", me=object(), system_channel=None, owner=None, owner_id=1,
                                  get_channel=lambda c: chans.get(c))
    bot = EventBot(s)
    bot.get_guild = lambda gid: guild
    return bot, chans


def tick(bot, now):
    asyncio.run(bot._check_guild(bot.storage.get_config(1), now))


def test_storage_round_trips_new_settings(tmp_path):
    s = Storage(str(tmp_path / "s.db"))
    s.save_config(GuildConfig(1, 2, 3, "TW", 5, "server", "02:00", "08:00", True, True, 30))
    cfg = s.get_config(1)
    assert (cfg.quiet_start, cfg.quiet_end, cfg.start_ping, cfg.digest, cfg.delete_after) == ("02:00", "08:00", True, True, 30)


def test_quiet_hours_cross_midnight():
    cfg = GuildConfig(1, 2, 3, "KR", 10, quiet_start="23:00", quiet_end="07:00")
    assert in_quiet_hours(cfg, datetime(2026, 10, 8, 2, 0, tzinfo=KR))
    assert in_quiet_hours(cfg, datetime(2026, 10, 8, 23, 0, tzinfo=KR))
    assert not in_quiet_hours(cfg, datetime(2026, 10, 8, 7, 0, tzinfo=KR))
    assert not in_quiet_hours(GuildConfig(1, 2, 3, "KR", 10), datetime(2026, 10, 8, 2, 0, tzinfo=KR))
    assert parse_clock("7:05") == "07:05" and parse_clock("24:00") is None


def test_quiet_hours_skip_pings(tmp_path):
    cfg = GuildConfig(1, 10, 3, "KR", 10, quiet_start="01:00", quiet_end="03:00")
    bot, chans = bot_with(tmp_path, cfg, ["spacetime_rift"])
    rift = datetime(2026, 10, 8, 2, 0, tzinfo=KR)  # inside quiet hours
    tick(bot, rift - timedelta(minutes=10))
    tick(bot, rift - timedelta(minutes=5))
    assert chans[10].sent == []
    rift = datetime(2026, 10, 8, 5, 0, tzinfo=KR)
    tick(bot, rift - timedelta(minutes=10))
    assert len(chans[10].sent) == 1


def test_routes_send_bosses_elsewhere(tmp_path):
    cfg = GuildConfig(1, 10, 3, "KR", 10)
    bot, chans = bot_with(tmp_path, cfg, ["executor_argo", "spacetime_rift"], channels=(10, 20))
    bot.storage.set_route(1, "boss", 20, 99)
    wed = datetime(2026, 10, 7, 22, 30, tzinfo=KR)
    tick(bot, wed - timedelta(minutes=10))
    assert chans[10].sent == []
    assert chans[20].sent[0][0].startswith("<@&99> **Executor Argo**")
    rift = datetime(2026, 10, 7, 23, 0, tzinfo=KR)
    tick(bot, rift - timedelta(minutes=10))
    assert chans[10].sent[0][0].startswith("<@&3> **Spacetime Rift**")


def test_start_ping_and_auto_delete(tmp_path):
    cfg = GuildConfig(1, 10, 3, "KR", 10, start_ping=True, delete_after=15)
    bot, chans = bot_with(tmp_path, cfg, ["spacetime_rift"])
    rift = datetime(2026, 10, 8, 14, 0, tzinfo=KR)
    tick(bot, rift - timedelta(minutes=10))
    tick(bot, rift + timedelta(seconds=5))
    texts = [t for t, _ in chans[10].sent]
    assert len(texts) == 2 and "starting now" in texts[1]
    due = bot.storage.posted_due(int((rift + timedelta(minutes=15)).timestamp()))
    assert len(due) == 2 and bot.storage.posted_due(int(rift.timestamp())) == []


def test_digest_once_after_reset(tmp_path):
    cfg = GuildConfig(1, 10, 3, "KR", 10, digest=True)
    bot, chans = bot_with(tmp_path, cfg, ["spacetime_rift", "executor_argo"])
    reset = datetime(2026, 10, 7, 5, 0, tzinfo=KR)  # Wednesday
    tick(bot, reset + timedelta(minutes=1))
    tick(bot, reset + timedelta(minutes=2))
    digests = [t for t, kw in chans[10].sent if t.startswith("📋")]
    assert len(digests) == 1
    assert "Executor Argo" in digests[0] and "Spacetime Rift" in digests[0]
    assert next_reset(cfg, reset) == reset + timedelta(days=1)


def test_digest_fits_in_a_message():
    cfg = GuildConfig(1, 10, 3, "KR", 10)
    text = format_digest(cfg, list(EVENTS), [], datetime(2026, 10, 7, 5, 0, tzinfo=KR))
    assert len(text) <= 2000


def test_ping_shows_end_time_and_link():
    cfg = GuildConfig(1, 10, 3, "KR", 10)
    start = datetime(2026, 10, 8, 20, 0, tzinfo=KR)
    msg = format_ping([EVENTS["rift_domination"]], start, cfg)
    assert f"ends <t:{int(start.timestamp()) + 20 * 60}:t>" in msg
    assert "aion2hub.com/tools/event-timer" in msg
    assert "ends" not in format_ping([EVENTS["spacetime_rift"]], start, cfg)


def test_respawn_minutes_and_storage(tmp_path):
    assert fieldboss.parse_duration_minutes("2h") == 120
    assert fieldboss.parse_duration_minutes("1h 30m") == 90
    assert fieldboss.parse_duration_minutes("soon") is None
    s = Storage(str(tmp_path / "r.db"))
    s.set_respawn_minutes(1, "Silent Dartan", 120)
    assert s.respawn_minutes(1, "silent dartan") == 120


def test_health_staleness():
    assert not health.is_stale(None, 10_000)
    assert not health.is_stale(100.0, 200.0, timeout=300)
    assert health.is_stale(100.0, 500.0, timeout=300)


def test_killed_button_restarts_timer(tmp_path):
    cfg = GuildConfig(1, 10, 3, "KR", 10)
    bot, _ = bot_with(tmp_path, cfg)
    bot.storage.set_field_timer(1, "Silent Dartan", "Altgard", int(time.time()) - 60)
    bot.storage.set_respawn_minutes(1, "Silent Dartan", 120)
    replies = []

    async def send_message(text, **kw):
        replies.append(text)

    inter = types.SimpleNamespace(
        guild_id=1, user=types.SimpleNamespace(mention="@Sam"),
        response=types.SimpleNamespace(send_message=send_message),
    )
    asyncio.run(bot._boss_killed(inter, "Silent Dartan", "Altgard"))
    (boss, zone, ts), = bot.storage.field_timers(1)
    assert (boss, zone) == ("Silent Dartan", "Altgard")
    assert abs(ts - (time.time() + 7200)) < 5
    assert "killed by @Sam" in replies[0]


def test_field_ping_has_killed_button(tmp_path):
    cfg = GuildConfig(1, 10, 3, "KR", 10)
    bot, chans = bot_with(tmp_path, cfg)
    now = datetime.now(timezone.utc)
    bot.storage.set_field_timer(1, "Silent Dartan", "", int((now + timedelta(minutes=5)).timestamp()))
    tick(bot, now)
    text, kw = chans[10].sent[0]
    assert "Silent Dartan" in text
    assert kw["view"].children[0].custom_id == "aion2:killed:Silent Dartan|"


def test_pings_can_only_mention_their_role(tmp_path):
    """A boss name typed by a user must not be able to ping @everyone or other roles."""
    cfg = GuildConfig(1, 10, 3, "KR", 10)
    bot, chans = bot_with(tmp_path, cfg)
    now = datetime.now(timezone.utc)
    bot.storage.set_field_timer(1, "@everyone <@&777>", "", int((now + timedelta(minutes=5)).timestamp()))
    tick(bot, now)
    am = chans[10].sent[0][1]["allowed_mentions"]
    assert am.everyone is False and am.users is False
    assert [r.id for r in am.roles] == [3]


def test_same_boss_in_two_zones_keeps_both(tmp_path):
    s = Storage(str(tmp_path / "z.db"))
    s.set_field_timer(1, "Silent Dartan", "Altgard", 100)
    s.set_field_timer(1, "Silent Dartan", "Verteron", 200)
    s.set_field_timer(1, "silent dartan", "ALTGARD", 300)  # same boss and zone, other case: replaces
    assert sorted(s.field_timers(1)) == [("Silent Dartan", "Altgard", 300), ("Silent Dartan", "Verteron", 200)]
    assert s.remove_field_timer(1, "Silent Dartan", "verteron")
    assert s.field_timers(1) == [("Silent Dartan", "Altgard", 300)]


def test_old_field_timer_table_is_migrated(tmp_path):
    import sqlite3

    path = str(tmp_path / "old.db")
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE field_timers (guild_id INTEGER NOT NULL, boss TEXT NOT NULL, "
               "zone TEXT NOT NULL DEFAULT '', spawn_at INTEGER NOT NULL, PRIMARY KEY (guild_id, boss))")
    db.execute("INSERT INTO field_timers VALUES (1, 'Silent Dartan', 'Altgard', 100)")
    db.commit()
    db.close()
    s = Storage(path)
    s.set_field_timer(1, "Silent Dartan", "Verteron", 200)
    assert len(s.field_timers(1)) == 2


def test_role_buttons_only_toggle_ping_roles(tmp_path):
    from bot import views

    replies = []

    async def send_message(text, **kw):
        replies.append(text)

    inter = types.SimpleNamespace(guild=object(), response=types.SimpleNamespace(send_message=send_message))
    asyncio.run(views.toggle_role(inter, 555, allowed={3}))
    assert "out of date" in replies[0]


def test_clean_name():
    assert fieldboss.clean_name("  Silent   Dartan | x ") == "Silent Dartan / x"
    assert len(fieldboss.clean_name("x" * 100)) == fieldboss.NAME_LIMIT
