"""Discord bot that pings a role before selected Aion 2 events."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

import aiohttp
import discord
from discord import app_commands
from discord.ext import tasks

from .announcer import (
    FIELD_LEAD_KEY,
    TIME_DISPLAYS,
    due,
    due_field,
    format_digest,
    format_field_ping,
    format_ping,
    format_start_ping,
    format_time,
    in_quiet_hours,
    next_reset,
    parse_clock,
)
from .schedule import EVENTS, REGION_LABELS, REGIONS, next_occurrence
from .storage import GuildConfig, Storage
from . import fieldboss, health, views, watcher

log = logging.getLogger("aion2bot")

EVENT_CHOICES = [
    app_commands.Choice(name=("Boss: " if e.category == "boss" else "") + e.name, value=e.key)
    for e in EVENTS.values()
] + [
    app_commands.Choice(name="All bosses", value="*boss"),
    app_commands.Choice(name="All events", value="*event"),
    app_commands.Choice(name="Everything", value="*"),
]
PING_TIME_CHOICES = EVENT_CHOICES + [app_commands.Choice(name="Field bosses (timers you add)", value=FIELD_LEAD_KEY)]
CATEGORY_CHOICES = [
    app_commands.Choice(name="Events", value="event"),
    app_commands.Choice(name="World bosses", value="boss"),
    app_commands.Choice(name="Field bosses", value="field"),
]
CATEGORY_LABELS = {c.value: c.name for c in CATEGORY_CHOICES}
TIME_DISPLAY_CHOICES = [app_commands.Choice(name=label, value=key) for key, label in TIME_DISPLAYS.items()]
REGION_CHOICES = [app_commands.Choice(name=label, value=key) for key, label in REGION_LABELS.items()]
DEFAULT_LEAD = int(os.getenv("DEFAULT_LEAD_MINUTES", "10"))
AUTO_UPDATE = os.getenv("SCHEDULE_AUTO_UPDATE", "1") != "0"


class EventBot(discord.Client):
    def __init__(self, storage: Storage):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.storage = storage

    async def setup_hook(self) -> None:
        saved = self.storage.get_meta("schedule")
        if saved:
            watcher.apply(watcher.from_json(json.loads(saved)))
            log.info("Loaded schedule updates saved from aion2hub")
        register_commands(self)
        await self.tree.sync()
        self.check_events.start()
        health.start_watchdog()
        if AUTO_UPDATE:
            self.refresh_schedule.start()

    async def update_schedule(self) -> tuple[list[watcher.Change], list[str], list[str]]:
        """Read aion2hub, apply and save any changed times. Returns (changes, problems, versions)."""
        async with aiohttp.ClientSession() as session:
            boss_html = await watcher.fetch(session, watcher.BOSS_URL)
            event_html = await watcher.fetch(session, watcher.EVENT_URL)
        parsed, problems = watcher.read_pages(boss_html, event_html)
        versions = watcher.source_versions(watcher.page_text(boss_html)) if boss_html else []
        changes = watcher.diff(parsed)
        if changes:
            watcher.apply(parsed)
            saved = watcher.from_json(json.loads(self.storage.get_meta("schedule") or "[]"))
            saved.update(parsed)
            self.storage.set_meta("schedule", json.dumps(watcher.to_json(saved)))
        self.storage.set_meta("schedule_checked_at", datetime.now(timezone.utc).isoformat())
        for p in problems:
            log.warning("Schedule check: %s", p)
        for (key, region), rule in sorted(parsed.items()):
            log.info("Schedule check read: %s (%s) = %s", key, region, watcher.describe(rule))
        bosses = sum(1 for k, _ in parsed if EVENTS[k].category == "boss")
        summary = f"Read {bosses} boss time(s) and {len(parsed) - bosses} event time(s) from aion2hub."
        self.storage.set_meta("schedule_last_read", summary)
        log.info("Schedule check: %s %d changed", summary, len(changes))
        return changes, problems, versions

    @tasks.loop(hours=24)
    async def refresh_schedule(self) -> None:
        try:
            changes, _, versions = await self.update_schedule()
        except Exception:  # an unhandled error would stop this loop for good
            log.exception("Daily schedule check failed")
            return
        if not changes:
            return
        text = _fit(
            "📅 **aion2hub updated its schedule**, so these times changed:\n"
            + "\n".join(f"• {c.line()}" for c in changes)
            + (f"\n-# Source data: {', '.join(versions)}" if versions else "")
        )
        for cfg in self.storage.all_configs():
            channel = self.get_channel(cfg.channel_id)
            if channel is None:
                continue
            try:
                await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                log.exception("Could not post schedule update in guild %s", cfg.guild_id)

    @refresh_schedule.before_loop
    async def before_refresh(self) -> None:
        await self.wait_until_ready()

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Buttons that must keep working after a restart: role menu and field boss Killed."""
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = (interaction.data or {}).get("custom_id", "")
        if not interaction.guild_id:
            return
        if custom_id.startswith(views.ROLE_PREFIX):
            role_id = custom_id[len(views.ROLE_PREFIX):]
            allowed = set(_ping_role_ids(self, interaction.guild_id))
            await views.toggle_role(interaction, int(role_id) if role_id.isdigit() else 0, allowed)
        elif custom_id.startswith(views.KILLED_PREFIX):
            boss, zone = views.parse_killed(custom_id)
            await self._boss_killed(interaction, fieldboss.clean_name(boss), fieldboss.clean_name(zone))

    async def _boss_killed(self, interaction: discord.Interaction, boss: str, zone: str = "") -> None:
        gid = interaction.guild_id
        if not boss:
            return
        known = self.storage.respawn_minutes(gid, boss)

        async def start_timer(inter: discord.Interaction, minutes: int) -> None:
            cfg = _config(self, gid)
            spawn = datetime.now(timezone.utc) + timedelta(minutes=minutes)
            self.storage.set_field_timer(gid, boss, zone, int(spawn.timestamp()))
            await inter.response.send_message(
                f"✅ **{boss}** killed by {inter.user.mention}. Next spawn <t:{int(spawn.timestamp())}:R>, "
                f"{format_time(spawn, cfg.region, cfg.time_display)}.",
                allowed_mentions=discord.AllowedMentions.none(),
            )

        if known:
            await start_timer(interaction, known)
            return

        async def submitted(inter: discord.Interaction, text: str) -> None:
            minutes = fieldboss.parse_duration_minutes(text)
            if minutes is None:
                await inter.response.send_message("I couldn't read that. Try `2h` or `1h 30m`.", ephemeral=True)
                return
            self.storage.set_respawn_minutes(gid, boss, minutes)
            await start_timer(inter, minutes)

        await interaction.response.send_modal(views.RespawnModal(boss, submitted))

    async def on_ready(self) -> None:
        log.info("Logged in as %s in %d server(s)", self.user, len(self.guilds))

    @tasks.loop(seconds=20)
    async def check_events(self) -> None:
        now = datetime.now(timezone.utc)
        health.beat()
        for cfg in self.storage.all_configs():
            try:
                await self._check_guild(cfg, now)
            except Exception:
                log.exception("Check failed for guild %s", cfg.guild_id)
        try:
            await self._delete_old_pings(now)
            self.storage.prune_field_timers(int((now - timedelta(hours=1)).timestamp()))
            self.storage.prune_sent(int((now - timedelta(days=2)).timestamp()))
        except Exception:  # an unhandled error would stop the check loop for good
            log.exception("Cleanup failed")

    def _target(self, cfg: GuildConfig, category: str) -> tuple[int | None, int | None]:
        """(channel_id, role_id) for a category: its own route if set, else the server defaults."""
        channel_id, role_id = self.storage.route(cfg.guild_id, category)
        return channel_id or cfg.channel_id, role_id or cfg.role_id

    def _messages(self, cfg: GuildConfig, now: datetime) -> list[Outgoing]:
        """Everything that should be posted for this server right now."""
        gid = cfg.guild_id
        overrides = self.storage.lead_overrides(gid)
        followed = self.storage.followed(gid)
        out: list[Outgoing] = []

        def add(keys, category, start, text_for_role, view=None):
            pending = [k for k in keys if not self.storage.was_sent(gid, *k)]
            if not pending:
                return
            if in_quiet_hours(cfg, start):
                for k in pending:  # skipped, not delayed: mark so it isn't posted late
                    self.storage.mark_sent(gid, *k)
                return
            channel_id, role_id = self._target(cfg, category)
            delete_at = int(start.timestamp()) + cfg.delete_after * 60 if cfg.delete_after is not None else None
            out.append(Outgoing(pending, channel_id, text_for_role(role_id), view, delete_at, role_id))

        # Advance pings, one message per start time and category
        groups: dict[tuple[datetime, str], list] = {}
        for event, start in due(cfg, followed, now, overrides):
            groups.setdefault((start, event.category), []).append(event)
        for (start, category), events in groups.items():
            keys = [(e.key, int(start.timestamp())) for e in events]
            add(keys, category, start, lambda r, ev=events, st=start: format_ping(ev, st, cfg, r))

        field = due_field(cfg, self.storage.field_timers(gid), now, overrides)
        for boss, zone, start in field:
            add([(f"field:{boss.lower()}|{zone.lower()}", int(start.timestamp()))], "field", start,
                lambda r, b=boss, z=zone, st=start: format_field_ping(b, z, st, cfg, r), views.killed_view(boss, zone))

        if cfg.start_ping:
            starting = replace(cfg, lead_minutes=0)
            groups = {}
            for event, start in due(starting, followed, now, {}):
                groups.setdefault((start, event.category), []).append(event)
            for (start, category), events in groups.items():
                keys = [(f"start:{e.key}", int(start.timestamp())) for e in events]
                add(keys, category, start, lambda r, ev=events: format_start_ping([e.name for e in ev], cfg, r))
            for boss, zone, start in due_field(starting, self.storage.field_timers(gid), now, {FIELD_LEAD_KEY: 0}):
                add([(f"start:field:{boss.lower()}|{zone.lower()}", int(start.timestamp()))], "field", start,
                    lambda r, b=boss: format_start_ping([b], cfg, r), views.killed_view(boss, zone))

        if cfg.digest:
            reset = next_reset(cfg, now - timedelta(minutes=10))
            if reset is not None and reset <= now:
                key = ("digest", int(reset.timestamp()))
                if not self.storage.was_sent(gid, *key):
                    text = format_digest(cfg, followed, self.storage.field_timers(gid), now)
                    out.append(Outgoing([key], cfg.channel_id, text, None, None, None))
        return out

    async def _check_guild(self, cfg: GuildConfig, now: datetime) -> None:
        guild = self.get_guild(cfg.guild_id)
        if guild is None:
            return  # the bot was removed from this server
        messages = self._messages(cfg, now)
        if not messages:
            return
        for msg in messages:
            channel = guild.get_channel(msg.channel_id)
            if channel is None:
                if msg.channel_id is None:
                    continue
                await self._warn_admins(
                    guild, f"channel_missing:{msg.channel_id}",
                    f"I can't find an announcement channel I was set up with in **{guild.name}**, "
                    "so some pings aren't being posted. Run `/setup` or `/route` to pick a channel.",
                )
                continue
            if not channel.permissions_for(guild.me).send_messages:
                await self._warn_admins(
                    guild, f"no_permission:{channel.id}",
                    f"I don't have permission to send messages in {channel.mention} on **{guild.name}**, "
                    "so pings there aren't being posted. Give me View Channel and Send Messages there.",
                )
                continue
            # Mark first so a slow send can't be posted twice by the next check;
            # undo the mark if the send fails, so the next check retries it.
            for key, ts in msg.keys:
                self.storage.mark_sent(cfg.guild_id, key, ts)
            try:
                kwargs = {"view": msg.view} if msg.view is not None else {}
                posted = await channel.send(
                    msg.text,
                    allowed_mentions=_only_role(msg.role_id),
                    **kwargs,
                )
            except discord.Forbidden:
                for key, ts in msg.keys:
                    self.storage.unmark_sent(cfg.guild_id, key, ts)
                await self._warn_admins(
                    guild, f"no_permission:{channel.id}",
                    f"Discord refused my message in {channel.mention} on **{guild.name}**, "
                    "so pings there aren't being posted. Check my permissions in that channel.",
                )
                continue
            except discord.HTTPException as e:
                if 400 <= e.status < 500 and e.status != 429:
                    # The message itself was rejected; retrying would fail the same way.
                    log.error("Ping in guild %s rejected (%s %s): %s", cfg.guild_id, e.status, e.text, [k for k, _ in msg.keys])
                    continue
                for key, ts in msg.keys:
                    self.storage.unmark_sent(cfg.guild_id, key, ts)
                log.warning("Ping in guild %s failed (%s); will retry: %s", cfg.guild_id, e.status, [k for k, _ in msg.keys])
                continue
            if msg.delete_at is not None and posted is not None:
                self.storage.add_posted(channel.id, posted.id, msg.delete_at)
            self._clear_warning(guild.id, channel.id)

    async def _delete_old_pings(self, now: datetime) -> None:
        for channel_id, message_id in self.storage.posted_due(int(now.timestamp())):
            channel = self.get_channel(channel_id)
            if channel is not None:
                try:
                    await channel.get_partial_message(message_id).delete()
                except discord.NotFound:
                    pass
                except discord.HTTPException:
                    log.warning("Couldn't delete old ping %s in channel %s", message_id, channel_id)
            self.storage.remove_posted(channel_id, message_id)

    async def _warn_admins(self, guild: discord.Guild, problem: str, text: str) -> None:
        """Tell the server's admins about a problem once, until it's fixed."""
        meta_key = f"warned:{guild.id}:{problem}"
        if self.storage.get_meta(meta_key):
            return
        log.warning("Guild %s: %s", guild.id, problem)
        text = f"⚠️ **Aion 2 Event Bot:** {text}"
        system = guild.system_channel
        delivered = False
        if system is not None and system.permissions_for(guild.me).send_messages:
            try:
                await system.send(text, allowed_mentions=discord.AllowedMentions.none())
                delivered = True
            except discord.HTTPException:
                pass
        if not delivered:
            try:
                owner = guild.owner or await self.fetch_user(guild.owner_id)
                await owner.send(text)
                delivered = True
            except discord.HTTPException:
                log.warning("Couldn't warn the admins of guild %s", guild.id)
        if delivered:
            self.storage.set_meta(meta_key, "1")

    def _clear_warning(self, guild_id: int, channel_id: int) -> None:
        """A post in this channel worked, so warn again if it breaks later."""
        for problem in (f"no_permission:{channel_id}", f"channel_missing:{channel_id}"):
            if self.storage.get_meta(f"warned:{guild_id}:{problem}"):
                self.storage.set_meta(f"warned:{guild_id}:{problem}", "")

    @check_events.before_loop
    async def before_check(self) -> None:
        await self.wait_until_ready()


