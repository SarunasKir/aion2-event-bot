"""The controls the MCP server gives Claude, and the MCP server itself."""

import asyncio
import io

import pytest
from PIL import Image

from bot import mcp_server
from bot.control import Control, ControlError
from bot.storage import GuildConfig, Storage


class FakeDiscord:
    def __init__(self):
        self.sent = []

    async def guilds(self):
        return [{"id": "1", "name": "Legion"}, {"id": "2", "name": "Other"}]

    async def channels(self, guild_id):
        return [{"id": "10", "name": "boss-alerts", "type": 0}, {"id": "11", "name": "Voice", "type": 2},
                {"id": "12", "name": "events", "type": 0}]

    async def roles(self, guild_id):
        return [{"id": "1", "name": "@everyone"}, {"id": "20", "name": "Bosses"}, {"id": "21", "name": "PvP"}]

    async def send(self, channel_id, content, role_id):
        self.sent.append((channel_id, content, role_id))
        return {"id": "99"}


def make(tmp_path, configured=True):
    s = Storage(str(tmp_path / "c.db"))
    if configured:
        s.save_config(GuildConfig(1, 10, 20, "KR", 10))
    discord = FakeDiscord()
    return Control(s, discord), discord


def run(coro):
    return asyncio.run(coro)


def test_server_can_be_left_out_when_only_one_is_set_up(tmp_path):
    c, _ = make(tmp_path)
    assert run(c.guild_id(None)) == 1
    assert run(c.guild_id("Other")) == 2
    assert run(c.guild_id("2")) == 2
    c.storage.save_config(GuildConfig(2, 12, 21, "TW", 5))
    with pytest.raises(ControlError, match="list_servers"):
        run(c.guild_id(None))


def test_setup_finds_channels_and_roles_by_name(tmp_path):
    c, _ = make(tmp_path, configured=False)
    run(c.setup(1, "#events", "@PvP", "tw", 15))
    cfg = c.storage.get_config(1)
    assert (cfg.channel_id, cfg.role_id, cfg.region, cfg.lead_minutes) == (12, 21, "TW", 15)
    with pytest.raises(ControlError, match="No text channel called 'Voice'"):
        run(c.setup(1, "Voice", "PvP", None, None))
    with pytest.raises(ControlError, match="Region"):
        run(c.setup(1, "events", "PvP", "EU", None))


def test_follow_by_name_key_or_group(tmp_path):
    c, _ = make(tmp_path)
    names = c.follow(1, ["Spacetime Rift", "all bosses"])
    assert "Spacetime Rift" in names and "Executor Argo" in names
    c.follow(1, ["spacetime_rift"], follow=False)
    assert "spacetime_rift" not in c.storage.followed(1)
    with pytest.raises(ControlError, match="Unknown event"):
        c.follow(1, ["Balaur"])


def test_ping_times_and_options(tmp_path):
    c, _ = make(tmp_path)
    c.set_ping_time(1, 30, None)
    c.set_ping_time(1, 5, "field bosses")
    c.set_ping_time(1, 0, "Executor Argo")
    assert c.storage.get_config(1).lead_minutes == 30
    assert c.storage.lead_overrides(1) == {"field_bosses": 5, "executor_argo": 0}
    with pytest.raises(ControlError):
        c.set_ping_time(1, 500, None)
    s = c.set_options(1, time_display="server", quiet_hours="23:00-07:00", start_ping=True, auto_delete_minutes=30)
    assert (s["time_display"], s["quiet_hours_server_time"], s["start_ping"], s["auto_delete_minutes_after_start"]) \
        == ("server", "23:00-07:00", True, 30)
    s = c.set_options(1, quiet_hours="off", auto_delete_off=True)
    assert s["quiet_hours_server_time"] is None and s["auto_delete_minutes_after_start"] is None
    with pytest.raises(ControlError, match="quiet_hours"):
        c.set_options(1, quiet_hours="late")


def test_routes(tmp_path):
    c, _ = make(tmp_path)
    run(c.set_route(1, "boss", "boss-alerts", "Bosses"))
    assert c.storage.route(1, "boss") == (10, 20)
    run(c.set_route(1, "boss", None, None))
    assert c.storage.route(1, "boss") == (None, None)


def test_field_timers_show_in_upcoming(tmp_path):
    c, _ = make(tmp_path)
    added = c.add_field_timer(1, "Silent Dartan", "1h", "Altgard", "2h")
    assert added["boss"] == "Silent Dartan" and added["server_time"].endswith("KST")
    assert c.storage.respawn_minutes(1, "Silent Dartan") == 120
    rows = c.upcoming(1, 3)
    assert any(r["name"] == "Silent Dartan" and r["category"] == "field" for r in rows)
    assert rows == sorted(rows, key=lambda r: r["utc"])
    assert c.remove_field_timer(1, "Silent Dartan", "Altgard")
    with pytest.raises(ControlError, match="spawns_in"):
        c.add_field_timer(1, "Silent Dartan", "soon")


def test_boss_image_style_changes_only_what_is_given(tmp_path):
    c, _ = make(tmp_path)
    state = c.set_boss_image(1, None, {"accent": "#ff0000", "layout": "center", "font": None}, False)
    assert state["enabled"] is True and state["style"]["accent"] == "#ff0000"
    assert state["style"]["font"] == "serif"  # the default, untouched
    state = c.set_boss_image(1, False, {}, False)
    assert state["enabled"] is False and state["style"]["layout"] == "center"
    state = c.set_boss_image(1, None, {"tagline": "Go"}, False)
    assert state["enabled"] is False  # changing the style doesn't turn images back on
    with Image.open(io.BytesIO(c.boss_image_preview(1))) as img:
        assert img.size == (1200, 400)


def test_test_ping_posts_through_discord(tmp_path):
    c, discord = make(tmp_path)
    run(c.test_ping(1))
    channel, text, role = discord.sent[0]
    assert (channel, role) == (10, 20) and "Spacetime Rift" in text


def test_mcp_server_lists_tools_and_returns_errors(tmp_path):
    c, _ = make(tmp_path)
    server = mcp_server.build(c)
    tools = {t.name for t in run(server.list_tools())}
    assert {"get_settings", "follow", "add_field_timer", "set_boss_image", "preview_boss_image"} <= tools
    with pytest.raises(Exception, match="Unknown event"):
        run(server.call_tool("follow", {"events": ["Balaur"]}))


def test_load_env_keeps_existing_variables(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nDISCORD_TOKEN=abc\nDATABASE_PATH='/data/x.db'\n")
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.setenv("DATABASE_PATH", "keep.db")
    mcp_server.load_env(str(env))
    import os
    assert os.environ["DISCORD_TOKEN"] == "abc" and os.environ["DATABASE_PATH"] == "keep.db"
