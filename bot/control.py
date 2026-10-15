"""Bot settings as plain functions, for the MCP server (bot/mcp_server.py).

Everything here writes the same database the running bot reads on every check, so a
change takes effect within about 20 seconds without restarting the bot. Messages the
bot posts through Discord's API (test pings) use the bot token directly.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import aiohttp
from mcp.server.mcpserver.exceptions import ToolError

from . import cards, fieldboss, watcher
from .announcer import FIELD_LEAD_KEY, TIME_DISPLAYS, format_ping, parse_clock
from .schedule import EVENTS, REGION_LABELS, REGIONS, occurrences
from .storage import GuildConfig, Storage

API = "https://discord.com/api/v10"
CATEGORIES = {"event": "Events", "boss": "World bosses", "field": "Field bosses"}
GROUPS = {"all bosses": "boss", "all events": "event"}


class ControlError(ToolError):
    """A request that can't be done; the MCP server returns the message to Claude as a tool error."""


class DiscordAPI:
    """The few Discord REST calls the controls need, made with the bot's token."""

    def __init__(self, token: str):
        self.token = token

    async def _request(self, method: str, path: str, payload: dict | None = None):
        headers = {"Authorization": f"Bot {self.token}", "User-Agent": "aion2-event-bot (mcp)"}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.request(method, API + path, json=payload) as resp:
                body = await resp.json(content_type=None)
                if resp.status >= 400:
                    raise ControlError(f"Discord refused {method} {path}: {resp.status} {body}")
                return body

    async def guilds(self) -> list[dict]:
        return await self._request("GET", "/users/@me/guilds")

    async def channels(self, guild_id: int) -> list[dict]:
        return await self._request("GET", f"/guilds/{guild_id}/channels")

    async def roles(self, guild_id: int) -> list[dict]:
        return await self._request("GET", f"/guilds/{guild_id}/roles")

    async def send(self, channel_id: int, content: str, role_id: int | None) -> dict:
        mentions = {"parse": [], "roles": [str(role_id)] if role_id else []}
        return await self._request(
            "POST", f"/channels/{channel_id}/messages", {"content": content, "allowed_mentions": mentions}
        )


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _server_time(dt: datetime, region: str) -> str:
    tz = REGIONS[region]
    return f"{dt.astimezone(tz):%a %H:%M} {tz.tzname(None)}"


def _find(items: list[dict], text: str, kind: str) -> dict:
    """A channel or role by ID, mention, or name (case-insensitive)."""
    key = re.sub(r"^[#@]|^<[#@]&?|>$", "", text.strip())
    for item in items:
        if str(item["id"]) == key:
            return item
    matches = [i for i in items if i["name"].lower() == key.lower()]
    if len(matches) == 1:
        return matches[0]
    names = ", ".join(sorted(i["name"] for i in items))[:1500]
    if matches:
        raise ControlError(f"More than one {kind} is called '{key}'. Use its ID instead.")
    raise ControlError(f"No {kind} called '{text}'. Available: {names}")


