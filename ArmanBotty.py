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
    return current_prefix
bot = commands.Bot(command_prefix=get_prefix, intents=intents, help_command=None)

CONFIG_FILE = "player_config.json"
player_data = {}
lobby_players = []  # list of (user_id, name, mmr)
lobby_message = None
roll_count = 0
MAX_ROLLS = 5
team_rolls = []
original_teams = None
current_password = "penguin"
current_prefix = "!"

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

@tasks.loop(hours=24)
async def refresh_all_mmrs():
    print("Refreshing MMRs...")
    for user_id, info in player_data.items():
        if isinstance(info, dict) and "steam_id" in info:
            steam_id = info["steam_id"]
            mmr, season_rank = fetch_mmr_from_stratz(steam_id)
            if mmr:
                info["mmr"] = mmr
                info["seasonRank"] = season_rank
    save_config(player_data)
    await update_lobby_embed()

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

@bot.command(name="set_mmr")
@commands.has_permissions(administrator=True)
async def set_mmr(ctx, mmr: int, member: discord.Member):
    user_id = str(member.id)
    if user_id not in player_data:
        player_data[user_id] = {}
    player_data[user_id]["mmr"] = mmr
    save_config(player_data)
    await ctx.send(f"{member.mention}'s MMR has been manually set to **{mmr}**.")

@set_mmr.error
async def set_mmr_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You do not have permission to use this command.")

@bot.command(name="add")
async def add_to_lobby(ctx, *members: discord.Member):
    global lobby_players
    added = []
    for member in members:
        if any(uid == member.id for uid, _, _ in lobby_players):
            continue
        mmr = get_mmr(member)
        lobby_players.append((member.id, member.name, mmr))
        added.append(member.display_name)
    if added:
        await update_lobby_embed()
        await ctx.send(f"Added to lobby: {', '.join(added)}")
    else:
        await ctx.send("No new members were added.")

@bot.command(name="remove")
async def remove_from_lobby(ctx, *members: discord.Member):
    global lobby_players
    removed = []
    for member in members:
        for i, (uid, _, _) in enumerate(lobby_players):
            if uid == member.id:
                del lobby_players[i]
                removed.append(member.display_name)
                break
    if removed:
        await update_lobby_embed()
        await ctx.send(f"Removed from lobby: {', '.join(removed)}")
    else:
        await ctx.send("None of the specified members were in the lobby.")

@bot.command(name="lobby")
async def lobby_cmd(ctx):
    global lobby_message
    try:
        await ctx.message.delete()  # Delete the user's command message
    except discord.Forbidden:
        pass  # Bot doesn't have permission to delete messages
    if lobby_message:
        try:
            await lobby_message.delete()
        except discord.NotFound:
            pass
    embed = build_lobby_embed()
    lobby_message = await ctx.send(embed=embed)
    await lobby_message.add_reaction("👍")
    await lobby_message.add_reaction("👎")
    if len(lobby_players) == 10:
        await lobby_message.add_reaction("🚀")

@bot.command(name="reset")
async def reset(ctx):
    global lobby_players, lobby_message
    lobby_players.clear()
    if lobby_message:
        try:
            await lobby_message.delete()
        except discord.NotFound:
            pass
    embed = build_lobby_embed()
    lobby_message = await ctx.send(embed=embed)
    await lobby_message.add_reaction("👍")
    await lobby_message.add_reaction("👎")
    await ctx.send("Lobby has been cleared and refreshed.")

@bot.command(name="alert")
@commands.has_permissions(administrator=True)
async def alert(ctx):
    if len(lobby_players) != 10:
        await ctx.send("We do not have 10 players in the lobby yet.")
        return

    guild = ctx.guild
    mentions = []

    for user_id, _, _ in lobby_players:
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
    global current_password
    current_password = new_password
    await update_lobby_embed()
    await ctx.send(f"Password updated to: `{new_password}`")

@set_password.error
async def set_password_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You do not have permission to use this command.")

