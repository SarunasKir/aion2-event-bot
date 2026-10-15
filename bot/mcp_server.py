"""MCP server that lets Claude manage the bot: settings, followed events, field boss timers and boss images.

Run it next to the bot's database, and connect Claude Desktop or Claude Code to it over stdio:
    python -m bot.mcp_server --env .env
On the Google Cloud VM made by deploy/gcp.sh, `sudo aion2-mcp` runs it, so a client on your
computer can start it over SSH (see the README section "Control the bot from Claude").
"""

from __future__ import annotations

import argparse
import logging
import os
from typing import Any

from mcp.server.mcpserver import Image, MCPServer

from .control import Control, DiscordAPI
from .storage import Storage

log = logging.getLogger("aion2bot.mcp")

INSTRUCTIONS = """Manage the Aion 2 Discord event bot: which events and bosses it pings for, where and how early,
field boss timers, and the image on boss pings. Changes reach the running bot within about 20 seconds.
`server` can be left out when the bot is set up in only one Discord server; otherwise give its name or ID
(list_servers shows them). Times called "server time" are the game server's clock for the server's region.
Before changing settings, tell the user what you are about to change."""


def load_env(path: str) -> None:
    """Read KEY=value lines from a .env file without overriding variables that are already set."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def build(control: Control) -> MCPServer:
    server = MCPServer("aion2-event-bot", instructions=INSTRUCTIONS)

    async def gid(name: str | None) -> int:
        return await control.guild_id(name)

    @server.tool(description="List the Discord servers the bot is in, and whether each is set up.")
    async def list_servers() -> Any:
        return await control.list_servers()

    @server.tool(description="List every event and world boss the bot knows, with the keys follow/unfollow accept.")
    def list_events() -> Any:
        return control.list_events()

    @server.tool(description="Show a server's settings: channel, role, region, ping times, options, routes, "
                             "followed events and boss image style.")
    async def get_settings(server: str | None = None) -> Any:
        return control.settings(await gid(server))

    @server.tool(description="Events, world bosses and field boss timers coming up in the next hours (max 168), "
                             "with UTC and server time and whether each is followed.")
    async def upcoming(server: str | None = None, hours: int = 24) -> Any:
        return control.upcoming(await gid(server), hours)

    @server.tool(description="Set the announcement channel and the role to ping (names or IDs). "
                             "Optionally the region (KR, TW or GLOBAL) and minutes before the start to ping (0-120).")
    async def setup(channel: str, role: str, region: str | None = None, lead_minutes: int | None = None,
                    server: str | None = None) -> str:
        return await control.setup(await gid(server), channel, role, region, lead_minutes)

    @server.tool(description="Start pinging events or bosses. Accepts keys or names from list_events, "
                             "or 'all bosses', 'all events', 'everything'.")
    async def follow(events: list[str], server: str | None = None) -> str:
        names = control.follow(await gid(server), events, True)
        return "Following: " + ", ".join(names)

    @server.tool(description="Stop pinging events or bosses. Same names as follow.")
    async def unfollow(events: list[str], server: str | None = None) -> str:
        names = control.follow(await gid(server), events, False)
        return "Stopped: " + ", ".join(names)

    @server.tool(description="Set how many minutes before the start to ping (0-120). Without event, sets the server "
                             "default. With event (a name, a group, or 'field bosses'), sets just that; with event and "
                             "no minutes, puts it back on the default.")
    async def set_ping_time(minutes: int | None = None, event: str | None = None, server: str | None = None) -> str:
        return control.set_ping_time(await gid(server), minutes, event)

    @server.tool(description="Change options; leave out what should stay. time_display: local, server or both. "
                             "quiet_hours: like 02:00-08:00 server time, or 'off'. start_ping: also ping at the start. "
                             "daily_digest: post the day's schedule after the daily reset. auto_delete_minutes: delete "
                             "pings that many minutes after the start; auto_delete_off turns that off.")
    async def set_options(
        time_display: str | None = None,
        quiet_hours: str | None = None,
        start_ping: bool | None = None,
        daily_digest: bool | None = None,
        auto_delete_minutes: int | None = None,
        auto_delete_off: bool = False,
        server: str | None = None,
    ) -> Any:
        return control.set_options(await gid(server), time_display, quiet_hours, start_ping, daily_digest,
                                   auto_delete_minutes, auto_delete_off)

    @server.tool(description="Send one category's pings (event, boss or field) to their own channel and/or role. "
                             "Leave both out to go back to the setup channel and role.")
    async def set_route(category: str, channel: str | None = None, role: str | None = None,
                        server: str | None = None) -> str:
        return await control.set_route(await gid(server), category, channel, role)

    @server.tool(description="Add or replace a field boss timer. spawns_in: like 1h 20m, 45m, 1:23:45, or 'at 21:30' "
                             "(server time). respawn: optional time from kill to next spawn, like 2h, for the "
                             "Killed button.")
    async def add_field_timer(boss: str, spawns_in: str, zone: str = "", respawn: str = "",
                              server: str | None = None) -> Any:
        return control.add_field_timer(await gid(server), boss, spawns_in, zone, respawn)

    @server.tool(description="Remove a field boss timer; give zone to remove only that zone's timer.")
    async def remove_field_timer(boss: str, zone: str | None = None, server: str | None = None) -> str:
        removed = control.remove_field_timer(await gid(server), boss, zone)
        return "Removed." if removed else "No such timer. upcoming lists them."

    @server.tool(description="Turn the image on boss pings on or off and/or change its style. Leave out what should "
                             "stay. Colors are #rrggbb. font: serif or sans. layout: left or center. border: none, "
                             "thin, double or glow. pattern: none, vignette, rays, stripes or sparks. tagline: up to "
                             "30 characters ('' removes it). Call preview_boss_image afterwards to see it.")
    async def set_boss_image(
        enabled: bool | None = None,
        bg_top: str | None = None,
        bg_bottom: str | None = None,
        accent: str | None = None,
        text: str | None = None,
        subtext: str | None = None,
        font: str | None = None,
        layout: str | None = None,
        border: str | None = None,
        pattern: str | None = None,
        uppercase: bool | None = None,
        tagline: str | None = None,
        remove_background: bool = False,
        server: str | None = None,
    ) -> Any:
        style = dict(bg_top=bg_top, bg_bottom=bg_bottom, accent=accent, text=text, subtext=subtext, font=font,
                     layout=layout, border=border, pattern=pattern, uppercase=uppercase, tagline=tagline)
        return control.set_boss_image(await gid(server), enabled, style, remove_background)

    @server.tool(description="Show a sample of the image boss pings get, in the server's current style.")
    async def preview_boss_image(server: str | None = None) -> Any:
        return Image(data=control.boss_image_preview(await gid(server)), format="png")

    @server.tool(description="Post a sample ping in the server's announcement channel, pinging its role.")
    async def send_test_ping(server: str | None = None) -> str:
        return await control.test_ping(await gid(server))

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", help="a .env file to read DISCORD_TOKEN and DATABASE_PATH from")
    args = parser.parse_args()
    # stdout carries the MCP protocol, so logs go to stderr.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.env:
        load_env(args.env)
    token = os.environ.get("DISCORD_TOKEN")
    storage = Storage(os.getenv("DATABASE_PATH", "bot.db"))
    build(Control(storage, DiscordAPI(token) if token else None)).run("stdio")


if __name__ == "__main__":
    main()