class Control:
    def __init__(self, storage: Storage, discord: DiscordAPI | None):
        self.storage = storage
        self.discord = discord
        saved = storage.get_meta("schedule")
        if saved:  # times the bot read from aion2hub, so upcoming times match what it pings
            watcher.apply(watcher.from_json(json.loads(saved)))

    # ------------------------------------------------------------------ helpers

    def _api(self) -> DiscordAPI:
        if self.discord is None:
            raise ControlError("DISCORD_TOKEN isn't set, so I can't reach Discord.")
        return self.discord

    async def guild_id(self, server: str | int | None) -> int:
        """A server by ID or name; may be left out when the bot is set up in exactly one server."""
        if server in (None, ""):
            configured = self.storage.all_configs()
            if len(configured) == 1:
                return configured[0].guild_id
            raise ControlError("Say which server: call list_servers to see them.")
        text = str(server).strip()
        if text.isdigit():
            return int(text)
        return int(_find(await self._api().guilds(), text, "server")["id"])

    def config(self, guild_id: int) -> GuildConfig:
        return self.storage.get_config(guild_id) or GuildConfig(guild_id, None, None, "GLOBAL", 10)

    async def _channel_id(self, guild_id: int, channel: str) -> int:
        text = [c for c in await self._api().channels(guild_id) if c.get("type") in (0, 5)]
        return int(_find(text, channel, "text channel")["id"])

    async def _role_id(self, guild_id: int, role: str) -> int:
        roles = [r for r in await self._api().roles(guild_id) if r["name"] != "@everyone"]
        return int(_find(roles, role, "role")["id"])

    @staticmethod
    def event_keys(names: list[str]) -> list[str]:
        """Event keys from keys, names, or the groups "all bosses", "all events" and "everything"."""
        keys: list[str] = []
        by_name = {e.name.lower(): k for k, e in EVENTS.items()}
        for raw in names:
            name = raw.strip().lower()
            if name in ("everything", "all", "*"):
                keys += list(EVENTS)
            elif name in GROUPS:
                keys += [k for k, e in EVENTS.items() if e.category == GROUPS[name]]
            elif name in EVENTS:
                keys.append(name)
            elif name in by_name:
                keys.append(by_name[name])
            else:
                raise ControlError(f"Unknown event '{raw}'. Call list_events for the names.")
        return list(dict.fromkeys(keys))

    # ------------------------------------------------------------------ reading

    async def list_servers(self) -> list[dict]:
        configured = {c.guild_id: c for c in self.storage.all_configs()}
        out = []
        for g in await self._api().guilds():
            gid = int(g["id"])
            out.append({"id": str(gid), "name": g["name"], "set_up": gid in configured})
        return out

    def list_events(self) -> list[dict]:
        return [
            {"key": e.key, "name": e.name, "category": e.category, "where": e.description, "regions": e.regions()}
            for e in EVENTS.values()
        ]

    def settings(self, guild_id: int) -> dict:
        cfg = self.config(guild_id)
        saved = self.storage.card_style(guild_id)
        return {
            "channel_id": str(cfg.channel_id) if cfg.channel_id else None,
            "role_id": str(cfg.role_id) if cfg.role_id else None,
            "region": f"{cfg.region} ({REGION_LABELS[cfg.region]})",
            "ping_minutes_before": cfg.lead_minutes,
            "ping_minutes_per_event": self.storage.lead_overrides(guild_id),
            "time_display": cfg.time_display,
            "quiet_hours_server_time": f"{cfg.quiet_start}-{cfg.quiet_end}" if cfg.quiet_start else None,
            "start_ping": cfg.start_ping,
            "daily_digest": cfg.digest,
            "auto_delete_minutes_after_start": cfg.delete_after,
            "routes": {
                c: {"channel_id": str(ch) if ch else None, "role_id": str(r) if r else None}
                for c, (ch, r) in self.storage.routes(guild_id).items()
            },
            "followed": self.storage.followed(guild_id),
            "boss_images": {
                "enabled": bool(saved and saved[0]),
                "style": asdict(_style(saved)),
                "has_background_image": bool(saved and saved[2]),
            },
        }

    def upcoming(self, guild_id: int, hours: int = 24) -> list[dict]:
        cfg = self.config(guild_id)
        now = datetime.now(timezone.utc)
        end = now + timedelta(hours=max(1, min(hours, 24 * 7)))
        followed = set(self.storage.followed(guild_id))
        rows = []
        for event in EVENTS.values():
            for start in occurrences(event, cfg.region, now, end):
                rows.append({"utc": _iso(start), "server_time": _server_time(start, cfg.region), "name": event.name,
                             "category": event.category, "followed": event.key in followed})
        for boss, zone, ts in self.storage.field_timers(guild_id):
            start = datetime.fromtimestamp(ts, timezone.utc)
            if now < start <= end:
                rows.append({"utc": _iso(start), "server_time": _server_time(start, cfg.region),
                             "name": boss, "zone": zone, "category": "field", "followed": True})
        return sorted(rows, key=lambda r: r["utc"])[:200]

    # ------------------------------------------------------------------ changing settings

    async def setup(self, guild_id: int, channel: str, role: str, region: str | None, lead_minutes: int | None) -> str:
        cfg = self.config(guild_id)
        cfg.channel_id = await self._channel_id(guild_id, channel)
        cfg.role_id = await self._role_id(guild_id, role)
        if region:
            if region.upper() not in REGIONS:
                raise ControlError(f"Region must be one of {', '.join(REGIONS)}.")
            cfg.region = region.upper()
        if lead_minutes is not None:
            cfg.lead_minutes = _lead(lead_minutes)
        self.storage.save_config(cfg)
        return f"Pings go to channel {cfg.channel_id} and ping role {cfg.role_id}, region {cfg.region}."

    def follow(self, guild_id: int, events: list[str], follow: bool = True) -> list[str]:
        keys = self.event_keys(events)
        for key in keys:
            (self.storage.follow if follow else self.storage.unfollow)(guild_id, key)
        return [EVENTS[k].name for k in keys]

    def set_ping_time(self, guild_id: int, minutes: int | None, event: str | None) -> str:
        if not event:
            if minutes is None:
                raise ControlError("Give minutes, or an event to put back on the server default.")
            cfg = self.config(guild_id)
            cfg.lead_minutes = _lead(minutes)
            self.storage.save_config(cfg)
            return f"Server default is now {cfg.lead_minutes} minutes before."
        keys = [FIELD_LEAD_KEY] if event.strip().lower() in ("field bosses", FIELD_LEAD_KEY) else self.event_keys([event])
        for key in keys:
            self.storage.set_lead_override(guild_id, key, None if minutes is None else _lead(minutes))
        return f"Updated {len(keys)} item(s)."

    def set_options(
        self,
        guild_id: int,
        time_display: str | None = None,
        quiet_hours: str | None = None,
        start_ping: bool | None = None,
        daily_digest: bool | None = None,
        auto_delete_minutes: int | None = None,
        auto_delete_off: bool = False,
    ) -> dict:
        cfg = self.config(guild_id)
        if time_display is not None:
            if time_display not in TIME_DISPLAYS:
                raise ControlError(f"time_display must be one of {', '.join(TIME_DISPLAYS)}.")
            cfg.time_display = time_display
        if quiet_hours is not None:
            if quiet_hours.strip().lower() == "off":
                cfg.quiet_start = cfg.quiet_end = None
            else:
                start, _, end = quiet_hours.partition("-")
                qs, qe = parse_clock(start), parse_clock(end)
                if qs is None or qe is None or qs == qe:
                    raise ControlError("quiet_hours must look like 02:00-08:00 (server time), or 'off'.")
                cfg.quiet_start, cfg.quiet_end = qs, qe
        if start_ping is not None:
            cfg.start_ping = start_ping
        if daily_digest is not None:
            cfg.digest = daily_digest
        if auto_delete_off:
            cfg.delete_after = None
        elif auto_delete_minutes is not None:
            if not 0 <= auto_delete_minutes <= 1440:
                raise ControlError("auto_delete_minutes must be 0 to 1440.")
            cfg.delete_after = auto_delete_minutes
        self.storage.save_config(cfg)
        return self.settings(guild_id)

    async def set_route(self, guild_id: int, category: str, channel: str | None, role: str | None) -> str:
        if category not in CATEGORIES:
            raise ControlError(f"category must be one of {', '.join(CATEGORIES)}.")
        channel_id = await self._channel_id(guild_id, channel) if channel else None
        role_id = await self._role_id(guild_id, role) if role else None
        self.storage.set_route(guild_id, category, channel_id, role_id)
        if channel_id is None and role_id is None:
            return f"{CATEGORIES[category]} use the setup channel and role again."
        return f"{CATEGORIES[category]} go to channel {channel_id or 'from setup'} and ping role {role_id or 'from setup'}."

    # ------------------------------------------------------------------ field bosses

    def add_field_timer(self, guild_id: int, boss: str, spawns_in: str, zone: str = "", respawn: str = "") -> dict:
        cfg = self.config(guild_id)
        spawn = fieldboss.parse_when(spawns_in, cfg.region, datetime.now(timezone.utc))
        if spawn is None:
            raise ControlError("spawns_in must look like 1h 20m, 45m, 1:23:45, or 'at 21:30' (server time).")
        name, where = fieldboss.clean_name(boss), fieldboss.clean_name(zone)
        if not name:
            raise ControlError("Give the boss a name.")
        if respawn:
            minutes = fieldboss.parse_duration_minutes(respawn)
            if minutes is None:
                raise ControlError("respawn must look like 2h or 1h 30m.")
            self.storage.set_respawn_minutes(guild_id, name, minutes)
        self.storage.set_field_timer(guild_id, name, where, int(spawn.timestamp()))
        return {"boss": name, "zone": where, "utc": _iso(spawn), "server_time": _server_time(spawn, cfg.region)}

    def remove_field_timer(self, guild_id: int, boss: str, zone: str | None = None) -> bool:
        return self.storage.remove_field_timer(guild_id, boss.strip(), zone.strip() if zone is not None else None)

    # ------------------------------------------------------------------ boss images

    def set_boss_image(self, guild_id: int, enabled: bool | None, style: dict, remove_background: bool) -> dict:
        saved = self.storage.card_style(guild_id)
        current = asdict(_style(saved))
        current.update({k: v for k, v in style.items() if v is not None})
        new = cards.CardStyle.from_dict(current)
        background = None if remove_background or saved is None else saved[2]
        self.storage.set_card_style(guild_id, new.to_json(), background)
        if enabled is False or (enabled is None and saved is not None and not saved[0]):
            self.storage.set_card_enabled(guild_id, False)
        return self.settings(guild_id)["boss_images"]

    def boss_image_preview(self, guild_id: int) -> bytes:
        saved = self.storage.card_style(guild_id)
        return cards.sample(_style(saved), saved[2] if saved else None)

    # ------------------------------------------------------------------ Discord

    async def test_ping(self, guild_id: int) -> str:
        cfg = self.config(guild_id)
        if not cfg.channel_id:
            raise ControlError("This server has no channel yet. Call setup first.")
        start = datetime.now(timezone.utc) + timedelta(minutes=cfg.lead_minutes)
        text = "🧪 Test announcement (sent from Claude)\n" + format_ping([EVENTS["spacetime_rift"]], start, cfg)
        await self._api().send(cfg.channel_id, text, cfg.role_id)
        return f"Sent a test ping to channel {cfg.channel_id}."


def _lead(minutes: int) -> int:
    if not 0 <= minutes <= 120:
        raise ControlError("Minutes must be 0 to 120.")
    return minutes


def _style(saved) -> cards.CardStyle:
    if not saved:
        return cards.CardStyle()
    try:
        return cards.CardStyle.from_dict(json.loads(saved[1] or "{}"))
    except (ValueError, AttributeError):
        return cards.CardStyle()
