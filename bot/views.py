"""Buttons and pop-up forms: role self-service, field boss "killed" and screenshot confirm."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta

import discord

from . import fieldboss

log = logging.getLogger("aion2bot.views")

ROLE_PREFIX = "aion2:role:"
KILLED_PREFIX = "aion2:killed:"


def role_menu_view(roles: list[discord.Role]) -> discord.ui.View:
    """Buttons that add or remove each ping role. Handled by handle_component, so they survive restarts."""
    view = discord.ui.View(timeout=None)
    for role in roles[:25]:
        view.add_item(discord.ui.Button(
            label=f"🔔 {role.name}"[:80], style=discord.ButtonStyle.secondary, custom_id=f"{ROLE_PREFIX}{role.id}",
        ))
    return view


def killed_view(boss: str, zone: str) -> discord.ui.View:
    """A "Killed" button under a field boss ping, to start its next respawn timer."""
    view = discord.ui.View(timeout=1)  # clicks are handled by EventBot.on_interaction, not this object
    view.add_item(discord.ui.Button(
        label="Killed: start respawn timer", emoji="✅", style=discord.ButtonStyle.success,
        custom_id=f"{KILLED_PREFIX}{boss}|{zone}",
    ))
    return view


def parse_killed(custom_id: str) -> tuple[str, str]:
    boss, _, zone = custom_id[len(KILLED_PREFIX):].partition("|")
    return boss, zone


class RespawnModal(discord.ui.Modal, title="Field boss killed"):
    respawn = discord.ui.TextInput(label="Respawns in", placeholder="e.g. 2h, 90m, 1h 30m", max_length=20)

    def __init__(self, boss: str, on_submit: Callable[[discord.Interaction, str], Awaitable[None]]):
        super().__init__()
        self.boss = boss
        self.respawn.label = f"{boss} respawns in"[:45]
        self._on_submit = on_submit

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_submit(interaction, str(self.respawn.value))


async def toggle_role(interaction: discord.Interaction, role_id: int, allowed: set[int]) -> None:
    """Add or remove a ping role. Only roles the bot is set up to ping can be toggled."""
    guild = interaction.guild
    if role_id not in allowed:
        await interaction.response.send_message("That button is out of date. Ask an admin to run /role-menu again.", ephemeral=True)
        return
    role = guild.get_role(role_id) if guild else None
    member = interaction.user
    if role is None or not isinstance(member, discord.Member):
        await interaction.response.send_message("That role no longer exists.", ephemeral=True)
        return
    me = guild.me
    if not me.guild_permissions.manage_roles or role >= me.top_role:
        await interaction.response.send_message(
            "I can't change that role. An admin needs to give me **Manage Roles** and move my role above it.",
            ephemeral=True,
        )
        return
    try:
        if role in member.roles:
            await member.remove_roles(role, reason="Notify me button")
            text = f"You won't be pinged for {role.mention} any more."
        else:
            await member.add_roles(role, reason="Notify me button")
            text = f"You'll now be pinged for {role.mention}."
    except discord.HTTPException:
        text = "Discord didn't let me change your roles. Ask an admin to check my permissions."
    await interaction.response.send_message(text, ephemeral=True)


class ConfirmTimers(discord.ui.View):
    """Save or discard the timers read from a screenshot."""

    def __init__(self, author_id: int, timers: list[fieldboss.FieldTimer], save: Callable[[], None]):
        super().__init__(timeout=600)
        self.author_id = author_id
        self.timers = timers
        self._save = save
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        perms = getattr(interaction.user, "guild_permissions", None)
        if interaction.user.id == self.author_id or (perms and perms.manage_guild):
            return True
        await interaction.response.send_message("Only the person who posted the screenshot can confirm it.", ephemeral=True)
        return False

    async def _finish(self, interaction: discord.Interaction, footer: str) -> None:
        self.stop()
        content = interaction.message.content.rsplit("\n-# ", 1)[0] + f"\n-# {footer}"
        await interaction.response.edit_message(content=content, view=None)

    @discord.ui.button(label="Save timers", style=discord.ButtonStyle.success, emoji="✅")
    async def save(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self._save()
        await self._finish(interaction, f"✅ Saved by {interaction.user.display_name}. Fix one with /fieldboss add.")

    @discord.ui.button(label="Discard", style=discord.ButtonStyle.secondary)
    async def discard(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._finish(interaction, f"Discarded by {interaction.user.display_name}. Nothing was saved.")

    async def on_timeout(self) -> None:
        if self.message is not None:
            try:
                content = self.message.content.rsplit("\n-# ", 1)[0] + "\n-# Not confirmed in time, so nothing was saved."
                await self.message.edit(content=content, view=None)
            except discord.HTTPException:
                pass


def respawn_text(minutes: int) -> str:
    td = timedelta(minutes=minutes)
    h, m = divmod(int(td.total_seconds()) // 60, 60)
    return f"{h}h {m}m" if h and m else f"{h}h" if h else f"{m}m"
