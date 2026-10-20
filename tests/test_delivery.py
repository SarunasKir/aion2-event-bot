"""Ping delivery: retries after failures and warns admins once."""

import asyncio
import types
from datetime import timedelta

import discord
import pytest

from bot.main import EventBot
from bot.schedule import REGIONS
from bot.storage import GuildConfig, Storage
from datetime import datetime

RIFT = datetime(2026, 10, 8, 14, 0, tzinfo=REGIONS["KR"])
NOW = RIFT - timedelta(minutes=10)


def _http_error(cls, status):
    resp = types.SimpleNamespace(status=status, reason="x")
    return cls(resp, "boom")


class FakeChannel:
    def __init__(self, can_send=True, fail=None):
        self.sent, self.can_send, self.fail = [], can_send, fail
        self.mention = "#alerts"
        self.id = 10

    def permissions_for(self, member):
        return types.SimpleNamespace(send_messages=self.can_send)

    async def send(self, text, **kwargs):
        if self.fail:
            err, self.fail = self.fail, None
            raise err
        self.sent.append(text)


class FakeOwner:
    def __init__(self):
        self.dms = []

    async def send(self, text, **kwargs):
        self.dms.append(text)


def make_bot(tmp_path, channel):
    storage = Storage(str(tmp_path / "d.db"))
    storage.save_config(GuildConfig(1, 10, 20, "KR", 10))
    storage.follow(1, "spacetime_rift")
    owner = FakeOwner()
    guild = types.SimpleNamespace(
        id=1, name="Clan", me=object(), system_channel=None, owner=owner, owner_id=5,
        get_channel=lambda cid: channel,
    )
    bot = EventBot(storage)
    bot.get_guild = lambda gid: guild
    return bot, guild, owner


def run(bot):
    asyncio.run(bot._check_guild(bot.storage.get_config(1), NOW))


def test_ping_sent_once(tmp_path):
    ch = FakeChannel()
    bot, _, _ = make_bot(tmp_path, ch)
    run(bot)
    run(bot)
    assert len(ch.sent) == 1 and "Spacetime Rift" in ch.sent[0]


def test_failed_ping_is_retried(tmp_path):
    ch = FakeChannel(fail=_http_error(discord.HTTPException, 503))
    bot, _, owner = make_bot(tmp_path, ch)
    run(bot)
    assert ch.sent == [] and owner.dms == []
    run(bot)
    assert len(ch.sent) == 1


@pytest.mark.parametrize("error", [OSError("dns"), asyncio.TimeoutError()])
def test_ping_lost_to_network_trouble_is_retried(tmp_path, error):
    ch = FakeChannel(fail=error)
    bot, _, _ = make_bot(tmp_path, ch)
    run(bot)
    assert ch.sent == []
    run(bot)
    assert len(ch.sent) == 1


def test_missing_permission_warns_owner_once_then_recovers(tmp_path):
    ch = FakeChannel(can_send=False)
    bot, _, owner = make_bot(tmp_path, ch)
    run(bot)
    run(bot)
    assert len(owner.dms) == 1 and "permission" in owner.dms[0]
    ch.can_send = True
    run(bot)
    assert len(ch.sent) == 1  # the ping wasn't lost while blocked
    assert bot.storage.get_meta("warned:1:no_permission:10") == ""


def test_forbidden_send_warns_and_keeps_ping(tmp_path):
    ch = FakeChannel(fail=_http_error(discord.Forbidden, 403))
    bot, _, owner = make_bot(tmp_path, ch)
    run(bot)
    assert len(owner.dms) == 1
    run(bot)
    assert len(ch.sent) == 1


def test_deleted_channel_warns(tmp_path):
    bot, guild, owner = make_bot(tmp_path, None)
    run(bot)
    assert len(owner.dms) == 1 and "/setup" in owner.dms[0]


def test_bot_only_subscribes_to_what_it_uses(tmp_path):
    bot = EventBot(Storage(str(tmp_path / "x.db")))
    assert bot.intents.value == discord.Intents(guilds=True).value  # no chat messages, typing or reactions
    assert bot._connection.max_messages is None
    assert not bot._connection.member_cache_flags.joined


def test_old_rows_are_pruned_every_half_hour_not_every_check(tmp_path):
    storage = Storage(str(tmp_path / "x.db"))
    bot = EventBot(storage)
    calls = []
    storage.prune_sent = lambda ts: calls.append(ts)
    bot.get_guild = lambda gid: None

    import bot.main as main_mod
    real = main_mod.datetime

    class Clock(real):
        current = NOW

        @classmethod
        def now(cls, tz=None):
            return cls.current

    main_mod.datetime = Clock
    try:
        for minutes in (0, 1, 29, 31):
            Clock.current = NOW + timedelta(minutes=minutes)
            asyncio.run(bot.check_events.coro(bot))
    finally:
        main_mod.datetime = real
    assert len(calls) == 2  # at 0 and 31 minutes
