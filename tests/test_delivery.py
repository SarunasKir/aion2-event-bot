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


def test_missing_permission_warns_owner_once_then_recovers(tmp_path):
    ch = FakeChannel(can_send=False)
    bot, _, owner = make_bot(tmp_path, ch)
    run(bot)
    run(bot)
    assert len(owner.dms) == 1 and "permission" in owner.dms[0]
    ch.can_send = True
    run(bot)
    assert len(ch.sent) == 1  # the ping wasn't lost while blocked
    assert bot.storage.get_meta("warned:1") == ""


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
