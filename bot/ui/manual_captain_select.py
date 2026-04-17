import discord
from discord import ui

from bot.state.runtime_state import captain_draft_state, immortal_draft_running, lobby_message, original_teams


find_lobby_tuple = None
is_placeholder_player = None
build_immortal_embed = None


def configure_manual_captain_select(*, find_lobby_tuple_fn, is_placeholder_player_fn, build_immortal_embed_fn):
    global find_lobby_tuple, is_placeholder_player, build_immortal_embed
    find_lobby_tuple = find_lobby_tuple_fn
    is_placeholder_player = is_placeholder_player_fn
    build_immortal_embed = build_immortal_embed_fn


class ManualCaptainSelectView(ui.View):
    def __init__(self, guild: discord.Guild, chooser: discord.Member, players: list[tuple[int, str, int]]):
        super().__init__(timeout=120)
        self.guild = guild
        self.chooser = chooser
        self.players = players
        self.selected: list[int] = []
        real_players = [p for p in players if not is_placeholder_player(p[0])]
        for i, (uid, name, mmr) in enumerate(real_players):
            row = i // 5
            label = f"{name} · {mmr}"
            self.add_item(CaptainSelectButton(uid, label, row=row))

    async def on_timeout(self):
        for child in self.children:
            if isinstance(child, ui.Button):
                child.disabled = True
        try:
            if self.message:
                await self.message.edit(view=self)
                await self.message.channel.send("Manual captain selection timed out - press 🎯 to try again.")
        except Exception:
            pass


class CaptainSelectButton(ui.Button):
    def __init__(self, user_id: int, label: str, row: int | None = None):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=row)
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        view: ManualCaptainSelectView = self.view  # type: ignore
        if interaction.user.id != view.chooser.id:
            return await interaction.response.send_message("Only the admin who started selection can pick captains.", ephemeral=True)
        if self.user_id in view.selected:
            return await interaction.response.send_message("Already selected.", ephemeral=True)
        view.selected.append(self.user_id)
        self.disabled = True
        self.style = discord.ButtonStyle.success
        await interaction.response.edit_message(view=view)
        if len(view.selected) != 2:
            return

        gid = view.guild.id
        p1 = find_lobby_tuple(gid, view.selected[0])
        p2 = find_lobby_tuple(gid, view.selected[1])
        if not p1 or not p2:
            return await interaction.followup.send("Could not resolve selected captains from the lobby.", ephemeral=True)
        if is_placeholder_player(p1[0]) or is_placeholder_player(p2[0]):
            return await interaction.followup.send("Placeholders cannot be selected as captains.", ephemeral=True)

        pool = [p for p in view.players if p[0] not in (p1[0], p2[0])]
        diff = abs(p1[2] - p2[2])
        captain_draft_state[gid] = {"pairs": [((p1, p2), pool, diff)], "index": 0, "manual": True}
        try:
            embed = build_immortal_embed((p1, p2), pool, view.guild, reroll_count=1)
            msg = lobby_message.get(gid)
            if msg:
                await msg.edit(embed=embed)
                try:
                    await msg.clear_reaction("⚔️")
                except Exception:
                    pass
                await msg.add_reaction("⚔️")
        except Exception as e:
            await interaction.followup.send(f"Failed to update embed: `{e}`", ephemeral=True)
            return

        await interaction.followup.send(
            f"Captains set: <@{p1[0]}> vs <@{p2[0]}>. Press ⚔️ to start the Immortal Draft."
        )
        for child in view.children:
            if isinstance(child, ui.Button):
                child.disabled = True
        try:
            await interaction.edit_original_response(view=view)
        except Exception:
            pass


async def handle_immortal_draft_cancel(guild_id: int):
    immortal_draft_running[guild_id] = False
    captain_draft_state.pop(guild_id, None)
    original_teams.pop(guild_id, None)
    msg = lobby_message.get(guild_id)
    if msg:
        try:
            await msg.add_reaction("⚔️")
        except Exception:
            pass