def _only_role(role_id: int | None) -> discord.AllowedMentions:
    """Allow pinging just this role. Boss and zone names come from users, so they must never
    be able to trigger @everyone, other roles or user mentions."""
    roles = [discord.Object(id=role_id)] if role_id else False
    return discord.AllowedMentions(everyone=False, users=False, roles=roles, replied_user=False)


def _ping_role_ids(bot: "EventBot", guild_id: int) -> list[int]:
    """The /setup role and any /route roles: the only roles the Notify-me buttons may change."""
    cfg = _config(bot, guild_id)
    ids = [cfg.role_id] + [r for _, r in bot.storage.routes(guild_id).values()]
    return [i for i in dict.fromkeys(ids) if i]


@dataclass
class Outgoing:
    keys: list[tuple[str, int]]  # (sent key, start timestamp) this message covers
    channel_id: int | None
    text: str
    view: discord.ui.View | None
    delete_at: int | None
    role_id: int | None  # the only thing this message may mention


def _fit(text: str, limit: int = 2000) -> str:
    """Cut a message to Discord's length limit at a line break."""
    if len(text) <= limit:
        return text
    return text[: limit - 20].rsplit("\n", 1)[0] + "\n…and more."


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


def _status_embed(bot: EventBot, guild_id: int) -> discord.Embed:
    """Settings and upcoming times. An embed holds up to 4096 characters; a message only 2000."""
    text = _status_text(bot, guild_id)
    if len(text) > 4096:
        text = text[:4000].rsplit("\n", 1)[0] + "\n…"
    return discord.Embed(title="Aion 2 Event Bot", description=text, color=0x5865F2)


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
        f"**Times shown as:** {TIME_DISPLAYS[cfg.time_display]} (change with /time-display)",
        f"**Quiet hours:** {f'{cfg.quiet_start} to {cfg.quiet_end} server time' if cfg.quiet_start else 'off'} (/quiet-hours)",
        f"**Ping at start too:** {'on' if cfg.start_ping else 'off'} (/start-ping)"
        f" · **Daily summary:** {'on' if cfg.digest else 'off'} (/digest)"
        f" · **Auto-delete:** {f'{cfg.delete_after} min after start' if cfg.delete_after is not None else 'off'} (/auto-delete)",
        "",
    ]
    routes = [
        f"**{CATEGORY_LABELS.get(c, c)} go to:** {f'<#{ch}>' if ch else 'the setup channel'}, "
        f"pinging {f'<@&{r}>' if r else 'the setup role'} (/route)"
        for c, (ch, r) in sorted(bot.storage.routes(guild_id).items())
    ]
    lines[2:2] = routes
    followed = set(bot.storage.followed(guild_id))
    overrides = bot.storage.lead_overrides(guild_id)
    for category, title in [("event", "Events"), ("boss", "World bosses")]:
        lines.append(f"**{title}** (✅ = pinged):")
        for event in EVENTS.values():
            if event.category != category:
                continue
            mark = "✅" if event.key in followed else "▫️"
            nxt = next_occurrence(event, cfg.region, now)
            when = (
                f"next <t:{int(nxt.timestamp())}:R>, {format_time(nxt, cfg.region, cfg.time_display)}"
                if nxt
                else "not in this region"
            )
            custom = f" · ping {_minutes(overrides[event.key])}" if event.key in overrides else ""
            lines.append(f"{mark} {event.name}: {when}{custom}")
        lines.append("")
    timers = bot.storage.field_timers(guild_id)
    field_lead = overrides.get(FIELD_LEAD_KEY)
    custom = f" · ping {_minutes(field_lead)}" if field_lead is not None else ""
    lines.append(f"**Field boss timers**{custom} (add with /fieldboss):")
    if not timers:
        lines.append("None yet.")
    for boss, zone, ts in timers:
        start = datetime.fromtimestamp(ts, timezone.utc)
        where = f" ({zone})" if zone else ""
        lines.append(f"⏳ {boss}{where}: <t:{ts}:R>, {format_time(start, cfg.region, cfg.time_display)}")
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
            "Saved. Now pick events with /follow." + warn,
            embed=_status_embed(bot, interaction.guild_id),
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
            f"Following **{event.name}**.", embed=_status_embed(bot, interaction.guild_id), ephemeral=True
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
            f"Stopped **{event.name}**.", embed=_status_embed(bot, interaction.guild_id), ephemeral=True
        )

    @bot.tree.command(name="ping-time", description="Set how many minutes before the start to ping.")
    @admin
    @app_commands.guild_only()
    @app_commands.describe(
        minutes="Minutes before start (0 = at start). Leave empty with an event to reset it to the server default.",
        event="Only change this event or boss. Leave empty to change the server default.",
    )
    @app_commands.choices(event=PING_TIME_CHOICES)
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
            for key in _expand(event.value) if event.value != FIELD_LEAD_KEY else [FIELD_LEAD_KEY]:
                bot.storage.set_lead_override(interaction.guild_id, key, minutes)
            if minutes is None:
                done = f"**{event.name}** now uses the server default."
            else:
                done = f"**{event.name}** now pings **{_minutes(minutes)}**."
        await interaction.response.send_message(done, embed=_status_embed(bot, interaction.guild_id), ephemeral=True)

    @bot.tree.command(name="time-display", description="Show times in pings as local time, server time, or both.")
    @admin
    @app_commands.guild_only()
    @app_commands.describe(mode="Local = each reader's own timezone; server = the game's server clock")
    @app_commands.choices(mode=TIME_DISPLAY_CHOICES)
    async def time_display(interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
        cfg = _config(bot, interaction.guild_id)
        cfg.time_display = mode.value
        bot.storage.save_config(cfg)
        await interaction.response.send_message(
            f"Pings now show **{mode.name.lower()}**.",
            embed=_status_embed(bot, interaction.guild_id),
            ephemeral=True,
        )

    @bot.tree.command(name="schedule-check", description="Check aion2hub now for changed event and boss times.")
    @admin
    @app_commands.guild_only()
    async def schedule_check(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        changes, problems, versions = await bot.update_schedule()
        if changes:
            lines = ["Updated these times from aion2hub:"] + [f"• {c.line()}" for c in changes]
        else:
            lines = ["No time changes: the bot already matches aion2hub."]
        lines.append(bot.storage.get_meta("schedule_last_read") or "")
        lines += [f"⚠️ {p}" for p in problems]
        if versions:
            lines.append(f"-# Source data: {', '.join(versions)}")
        if not AUTO_UPDATE:
            lines.append("-# The daily automatic check is turned off (SCHEDULE_AUTO_UPDATE=0).")
        await interaction.followup.send(_fit("\n".join(l for l in lines if l)), ephemeral=True)

    field = app_commands.Group(
        name="fieldboss",
        description="Field boss timers from a screenshot or typed in.",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def _timer_lines(cfg: GuildConfig, timers: list[fieldboss.FieldTimer]) -> list[str]:
        return [
            f"• **{t.boss}**{f' ({t.zone})' if t.zone else ''}: <t:{int(t.spawn_at.timestamp())}:R>, "
            f"{format_time(t.spawn_at, cfg.region, cfg.time_display)}"
            for t in timers
        ]

    @field.command(name="screenshot", description="Read field boss timers from a game screenshot.")
    @app_commands.describe(image="Screenshot showing field boss respawn timers")
    async def field_screenshot(interaction: discord.Interaction, image: discord.Attachment) -> None:
        cfg = _config(bot, interaction.guild_id)
        await interaction.response.defer(thinking=True)
        taken_at = interaction.created_at
        try:
            items = await fieldboss.read_screenshot(await image.read(), (image.content_type or "").split(";")[0])
        except fieldboss.ReadError as e:
            await interaction.followup.send(f"⚠️ {e}")
            return
        timers = fieldboss.to_timers(items, cfg.region, taken_at)
        if not timers:
            await interaction.followup.send(
                "I couldn't find any field boss timers in that screenshot. Try a tighter crop, or use /fieldboss add."
            )
            return
        gid = interaction.guild_id

        def save() -> None:
            for t in timers:
                bot.storage.set_field_timer(gid, t.boss, t.zone, int(t.spawn_at.timestamp()))

        lead = bot.storage.lead_overrides(gid).get(FIELD_LEAD_KEY, cfg.lead_minutes)
        view = views.ConfirmTimers(interaction.user.id, timers, save)
        view.message = await interaction.followup.send(
            f"I read {len(timers)} field boss timer(s). Check them, then save, and I'll ping {_minutes(lead)} each spawn:\n"
            + "\n".join(_timer_lines(cfg, timers))
            + "\n-# Not saved yet. Wrong times? Discard, or save and fix one with /fieldboss add.",
            allowed_mentions=discord.AllowedMentions.none(),
            view=view,
            wait=True,
        )

    @field.command(name="add", description="Add a field boss timer by hand.")
    @app_commands.describe(
        boss="Boss name",
        spawns_in="Time left, like 1h 20m, 45m or 1:23:45. Or a server time like: at 21:30",
        zone="Zone (optional)",
        respawn="How long it takes to respawn after a kill, like 2h. Used by the Killed button",
    )
    async def field_add(
        interaction: discord.Interaction, boss: str, spawns_in: str, zone: str = "", respawn: str = ""
    ) -> None:
        cfg = _config(bot, interaction.guild_id)
        spawn = fieldboss.parse_when(spawns_in, cfg.region, interaction.created_at)
        if spawn is None:
            await interaction.response.send_message(
                "I couldn't read that time. Use something like `1h 20m`, `45m`, `1:23:45` or `at 21:30`.",
                ephemeral=True,
            )
            return
        respawn_minutes = None
        if respawn:
            respawn_minutes = fieldboss.parse_duration_minutes(respawn)
            if respawn_minutes is None:
                await interaction.response.send_message(
                    "I couldn't read the respawn time. Use something like `2h` or `1h 30m`.", ephemeral=True
                )
                return
        timer = fieldboss.FieldTimer(fieldboss.clean_name(boss), fieldboss.clean_name(zone), spawn)
        if not timer.boss:
            await interaction.response.send_message("Give the boss a name.", ephemeral=True)
            return
        bot.storage.set_field_timer(interaction.guild_id, timer.boss, timer.zone, int(spawn.timestamp()))
        extra = ""
        if respawn_minutes:
            bot.storage.set_respawn_minutes(interaction.guild_id, timer.boss, respawn_minutes)
            extra = f"\n-# Respawn time saved: {views.respawn_text(respawn_minutes)}. The Killed button will use it."
        await interaction.response.send_message(
            "Timer added:\n" + _timer_lines(cfg, [timer])[0] + extra, allowed_mentions=discord.AllowedMentions.none()
        )

    @field.command(name="remove", description="Remove a field boss timer.")
    @app_commands.describe(boss="Boss name, as shown in /events")
    async def field_remove(interaction: discord.Interaction, boss: str) -> None:
        name, sep, zone = boss.partition("|")
        removed = bot.storage.remove_field_timer(interaction.guild_id, name.strip(), zone.strip() if sep else None)
        boss = f"{name.strip()} ({zone.strip()})" if sep and zone.strip() else name.strip()
        await interaction.response.send_message(
            f"Removed **{boss}**." if removed else f"No timer for **{boss}**. See /events for the list.",
            ephemeral=True,
        )

    @field_remove.autocomplete("boss")
    async def field_remove_names(interaction: discord.Interaction, current: str):
        timers = bot.storage.field_timers(interaction.guild_id)
        return [
            app_commands.Choice(name=f"{b} ({z})" if z else b, value=f"{b}|{z}")
            for b, z, _ in timers
            if current.lower() in f"{b} {z}".lower()
        ][:25]

    @field_add.autocomplete("boss")
    async def field_add_names(interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=n, value=n)
            for n in fieldboss.KNOWN_FIELD_BOSSES
            if current.lower() in n.lower()
        ][:25]

    bot.tree.add_command(field)

    @bot.tree.command(name="route", description="Send one category's pings to its own channel and role.")
    @admin
    @app_commands.guild_only()
    @app_commands.describe(
        category="Which pings",
        channel="Channel for these pings (leave empty to use the /setup channel)",
        role="Role to ping (leave empty to use the /setup role)",
    )
    @app_commands.choices(category=CATEGORY_CHOICES)
    async def route(
        interaction: discord.Interaction,
        category: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
        role: discord.Role | None = None,
    ) -> None:
        bot.storage.set_route(
            interaction.guild_id, category.value, channel.id if channel else None, role.id if role else None
        )
        if channel is None and role is None:
            done = f"**{category.name}** now use the /setup channel and role."
        else:
            done = f"**{category.name}** now go to {channel.mention if channel else 'the /setup channel'}" \
                   f" and ping {role.mention if role else 'the /setup role'}."
        await interaction.response.send_message(done, embed=_status_embed(bot, interaction.guild_id), ephemeral=True)

    @bot.tree.command(name="role-menu", description="Post buttons so members can turn ping roles on or off themselves.")
    @admin
    @app_commands.guild_only()
    async def role_menu(interaction: discord.Interaction) -> None:
        roles = [r for r in map(interaction.guild.get_role, _ping_role_ids(bot, interaction.guild_id)) if r]
        if not roles:
            await interaction.response.send_message("Set a role with /setup or /route first.", ephemeral=True)
            return
        me = interaction.guild.me
        if not me.guild_permissions.manage_roles or any(r >= me.top_role for r in roles):
            warn = "\n⚠️ I need **Manage Roles**, and my role must be above these roles, for the buttons to work."
        else:
            warn = ""
        try:
            await interaction.channel.send(
                "**Get pinged for Aion 2 events and bosses.** Tap a button to turn a ping role on or off.",
                view=views.role_menu_view(roles),
            )
        except discord.HTTPException:
            await interaction.response.send_message("I can't post in this channel. Check my permissions here.", ephemeral=True)
            return
        await interaction.response.send_message("Posted the role buttons." + warn, ephemeral=True)

    @bot.tree.command(name="quiet-hours", description="Don't ping for anything starting between these server times.")
    @admin
    @app_commands.guild_only()
    @app_commands.describe(
        start="Start of quiet hours, server time, like 02:00. Leave both empty to turn quiet hours off",
        end="End of quiet hours, server time, like 08:00",
    )
    async def quiet_hours(interaction: discord.Interaction, start: str = "", end: str = "") -> None:
        cfg = _config(bot, interaction.guild_id)
        if not start and not end:
            cfg.quiet_start = cfg.quiet_end = None
            done = "Quiet hours are off."
        else:
            qs, qe = parse_clock(start), parse_clock(end)
            if qs is None or qe is None or qs == qe:
                await interaction.response.send_message(
                    "Give two different times like `02:00` and `08:00` (24-hour, server time).", ephemeral=True
                )
                return
            cfg.quiet_start, cfg.quiet_end = qs, qe
            done = f"No pings for anything starting between **{qs}** and **{qe}** server time."
        bot.storage.save_config(cfg)
        await interaction.response.send_message(done, embed=_status_embed(bot, interaction.guild_id), ephemeral=True)

    @bot.tree.command(name="start-ping", description="Also ping when each event or boss starts.")
    @admin
    @app_commands.guild_only()
    async def start_ping(interaction: discord.Interaction, enabled: bool) -> None:
        cfg = _config(bot, interaction.guild_id)
        cfg.start_ping = enabled
        bot.storage.save_config(cfg)
        done = "I'll also ping when things start." if enabled else "I'll only ping before things start."
        await interaction.response.send_message(done, ephemeral=True)

    @bot.tree.command(name="digest", description="Post a summary of the day's events and bosses after the daily reset.")
    @admin
    @app_commands.guild_only()
    async def digest(interaction: discord.Interaction, enabled: bool) -> None:
        cfg = _config(bot, interaction.guild_id)
        cfg.digest = enabled
        bot.storage.save_config(cfg)
        if enabled:
            reset = next_reset(cfg, datetime.now(timezone.utc))
            when = f" The next one is <t:{int(reset.timestamp())}:R>." if reset else ""
            done = "I'll post the day's schedule right after each daily reset, without pinging anyone." + when
        else:
            done = "Daily summary is off."
        await interaction.response.send_message(done, ephemeral=True)

    @bot.tree.command(name="auto-delete", description="Delete pings a while after the event starts, to keep the channel tidy.")
    @admin
    @app_commands.guild_only()
    @app_commands.describe(minutes="Minutes after the start to delete the ping. Leave empty to keep pings")
    async def auto_delete(
        interaction: discord.Interaction, minutes: app_commands.Range[int, 0, 1440] | None = None
    ) -> None:
        cfg = _config(bot, interaction.guild_id)
        cfg.delete_after = minutes
        bot.storage.save_config(cfg)
        done = "Pings are kept." if minutes is None else f"Pings are deleted {minutes} min after the start."
        await interaction.response.send_message(done, ephemeral=True)

    @bot.tree.command(description="Show settings, followed events and when each is next.")
    @app_commands.guild_only()
    async def events(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=_status_embed(bot, interaction.guild_id), ephemeral=True)

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
            "🧪 Test announcement\n" + format_ping([event], start, cfg),
            allowed_mentions=_only_role(cfg.role_id),
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
