"""Discord bot that pings a role before selected Aion 2 events."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import tasks

from .announcer import due, format_ping
from .schedule import EVENTS, REGION_LABELS, REGIONS, next_occurrence
from .storage import GuildConfig, Storage

log = logging.getLogger("aion2bot")

EVENT_CHOICES = [
    app_commands.Choice(name=("Boss: " if e.category == "boss" else "") + e.name, value=e.key)
    for e in EVENTS.values()
] + [
    app_commands.Choice(name="All bosses", value="*boss"),
    app_commands.Choice(name="All events", value="*event"),
    app_commands.Choice(name="Everything", value="*"),
]
REGION_CHOICES = [app_commands.Choice(name=label, value=key) for key, label in REGION_LABELS.items()]
DEFAULT_LEAD = int(os.getenv("DEFAULT_LEAD_MINUTES", "10"))


class EventBot(discord.Client):
    def __init__(self, storage: Storage):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.storage = storage

    async def setup_hook(self) -> None:
        register_commands(self)
        await self.tree.sync()
        self.check_events.start()

    async def on_ready(self) -> None:
        log.info("Logged in as %s in %d server(s)", self.user, len(self.guilds))

    @tasks.loop(seconds=20)
    async def check_events(self) -> None:
        now = datetime.now(timezone.utc)
        for cfg in self.storage.all_configs():
            channel = self.get_channel(cfg.channel_id)
            if channel is None:
                continue
            by_start: dict[datetime, list] = {}
            for event, start in due(
                cfg, self.storage.followed(cfg.guild_id), now, self.storage.lead_overrides(cfg.guild_id)
            ):
                if self.storage.mark_sent(cfg.guild_id, event.key, int(start.timestamp())):
                    by_start.setdefault(start, []).append(event)
            for start, events in by_start.items():
                try:
                    await channel.send(
                        format_ping(events, start, cfg.role_id),
                        allowed_mentions=discord.AllowedMentions(roles=True),
                    )
                except discord.HTTPException:
                    log.exception("Could not post %s in guild %s", [e.key for e in events], cfg.guild_id)
        self.storage.prune_sent(int((now - timedelta(days=2)).timestamp()))

    @check_events.before_loop
    async def before_check(self) -> None:
        await self.wait_until_ready()


def _config(bot: EventBot, guild_id: int) -> GuildConfig:
    return bot.storage.get_config(guild_id) or GuildConfig(guild_id, None, None, "GLOBAL", DEFAULT_LEAD)


def _expand(choice: str) -> list[str]:
    if choice == "*":
        return list(EVENTS)
    if choice.startswith("*"):
        return [k for k, e in EVENTS.items() if e.category == choice[1:]]
    return [choice]


def _minutes(lead: int) -> str:
    return "at start" if lead == 0 else f"{lead} min before"


def _status_text(bot: EventBot, guild_id: int) -> str:
    cfg = _config(bot, guild_id)
    now = datetime.now(timezone.utc)
    channel = f"<#{cfg.channel_id}>" if cfg.channel_id else "not set (use /setup)"
    role = f"<@&{cfg.role_id}>" if cfg.role_id else "none"
    lines = [
        f"**Channel:** {channel}",
        f"**Role:** {role}",
        f"**Server region:** {REGION_LABELS[cfg.region]}",
        f"**Ping:** {_minutes(cfg.lead_minutes)} (change with /ping-time)",
        "",
    ]
    followed = set(bot.storage.followed(guild_id))
    overrides = bot.storage.lead_overrides(guild_id)
    for category, title in [("event", "Events"), ("boss", "World bosses")]:
        lines.append(f"**{title}** (✅ = pinged):")
        for event in EVENTS.values():
            if event.category != category:
                continue
            mark = "✅" if event.key in followed else "▫️"
            nxt = next_occurrence(event, cfg.region, now)
            when = f"next <t:{int(nxt.timestamp())}:R>" if nxt else "not in this region"
            custom = f" · ping {_minutes(overrides[event.key])}" if event.key in overrides else ""
            lines.append(f"{mark} {event.name}: {when}{custom}")
        lines.append("")
    return "\n".join(lines)


def register_commands(bot: EventBot) -> None:
    admin = app_commands.default_permissions(manage_guild=True)

    @bot.tree.command(description="Set the announcement channel, role to ping, region and lead time.")
    @admin
    @app_commands.guild_only()
    @app_commands.describe(
        channel="Channel to post announcements in",
        role="Role to ping",
        region="Which game servers you play on",
        lead_minutes="How many minutes before the event to ping (0 = at start)",
    )
    @app_commands.choices(region=REGION_CHOICES)
    async def setup(
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        role: discord.Role,
        region: app_commands.Choice[str] | None = None,
        lead_minutes: app_commands.Range[int, 0, 120] | None = None,
    ) -> None:
        cfg = _config(bot, interaction.guild_id)
        cfg.channel_id = channel.id
        cfg.role_id = role.id
        if region:
            cfg.region = region.value
        if lead_minutes is not None:
            cfg.lead_minutes = lead_minutes
        bot.storage.save_config(cfg)
        warn = ""
        perms = channel.permissions_for(interaction.guild.me)
        if not perms.send_messages:
            warn += "\n⚠️ I can't send messages in that channel yet."
        if not role.mentionable and not perms.mention_everyone:
            warn += "\n⚠️ That role isn't mentionable, so pings won't notify anyone. " \
                    "Make it mentionable or give me *Mention @everyone, @here and All Roles*."
        await interaction.response.send_message(
            "Saved. Now pick events with /follow.\n\n" + _status_text(bot, interaction.guild_id) + warn,
            ephemeral=True,
        )

    @bot.tree.command(description="Start announcing an event.")
    @admin
    @app_commands.guild_only()
    @app_commands.choices(event=EVENT_CHOICES)
    async def follow(interaction: discord.Interaction, event: app_commands.Choice[str]) -> None:
        keys = _expand(event.value)
        for key in keys:
            bot.storage.follow(interaction.guild_id, key)
        await interaction.response.send_message(
            f"Following **{event.name}**.\n\n" + _status_text(bot, interaction.guild_id), ephemeral=True
        )

    @bot.tree.command(description="Stop announcing an event.")
    @admin
    @app_commands.guild_only()
    @app_commands.choices(event=EVENT_CHOICES)
    async def unfollow(interaction: discord.Interaction, event: app_commands.Choice[str]) -> None:
        keys = _expand(event.value)
        for key in keys:
            bot.storage.unfollow(interaction.guild_id, key)
        await interaction.response.send_message(
            f"Stopped **{event.name}**.\n\n" + _status_text(bot, interaction.guild_id), ephemeral=True
        )

    @bot.tree.command(name="ping-time", description="Set how many minutes before the start to ping.")
    @admin
    @app_commands.guild_only()
    @app_commands.describe(
        minutes="Minutes before start (0 = at start). Leave empty with an event to reset it to the server default.",
        event="Only change this event or boss. Leave empty to change the server default.",
    )
    @app_commands.choices(event=EVENT_CHOICES)
    async def ping_time(
        interaction: discord.Interaction,
        minutes: app_commands.Range[int, 0, 120] | None = None,
        event: app_commands.Choice[str] | None = None,
    ) -> None:
        if event is None:
            if minutes is None:
                await interaction.response.send_message(
                    "Give minutes to change the server default, or pick an event to reset it.", ephemeral=True
                )
                return
            cfg = _config(bot, interaction.guild_id)
            cfg.lead_minutes = minutes
            bot.storage.save_config(cfg)
            done = f"Server default is now **{_minutes(minutes)}**. Events with their own time keep it."
        else:
            for key in _expand(event.value):
                bot.storage.set_lead_override(interaction.guild_id, key, minutes)
            if minutes is None:
                done = f"**{event.name}** now uses the server default."
            else:
                done = f"**{event.name}** now pings **{_minutes(minutes)}**."
        await interaction.response.send_message(
            done + "\n\n" + _status_text(bot, interaction.guild_id), ephemeral=True
        )

    @bot.tree.command(description="Show settings, followed events and when each is next.")
    @app_commands.guild_only()
    async def events(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(_status_text(bot, interaction.guild_id), ephemeral=True)

    @bot.tree.command(name="test-ping", description="Post a sample announcement in the configured channel.")
    @admin
    @app_commands.guild_only()
    async def test_ping(interaction: discord.Interaction) -> None:
        cfg = _config(bot, interaction.guild_id)
        channel = bot.get_channel(cfg.channel_id) if cfg.channel_id else None
        if channel is None:
            await interaction.response.send_message("Run /setup first.", ephemeral=True)
            return
        event = EVENTS["spacetime_rift"]
        start = datetime.now(timezone.utc) + timedelta(minutes=cfg.lead_minutes)
        await channel.send(
            "🧪 Test announcement\n" + format_ping([event], start, cfg.role_id),
            allowed_mentions=discord.AllowedMentions(roles=True),
        )
        await interaction.response.send_message(f"Sent a test to {channel.mention}.", ephemeral=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("Set DISCORD_TOKEN (see README).")
    assert set(REGION_LABELS) == set(REGIONS)
    storage = Storage(os.getenv("DATABASE_PATH", "bot.db"))
    EventBot(storage).run(token, log_handler=None)


if __name__ == "__main__":
    main()