@bot.command(name="help")
async def help_command(ctx):
    help_text = (
        "**Available Commands:**\n\n"
        "**!cfg `<steam_id>`** – Link your Steam ID to fetch your MMR from STRATZ.\n"
        "**!mmr [@user]** – Show your MMR or another user's MMR.\n"
        "**!lobby** – Create or refresh the inhouse lobby.\n"
        "**!reset** – Clear the current lobby and start fresh.\n"
        "**!add @user1 @user2 ...** – Manually add one or more users to the lobby.\n"
        "**!remove @user1 @user2 ...** – Manually remove one or more users from the lobby.\n"
        "**!set_mmr @user `<mmr>`** – (Admin only) Manually set a user's MMR.\n"
        "**!setpassword `<new_password>`** – (Admin only) Change the inhouse lobby password.\n"
        "**!changeprefix `<new_prefix>`** – (Admin only) Changes the prefix of the bot commands.\n"
        "**👍 / 👎 Reactions** – Join or leave the lobby.\n"
        "**🚀 Reaction** – Generate balanced teams when lobby is full.\n"
        "**♻️ Reaction** – Re-roll teams (up to 5 times).\n"
    )
    await ctx.send(help_text)

@bot.command(name="changeprefix")
@commands.has_permissions(administrator=True)
async def change_prefix(ctx, new_prefix: str):
    global current_prefix
    current_prefix = new_prefix
    await ctx.send(f"Command prefix changed to `{new_prefix}`")

@change_prefix.error
async def change_prefix_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You do not have permission to change the prefix.")

# ---------- Events ----------
@bot.event
async def on_ready():
    global player_data
    player_data = load_config()
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
    global lobby_players, roll_count, team_rolls, original_teams

    if payload.user_id == bot.user.id or payload.message_id != getattr(lobby_message, "id", None):
        return

    guild = bot.get_guild(payload.guild_id)
    user = guild.get_member(payload.user_id)
    if user is None:
        return

    channel = bot.get_channel(payload.channel_id)
    message = await channel.fetch_message(payload.message_id)

    emoji = str(payload.emoji)
    updated = False

    if emoji == "👍":
        if not any(uid == user.id for uid, _, _ in lobby_players):
            mmr = get_mmr(user)
            lobby_players.append((user.id, user.name, mmr))
            updated = True
    
    elif emoji == "👎":
        was_full = len(lobby_players) == 10

        for i, (uid, _, _) in enumerate(lobby_players):
            if uid == user.id:
                del lobby_players[i]
                updated = True
                if was_full and len(lobby_players) == 9:
                    await channel.send(f"Wow, so nice of you to leave at 9/10, {user.mention}")
                break

        # Remove 🚀 and ♻️ if lobby drops from 10 to 9
        if was_full and len(lobby_players) == 9:
            for reaction in message.reactions:
                if str(reaction.emoji) in ["🚀", "♻️"]:
                    await message.clear_reaction(reaction.emoji)

    elif emoji == "🚀" and len(lobby_players) == 10:
        team_rolls = calculate_balanced_teams(lobby_players)
        original_teams = team_rolls[0]
        roll_count = 1
        embed = build_team_embed(*original_teams)
        await message.edit(embed=embed)
        await message.clear_reactions()
        await message.add_reaction("👍")
        await message.add_reaction("👎")
        await message.add_reaction("♻️")
        await message.remove_reaction(payload.emoji, user)

    elif emoji == "♻️" and len(lobby_players) == 10:
        if not user.guild_permissions.administrator:
            await message.remove_reaction(payload.emoji, user)
            return
        if not team_rolls:
            return
        if roll_count >= MAX_ROLLS:
            roll_count = 1
            embed = build_team_embed(*original_teams)
        else:
            roll_count += 1
            embed = build_team_embed(*team_rolls[roll_count - 1])
        await message.edit(embed=embed)
        await message.remove_reaction(payload.emoji, user)

    if updated:
        await update_lobby_embed()

    channel = bot.get_channel(payload.channel_id)
    message = await channel.fetch_message(payload.message_id)
    await message.remove_reaction(payload.emoji, user)

# ---------- Embeds ----------
def build_lobby_embed():
    embed = discord.Embed(
        title="DotA2 Inhouse",
        description=f"({len(lobby_players)}/10)",
        color=discord.Color.purple()
    )
    for _, name, mmr in lobby_players:
        embed.add_field(name=name, value=str(mmr), inline=True)
    embed.add_field(name="**Password**", value=current_password, inline=False)
    return embed

async def update_lobby_embed():
    if lobby_message:
        embed = build_lobby_embed()
        await lobby_message.edit(embed=embed)
        if len(lobby_players) == 10:
            await lobby_message.add_reaction("🚀")

bot.run(TOKEN)