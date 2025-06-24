# ------------------------------------------------------------
# FeederBot - Discord Inhouse Lobby Bot
# Author: Arman Hasan
# Created: June 2025
# Location: Ft. Lauderdale, Florida
# Description: A Discord bot for managing DotA2 inhouse lobbies,
#              including MMR tracking, team balancing, and lobby alerts.
# ------------------------------------------------------------
import os
import json
import discord
import requests
import time
import itertools
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
STRATZ_TOKEN = os.getenv("STRATZ_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.guilds = True
intents.members = True

def get_prefix(bot, message):
    guild_id = message.guild.id if message.guild else None
    return current_prefix.get(guild_id, "!")
bot = commands.Bot(command_prefix=get_prefix, intents=intents, help_command=None)

CONFIG_FILE = "player_config.json"
PREFIX_FILE = "prefixes.json"
player_data = {}
lobby_players = {}         # {guild_id: list of (user_id, name, mmr)}
lobby_message = {}         # {guild_id: message}
roll_count = {}            # {guild_id: int}
team_rolls = {}            # {guild_id: list of team tuples}
original_teams = {}        # {guild_id: team tuple}
current_password = {}      # {guild_id: password string}
current_prefix = {}        # {guild_id: prefix}
MAX_ROLLS = 5

# ---------- MMR Mapping ----------
season_rank_to_mmr = {
    11: 77, 12: 231, 13: 385, 14: 539, 15: 693,
    21: 847, 22: 1001, 23: 1155, 24: 1309, 25: 1463,
    31: 1594, 32: 1749, 33: 1953, 34: 2081, 35: 2208,
    41: 2387, 42: 2541, 43: 2695, 44: 2849, 45: 3003,
    51: 3157, 52: 3311, 53: 3465, 54: 3619, 55: 3772,
    61: 3927, 62: 4081, 63: 4235, 64: 4389, 65: 4542,
    71: 4720, 72: 4920, 73: 5120, 74: 5320, 75: 5520,
    81: 5650, 82: 5650, 83: 5650, 84: 5650, 85: 5650
}

# ---------- Persistent Storage ----------
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

def save_config(data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)

def load_prefixes():
    if os.path.exists(PREFIX_FILE):
        with open(PREFIX_FILE, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

def save_prefixes():
    with open(PREFIX_FILE, "w") as f:
        json.dump(current_prefix, f, indent=2)

# ---------- Steam ID Conversion ----------
def convert_to_steam32(steam_id_str):
    try:
        steam_id = int(steam_id_str.replace(" ", ""))
        if steam_id > 76561197960265728:
            return steam_id - 76561197960265728
        return steam_id
    except ValueError:
        return None

# ---------- MMR Fetching ----------
def fetch_mmr_from_stratz(steam_id, max_retries=5):
    url = "https://api.stratz.com/graphql"
    headers = {
        "Authorization": f"Bearer {STRATZ_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "STRATZ_API"
    }
    query = {
        "query": f"""
        query {{
            player(steamAccountId: {steam_id}) {{
                steamAccount {{
                    seasonRank
                }}
            }}
        }}
        """
    }
    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=query, headers=headers, timeout=5)
            if response.status_code == 200:
                data = response.json()
                season_rank = data["data"]["player"]["steamAccount"]["seasonRank"]
                mmr = season_rank_to_mmr.get(season_rank, None)
                return mmr, season_rank
            elif response.status_code == 429:
                time.sleep(2 ** attempt)
            else:
                return None, None
        except Exception:
            return None, None
    return None, None

def get_mmr(user):
    user_id = str(user.id)
    info = player_data.get(user_id)
    if isinstance(info, dict):
        return info.get("mmr")
    return 4000 + hash(user.name) % 3000

def get_active_user_ids():
    """Return a set of user IDs across all servers the bot is in."""
    user_ids = set()
    for guild in bot.guilds:
        for member in guild.members:
            if not member.bot:
                user_ids.add(str(member.id))
    return user_ids

@tasks.loop(hours=24)
async def refresh_all_mmrs():
    print("Refreshing MMRs...")
    active_ids = get_active_user_ids()
    refreshed = 0
    for user_id in active_ids:
        info = player_data.get(user_id)
        if isinstance(info, dict) and "steam_id" in info:
            mmr, season_rank = fetch_mmr_from_stratz(info["steam_id"])
            if mmr:
                info["mmr"] = mmr
                info["seasonRank"] = season_rank
                refreshed += 1
    save_config(player_data)
    print(f"MMRs refreshed for {refreshed} active users.")
    await update_all_lobbies()

# ---------- Team Calculation ----------
def calculate_balanced_teams(players):
    combinations = list(itertools.combinations(players, 5))
    team_pairs = []
    for team1 in combinations:
        team2 = [p for p in players if p not in team1]
        avg1 = sum(p[2] for p in team1) / 5
        avg2 = sum(p[2] for p in team2) / 5
        diff = abs(avg1 - avg2)
        team_pairs.append((diff, list(team1), team2))
    team_pairs.sort(key=lambda x: x[0])
    return [(t1, t2) for _, t1, t2 in team_pairs]

def build_team_embed(team1, team2):
    global roll_count
    avg1 = sum(p[2] for p in team1) / 5
    avg2 = sum(p[2] for p in team2) / 5
    embed = discord.Embed(
        title="DotA2 Inhouse",
        description=f"(10/10): T1: {int(avg1)}, T2: {int(avg2)}, Roll #{roll_count}/{MAX_ROLLS}",
        color=discord.Color.gold()
    )
    team1_sorted = sorted(team1, key=lambda x: x[2], reverse=True)
    team2_sorted = sorted(team2, key=lambda x: x[2], reverse=True)
    embed.add_field(name="Team One", value=", ".join(f"{p[1]} ({p[2]})" for p in team1_sorted), inline=False)
    embed.add_field(name="Team Two", value=", ".join(f"{p[1]} ({p[2]})" for p in team2_sorted), inline=False)
    embed.add_field(name="**Password**", value=current_password, inline=False)
    return embed

# ---------- Commands ----------
@bot.command(name="cfg")
async def cfg_cmd(ctx, steam_id: str, member: discord.Member = None):
    steam32 = convert_to_steam32(steam_id)
    if steam32 is None:
        await ctx.send("Please provide a valid numeric Steam friend code or Steam ID.")
        return
    target = member or ctx.author
    user_id = str(target.id)
    mmr, season_rank = fetch_mmr_from_stratz(steam32)
    player_data[user_id] = {
        "steam_id": steam32,
        "steam_name": target.name,
        "discord_username": str(target), # Global username
        "discord_nickname": target.nick if target.nick else target.display_name,
        "mmr": mmr,
        "seasonRank": season_rank
    }
    save_config(player_data)
    if mmr:
        await ctx.send(f"{target.mention}, your Steam ID `{steam32}` has been linked with an estimated MMR of **{mmr}**.")
    else:
        await ctx.send(f"{target.mention}, Steam ID linked, but MMR could not be determined.")

@bot.command(name="mmr")
async def mmr_lookup(ctx, member: discord.Member = None):
    user = member or ctx.author
    mmr = get_mmr(user)
    await ctx.send(f"{user.display_name}'s MMR is **{mmr}**.")

@bot.command(name="setmmr")
@commands.has_permissions(administrator=True)
async def setmmr(ctx, mmr: int, member: discord.Member):
    # Safety check
    if member not in ctx.guild.members:
        await ctx.send("That user is not in this server.")
        return
    user_id = str(member.id)
    if user_id not in player_data:
        player_data[user_id] = {}
    player_data[user_id]["mmr"] = mmr
    save_config(player_data)
    await ctx.send(f"{member.mention}'s MMR has been manually set to **{mmr}**.")


@setmmr.error
async def set_mmr_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You do not have permission to use this command.")

@bot.command(name="add")
async def add_to_lobby(ctx, *members: discord.Member):
    guild_id = ctx.guild.id

    # Initialize lobby for this guild if not already present
    if guild_id not in lobby_players:
        lobby_players[guild_id] = []
    added = []
    for member in members:
        if any(uid == member.id for uid, _, _ in lobby_players[guild_id]):
            continue
        mmr = get_mmr(member)
        lobby_players[guild_id].append((member.id, member.name, mmr))
        added.append(member.display_name)
    if added:
        await update_lobby_embed(ctx.guild)
        await ctx.send(f"Added to lobby: {', '.join(added)}")
    else:
        await ctx.send("No new members were added.")

@bot.command(name="remove")
async def remove_from_lobby(ctx, *members: discord.Member):
    guild_id = ctx.guild.id
    removed = []
    if guild_id not in lobby_players:
        await ctx.send("There is no lobby for this server yet.")
        return
    for member in members:
        for i, (uid, _, _) in enumerate(lobby_players[guild_id]):
            if uid == member.id:
                del lobby_players[guild_id][i]
                removed.append(member.display_name)
                break
    if removed:
        await update_lobby_embed(ctx.guild)
        await ctx.send(f"Removed from lobby: {', '.join(removed)}")
    else:
        await ctx.send("None of the specified members were in the lobby.")

@bot.command(name="lobby")
async def lobby_cmd(ctx):
    guild_id = ctx.guild.id
    # Initialize structures if not already present
    if guild_id not in lobby_players:
        lobby_players[guild_id] = []
    if guild_id not in current_password:
        current_password[guild_id] = "penguin"
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        pass
    # Delete old lobby message if it exists
    if guild_id in lobby_message:
        try:
            await lobby_message[guild_id].delete()
        except discord.NotFound:
            pass
    # Send new lobby message
    embed = build_lobby_embed(ctx.guild)
    message = await ctx.send(embed=embed)
    lobby_message[guild_id] = message
    # Add reactions
    await message.add_reaction("👍")
    await message.add_reaction("👎")
    if len(lobby_players[guild_id]) == 10:
        await message.add_reaction("🚀")

@bot.command(name="reset")
async def reset(ctx):
    guild_id = ctx.guild.id
    lobby_players[guild_id] = []
    try:
        if guild_id in lobby_message:
            await lobby_message[guild_id].delete()
    except discord.NotFound:
        pass
    embed = build_lobby_embed(ctx.guild)
    message = await ctx.send(embed=embed)
    lobby_message[guild_id] = message
    await message.add_reaction("👍")
    await message.add_reaction("👎")
    await ctx.send("Lobby has been cleared and refreshed.")

@bot.command(name="alert")
@commands.has_permissions(administrator=True)
async def alert(ctx):
    guild = ctx.guild
    guild_id = guild.id
    if guild_id not in lobby_players or len(lobby_players[guild_id]) != 10:
        await ctx.send("We do not have 10 players in the lobby yet.")
        return
    mentions = []
    for user_id, _, _ in lobby_players[guild_id]:
        member = guild.get_member(user_id)
        if member:
            mentions.append(member.mention)
    if mentions:
        await ctx.send(f"{' '.join(mentions)} lobby up.")
    else:
        await ctx.send("Could not find any users to alert.")

@alert.error
async def alert_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You do not have permission to use this command.")

@bot.command(name="setpassword")
@commands.has_permissions(administrator=True)
async def set_password(ctx, *, new_password: str):
    guild_id = ctx.guild.id
    current_password[guild_id] = new_password
    await update_lobby_embed(ctx.guild)
    await ctx.send(f"Password updated to: `{new_password}`")

@set_password.error
async def set_password_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You do not have permission to use this command.")

@bot.command(name="help")
async def help_command(ctx):
    help_text = (
        "\n**Available Commands:**\n\n"
        "__**👥 General Commands**__\n"
        "**!cfg `<steam_id>` `<@user>`** - 🔗 Link your Steam ID to fetch your MMR from STRATZ.\n"
        "**!mmr `<@user>`** - 📈 Show your MMR or another user's MMR.\n"
        "**👍 / 👎 Reactions** - Join or leave the lobby.\n"
        "**🚀 Reaction** - Generate balanced teams when lobby is full.\n"
        "**♻️ Reaction** - Re-roll teams (up to 5 times).\n\n"
        "__**🏠 Lobby Management**__\n"
        "**!lobby** - Create or refresh the inhouse lobby.\n"
        "**!reset** - Clear the current lobby and start fresh.\n"
        "**!add `@user1` `@user2` ...** - Manually add one or more users to the lobby.\n"
        "**!remove `@user1` `@user2` ...** - Manually remove one or more users from the lobby.\n\n"
        "__**🔐 Admin Commands**__\n"
        "**!setmmr @user `<mmr>`** - (Admin only) Manually set a user's MMR.\n"
        "**!setpassword `<new_password>`** - (Admin only) Change the inhouse lobby password.\n"
        "**!changeprefix `<new_prefix>`** - (Admin only) Changes the prefix of the bot commands.\n"
        "**!alert** - (Admin only) Mention all 10 players when the lobby is full.\n"
    )
    await ctx.send(help_text)

@bot.command(name="changeprefix")
@commands.has_permissions(administrator=True)
async def change_prefix(ctx, new_prefix: str):
    guild_id = ctx.guild.id
    current_prefix[guild_id] = new_prefix
    save_prefixes()
    await ctx.send(f"Command prefix changed to `{new_prefix}` for this server.")

@change_prefix.error
async def change_prefix_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You do not have permission to change the prefix.")

# ---------- Events ----------
@bot.event
async def on_ready():
    global player_data, current_prefix
    player_data = load_config()
    current_prefix = load_prefixes()
    print(f"{bot.user} is online!")
    refresh_all_mmrs.start()

@bot.event
async def on_message(msg):
    if msg.author.bot:
        return
    if "dota" in msg.content.lower():
        await msg.channel.send(f"Interesting message, {msg.author.mention}")
    await bot.process_commands(msg)

@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return
    guild_id = payload.guild_id
    user_id = payload.user_id
    channel = bot.get_channel(payload.channel_id)
    guild = bot.get_guild(guild_id)
    if guild_id not in lobby_message:
        return
    # Make sure it's the correct message
    if payload.message_id != lobby_message[guild_id].id:
        return
    message = await channel.fetch_message(payload.message_id)
    user = guild.get_member(user_id)
    if user is None:
        return
    emoji = str(payload.emoji)
    updated = False
    # Initialize data if needed
    lobby_players.setdefault(guild_id, [])
    roll_count.setdefault(guild_id, 0)
    team_rolls.setdefault(guild_id, [])
    original_teams.setdefault(guild_id, None)
    if emoji == "👍":
        if not any(uid == user.id for uid, _, _ in lobby_players[guild_id]):
            mmr = get_mmr(user)
            lobby_players[guild_id].append((user.id, user.name, mmr))
            updated = True
    elif emoji == "👎":
        was_full = len(lobby_players[guild_id]) == 10
        for i, (uid, _, _) in enumerate(lobby_players[guild_id]):
            if uid == user.id:
                del lobby_players[guild_id][i]
                updated = True
                if was_full and len(lobby_players[guild_id]) == 9:
                    await channel.send(f"Wow, so nice of you to leave at 9/10, {user.mention}")
                break
        # Remove 🚀 and ♻️ if needed
        if was_full and len(lobby_players[guild_id]) == 9:
            for reaction in message.reactions:
                if str(reaction.emoji) in ["🚀", "♻️"]:
                    await message.clear_reaction(reaction.emoji)
    elif emoji == "🚀" and len(lobby_players[guild_id]) == 10:
        team_rolls[guild_id] = calculate_balanced_teams(lobby_players[guild_id])
        original_teams[guild_id] = team_rolls[guild_id][0]
        roll_count[guild_id] = 1
        embed = build_team_embed(*original_teams[guild_id])
        await message.edit(embed=embed)
        await message.clear_reactions()
        await message.add_reaction("👍")
        await message.add_reaction("👎")
        await message.add_reaction("♻️")
        await message.remove_reaction(payload.emoji, user)
    elif emoji == "♻️" and len(lobby_players[guild_id]) == 10:
        if not user.guild_permissions.administrator:
            await message.remove_reaction(payload.emoji, user)
            return
        if not team_rolls[guild_id]:
            return
        if roll_count[guild_id] >= MAX_ROLLS:
            roll_count[guild_id] = 1
            embed = build_team_embed(*original_teams[guild_id])
        else:
            roll_count[guild_id] += 1
            embed = build_team_embed(*team_rolls[guild_id][roll_count[guild_id] - 1])
        await message.edit(embed=embed)
        await message.remove_reaction(payload.emoji, user)
    if updated:
        await update_lobby_embed(guild)
    # Always remove the user's reaction
    await message.remove_reaction(payload.emoji, user)

@bot.event
async def on_guild_join(guild):
    # Try to find a text channel where the bot can send messages
    for channel in guild.text_channels:
        if channel.permissions_for(guild.me).send_messages:
            embed = discord.Embed(
                title="👋 Thanks for adding FeederBot!",
                description=(
                    "**Here's how to get started:**\n\n"
                    "🛠 Use `!lobby` to create a lobby embed\n"
                    "💡 Use `!help` to see all available commands\n"
                    "🔐 Admins can use `!changeprefix`, `!setpassword`, `!alert`, etc.\n\n"
                    "Happy inhousing!"
                ),
                color=discord.Color.green()
            )
            embed.set_footer(text="Need help? Use !help or contact the bot creator.")
            await channel.send(embed=embed)
            break

# ---------- Embeds ----------
def build_lobby_embed(guild):
    guild_id = guild.id
    embed = discord.Embed(
        title="DotA2 Inhouse",
        description=f"({len(lobby_players.get(guild_id, []))}/10)",
        color=discord.Color.purple()
    )
    for _, name, mmr in lobby_players.get(guild_id, []):
        embed.add_field(name=name, value=str(mmr), inline=True)
    password = current_password.get(guild_id, "penguin")
    embed.add_field(name="**Password**", value=password, inline=False)
    return embed

async def update_lobby_embed(guild):
    guild_id = guild.id
    if guild_id not in lobby_players or guild_id not in lobby_message:
        return
    embed = build_lobby_embed(guild)
    message = lobby_message[guild_id]
    await message.edit(embed=embed)
    if len(lobby_players[guild_id]) == 10:
        await message.add_reaction("🚀")

async def update_all_lobbies():
    for guild in bot.guilds:
        await update_lobby_embed(guild)

bot.run(TOKEN)