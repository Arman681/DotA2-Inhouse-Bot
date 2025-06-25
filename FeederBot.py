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
import firebase_admin
from firebase_admin import credentials, firestore

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
STRATZ_TOKEN = os.getenv("STRATZ_TOKEN")
cred_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
if not cred_json:
    raise ValueError("Missing Firebase credentials!")

cred_dict = json.loads(cred_json)
cred = credentials.Certificate(cred_dict)
firebase_admin.initialize_app(cred)
db = firestore.client()

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.guilds = True
intents.members = True

inhouse_mode = {}          # {guild_id: "regular" or "immortal"}
player_data = {}
lobby_players = {}         # {guild_id: list of (user_id, name, mmr)}
lobby_message = {}         # {guild_id: message}
roll_count = {}            # {guild_id: int}
team_rolls = {}            # {guild_id: list of team tuples}
original_teams = {}        # {guild_id: team tuple}
MAX_ROLLS = 5

# ========================================================================================================================
# ============================================ ⚙️ Core Functions & Utilities ============================================
# ========================================================================================================================

# ============================== 🛠️ Bot Configuration ==============================
# Resolves the correct command prefix for the bot, based on the message's guild.
async def resolve_command_prefix(bot, message):
    guild_id = message.guild.id if message.guild else None
    if guild_id:
        prefix = fetch_guild_prefix(guild_id)
        return prefix
    return "!"  # fallback default
bot = commands.Bot(command_prefix=resolve_command_prefix, intents=intents, help_command=None)

# =============================== 🔐 Permission Checks ===============================
# Custom check that allows admins or specific roles to use commands
def is_admin_or_has_role():
    async def predicate(ctx):
        if ctx.author.guild_permissions.administrator:
            return True
        admin_roles = ["Inhouse Admin", "Drow Picker"]
        return any(role.name in admin_roles for role in ctx.author.roles)
    return commands.check(predicate)

# Utility function version of the role check (returns True/False instead of being a decorator)
async def user_is_admin_or_has_role(member):
    if member.guild_permissions.administrator:
        return True
    allowed_roles = ["Inhouse Admin", "Drow Picker"]
    return any(role.name in allowed_roles for role in member.roles)

# ========================== 🔥 Firestore Access & Persistence ==========================
# Saves a player's config data (Steam info, MMR, etc.) to Firestore under their Discord user ID.
def save_player_config(user_id, data):
    doc_ref = db.collection("players").document(str(user_id))
    doc_ref.set(data)

# Retrieves a player's saved config data from Firestore using their Discord user ID.
def get_player_config(user_id):
    doc = db.collection("players").document(str(user_id)).get()
    return doc.to_dict() if doc.exists else None

# Stores a custom command prefix for a specific Discord server (guild) to Firestore.
def store_guild_prefix(guild_id, prefix, server_name=None, set_by=None):
    data = {
        "prefix": prefix,
        "server_name": server_name,
        "set_by": set_by,
        "timestamp": firestore.SERVER_TIMESTAMP
    }
    doc_ref = db.collection("prefixes").document(str(guild_id))
    doc_ref.set(data, merge=True)

# Retrieves the stored command prefix for a Discord server from Firestore, or "!" if none is set.
def fetch_guild_prefix(guild_id):
    doc = db.collection("prefixes").document(str(guild_id)).get()
    if doc.exists:
        return doc.to_dict().get("prefix", "!")
    return "!"

# Saves the inhouse lobby password for a Discord server (guild) to Firestore.
def save_lobby_password_for_guild(guild_id, password, server_name=None, set_by=None):
    data = {
        "password": password,
        "server_name": server_name,
        "set_by": set_by,
        "timestamp": firestore.SERVER_TIMESTAMP
    }
    doc_ref = db.collection("lobbies").document(str(guild_id))
    doc_ref.set(data, merge=True)

# Loads the saved inhouse lobby password for a guild from Firestore; returns "penguin" if not set.
def load_lobby_password_for_guild(guild_id):
    doc = db.collection("lobbies").document(str(guild_id)).get()
    if doc.exists:
        return doc.to_dict().get("password", "penguin")  # Default if not set
    return "penguin"

def save_inhouse_mode_for_guild(guild_id, mode, set_by):
    db.collection("inhouse_modes").document(str(guild_id)).set({
        "mode": mode,
        "set_by": str(set_by),
        "timestamp": firestore.SERVER_TIMESTAMP
    })

def load_inhouse_mode_for_guild(guild_id):
    doc = db.collection("inhouse_modes").document(str(guild_id)).get()
    if doc.exists:
        return doc.to_dict().get("mode", "regular")
    return "regular"

# ============================ 🎯 MMR & STRATZ Integration ============================
# Maps Dota 2 STRATZ seasonRank values to estimated MMR values.
season_rank_to_mmr = {
    11: 77, 12: 231, 13: 385, 14: 539, 15: 693,
    21: 847, 22: 1001, 23: 1155, 24: 1309, 25: 1463,
    31: 1594, 32: 1749, 33: 1953, 34: 2081, 35: 2208,
    41: 2387, 42: 2541, 43: 2695, 44: 2849, 45: 3003,
    51: 3157, 52: 3311, 53: 3465, 54: 3619, 55: 3772,
    61: 3927, 62: 4081, 63: 4235, 64: 4389, 65: 4542,
    71: 4720, 72: 4920, 73: 5120, 74: 5320, 75: 5520
}

# Converts a full 64-bit Steam ID to the shorter 32-bit Steam account ID used by STRATZ.
def convert_to_steam32(steam_id_str):
    try:
        steam_id = int(steam_id_str.replace(" ", ""))
        if steam_id > 76561197960265728:
            return steam_id - 76561197960265728
        return steam_id
    except ValueError:
        return None

# Sends a GraphQL query to STRATZ to fetch a user's seasonRank and maps it to an estimated MMR.
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

# Gets the stored MMR value for a given Discord user, or returns 0 if not found.
def get_mmr(user):
    user_id = str(user.id)
    info = get_player_config(user_id)
    if info and isinstance(info, dict):
        return info.get("mmr", 0)
    return 0

# ============================ 👥 Player & Lobby Utilities ============================
# Returns a set of user IDs across all servers that the bot is currently in (non-bot members only).
def get_active_user_ids():
    """Return a set of user IDs across all servers the bot is in."""
    user_ids = set()
    for guild in bot.guilds:
        for member in guild.members:
            if not member.bot:
                user_ids.add(str(member.id))
    return user_ids

# Periodic background task that updates all players' MMR values from STRATZ in Firebase,
# and refreshes lobby embeds across all servers.
@tasks.loop(hours=24)
async def refresh_all_mmrs():
    print("Refreshing MMRs (Firebase)...")
    players_ref = db.collection("players").stream()
    for doc in players_ref:
        user_id = doc.id
        data = doc.to_dict()
        if "steam_id" in data:
            mmr, season_rank = fetch_mmr_from_stratz(data["steam_id"])
            if mmr:
                db.collection("players").document(user_id).update({
                    "mmr": mmr,
                    "seasonRank": season_rank
                })
    # Refresh lobby embeds across all servers
    await update_all_lobbies()

def get_captains_and_pool(players):
    sorted_players = sorted(players, key=lambda p: p[2])  # sort by MMR
    min_diff = float('inf')
    best_pair = ()

    for i in range(len(sorted_players)):
        for j in range(i + 1, len(sorted_players)):
            diff = abs(sorted_players[i][2] - sorted_players[j][2])
            if diff < min_diff:
                min_diff = diff
                best_pair = (sorted_players[i], sorted_players[j])

    pool = [p for p in players if p not in best_pair]
    return best_pair, pool

# ================================ ⚖️ Team Balancing ================================
# Finds all possible 5v5 team splits from a 10-player list and sorts them by MMR balance.
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

# ========================================================================================================================
# ================================================= 💬 Commands Section =================================================
# ========================================================================================================================

# ============================== 👥 General Commands ==============================
# Links a user's Steam ID to their Discord account and stores their MMR/seasonRank in Firebase.
@bot.command(name="cfg")
async def cfg_cmd(ctx, steam_id: str, member: discord.Member = None):
    steam32 = convert_to_steam32(steam_id)
    if steam32 is None:
        await ctx.send("Please provide a valid numeric Steam friend code or Steam ID.")
        return
    target = member or ctx.author
    # Check if user is trying to configure someone else
    if target != ctx.author:
        # Only allow if user is admin or has one of the special roles
        allowed_roles = ["Inhouse Admin", "Drow Picker"]
        if not ctx.author.guild_permissions.administrator and not any(role.name in allowed_roles for role in ctx.author.roles):
            await ctx.send("❌ You do not have permission to configure another user. Only admins or users with the 'Inhouse Admin' or 'Drow Picker' role may do that.")
            return
    user_id = str(target.id)
    mmr, season_rank = fetch_mmr_from_stratz(steam32)
    # If MMR is None but seasonRank is high, set MMR manually
    if mmr is None and season_rank and season_rank >= 80:
        mmr = 5650
    config_data = {
        "steam_id": steam32,
        "steam_name": target.name,
        "discord_username": str(target),
        "discord_nickname": target.nick if target.nick else target.display_name,
        "mmr": mmr,
        "seasonRank": season_rank
    }
    save_player_config(user_id, config_data)
    if mmr:
        await ctx.send(f"{target.mention}, your Steam ID `{steam32}` has been linked with an estimated MMR of **{mmr}**.")
    else:
        await ctx.send(f"{target.mention}, Steam ID linked, but MMR could not be determined.")

# Displays the stored MMR for the user or another mentioned member.
@bot.command(name="mmr")
async def mmr_lookup(ctx, member: discord.Member = None):
    user = member or ctx.author
    mmr = get_mmr(user)
    await ctx.send(f"{user.display_name}'s MMR is **{mmr}**.")

# ========================== 🏠 Lobby Management Commands =========================
# Adds one or more users to the current lobby for the server.
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

# Removes one or more users from the current lobby for the server.
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

# Launches the inhouse lobby message and embed, or refreshes the existing one.
# Accepts optional mode: 'regular' or 'immortal'. If mode is not provided, the bot will load the last-used mode from Firestore.
@bot.command(name="lobby")
async def lobby_cmd(ctx, mode: str = None):
    guild_id = ctx.guild.id
    if mode:
        # Restrict mode changes to admins and allowed roles only
        if not await user_is_admin_or_has_role(ctx.author):
            await ctx.send("❌ You don't have permission to change the inhouse mode.")
            return
        # Save and use the provided mode (if valid)
        selected_mode = mode.lower() if mode.lower() in ["regular", "immortal"] else "regular"
        save_inhouse_mode_for_guild(guild_id, selected_mode, ctx.author)
    else:
        # Load last used mode from Firestore
        selected_mode = load_inhouse_mode_for_guild(guild_id)
    # Store mode in memory for reaction handling
    inhouse_mode[guild_id] = selected_mode
    # Initialize structures if not already present
    if guild_id not in lobby_players:
        lobby_players[guild_id] = []
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

# Clears the current lobby list and creates a new lobby message embed.
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

# ============================= 🔐 Admin-Only Commands ============================
# Admin only: manually set a user's MMR in Firebase.
@bot.command(name="setmmr")
@is_admin_or_has_role()
async def setmmr(ctx, mmr: int, member: discord.Member):
    # Safety check
    if member not in ctx.guild.members:
        await ctx.send("That user is not in this server.")
        return
    user_id = str(member.id)
    # Update Firestore document
    try:
        user_ref = db.collection("players").document(user_id)
        user_ref.set({"mmr": mmr}, merge=True)
        await ctx.send(f"{member.mention}'s MMR has been manually set to **{mmr}**.")
    except Exception as e:
        await ctx.send(f"Failed to set MMR due to an error: {e}")

@setmmr.error
async def set_mmr_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("❌ You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' or 'Drow Picker' role.")

# Admin-only: mentions all 10 players in a full lobby to alert them.
@bot.command(name="alert")
@is_admin_or_has_role()
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
    if isinstance(error, commands.CheckFailure):
        await ctx.send("❌ You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' or 'Drow Picker' role.")

# Admin-only: changes the inhouse lobby password and updates the lobby embed.
@bot.command(name="setpassword")
@is_admin_or_has_role()
async def set_password(ctx, *, new_password: str):
    guild_id = ctx.guild.id
    save_lobby_password_for_guild(
    guild_id,
    new_password,
    server_name=ctx.guild.name,
    set_by=str(ctx.author)
)
    await update_lobby_embed(ctx.guild)
    await ctx.send(f"Password updated to: `{new_password}`")

@set_password.error
async def set_password_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("❌ You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' or 'Drow Picker' role.")

# Admin-only: changes the bot's command prefix for the server.
@bot.command(name="changeprefix")
@is_admin_or_has_role()
async def change_prefix(ctx, new_prefix: str):
    guild_id = ctx.guild.id
    store_guild_prefix(
    guild_id,
    new_prefix,
    server_name=ctx.guild.name,
    set_by=str(ctx.author)
)
    await ctx.send(f"✅ Command prefix changed to `{new_prefix}` for this server.")

@change_prefix.error
async def change_prefix_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("❌ You do not have permission to change the prefix. You must be a server admin or have the 'Inhouse Admin' or 'Drow Picker' role.")

# Displays the most recent prefix and lobby password logs for the current server. By default, shows a clean summary with who set each value and when.
# Use "--verbose" to display detailed metadata including user IDs, timestamps, and full Firestore document data.
@bot.command(name="viewlogs")
@is_admin_or_has_role()
async def viewlogs(ctx, *, flags: str = ""):
    guild_id = ctx.guild.id
    guild_name = ctx.guild.name
    verbose = '--verbose' in (flags or "").lower()
    prefix_doc = db.collection("prefixes").document(str(guild_id)).get()
    password_doc = db.collection("lobbies").document(str(guild_id)).get()
    mode_doc = db.collection("inhouse_modes").document(str(guild_id)).get()
    lines = []
    if verbose:
        lines.append(f"📜 **Admin Logs (Verbose)** for `{guild_name}` (Guild ID: `{guild_id}`)\n")
    else:
        lines.append(f"📜 **Admin Logs for `{guild_name}`**\n")
    # PREFIX LOG
    if prefix_doc.exists:
        data = prefix_doc.to_dict()
        prefix = data.get("prefix", "Unknown")
        set_by = data.get("set_by", "Unknown")
        timestamp = data.get("timestamp", "Unknown")
        if verbose:
            lines.append(f"🔧 **Prefix**:\n  • Value: `{prefix}`\n  • Set by: {set_by}\n  • Timestamp: `{timestamp}`\n  • Full Doc: `{data}`")
        else:
            lines.append(f"🔧 **Prefix**: `{prefix}`\nSet by: {set_by}\nTime: {timestamp}")
    else:
        lines.append("🔧 **Prefix**: No record found.")
    # PASSWORD LOG
    if password_doc.exists:
        data = password_doc.to_dict()
        password = data.get("password", "Unknown")
        set_by = data.get("set_by", "Unknown")
        timestamp = data.get("timestamp", "Unknown")
        if verbose:
            lines.append(f"\n🔐 **Lobby Password**:\n  • Value: `{password}`\n  • Set by: {set_by}\n  • Timestamp: `{timestamp}`\n  • Full Doc: `{data}`")
        else:
            lines.append(f"\n🔐 **Lobby Password**: `{password}`\nSet by: {set_by}\nTime: {timestamp}")
    else:
        lines.append("\n🔐 **Lobby Password**: No record found.")
    await ctx.send("\n".join(lines))
    # MODE LOG
    if mode_doc.exists:
        data = mode_doc.to_dict()
        mode = data.get("mode", "Unknown")
        set_by = data.get("set_by", "Unknown")
        timestamp = data.get("timestamp", "Unknown")
        if verbose:
            lines.append(f"\n🛠️ **Inhouse Mode**:\n • Value: `{mode}`\n • Set by: {set_by}\n • Timestamp: `{timestamp}`\n • Full Doc: `{data}`")
        else:
            lines.append(f"\n🛠️ **Inhouse Mode**: `{mode}`\nSet by: {set_by}\nTime: {timestamp}")
    else:
        lines.append("\n🛠️ **Inhouse Mode**: No record found.")

# ================================ ℹ️ Help Command ================================
# Displays a list of all bot commands and their usage.
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
        "**!add `<@user1>` `<@user2>` ...** - Manually add one or more users to the lobby.\n"
        "**!remove `<@user1>` `<@user2>` ...** - Manually remove one or more users from the lobby.\n\n"
        "__**🔐 Admin Commands**__\n"
        "**!lobby `<mode>`** - (Admin only) Sets the lobby mode for the inhouse \n"
        "Modes: • `regular` — Regular Captain’s Mode (MMR-balanced teams) \n"
        "• `immortal` — Captain’s Mode with Immortal Draft (captains pick teams) \n"
        "**!setmmr `<mmr>` `<@user>`** - (Admin only) Manually set a user's MMR.\n"
        "**!setpassword `<new_password>`** - (Admin only) Change the inhouse lobby password.\n"
        "**!changeprefix `<new_prefix>`** - (Admin only) Changes the prefix of the bot commands.\n"
        "**!alert** - (Admin only) Mention all 10 players when the lobby is full.\n"
    )
    await ctx.send(help_text)

# ========================================================================================================================
# ================================================ 🎯 Bot Event Handlers ================================================
# ========================================================================================================================

# Runs once when the bot starts and begins the MMR refresh task.
@bot.event
async def on_ready():
    global player_data
    player_data = {}  # still fine to cache this in memory
    print(f"{bot.user} is online!")
    refresh_all_mmrs.start()

# Listens for any messages containing "dota" and replies with a generic response.
@bot.event
async def on_message(msg):
    if msg.author.bot:
        return
    if "dota" in msg.content.lower():
        await msg.channel.send(f"Interesting message, {msg.author.mention}")
    await bot.process_commands(msg)

# Handles user reactions on lobby messages to join, leave, or roll teams.
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
        mode = inhouse_mode.get(guild_id, "regular")
        if mode == "regular":
            team_rolls[guild_id] = calculate_balanced_teams(lobby_players[guild_id])
            original_teams[guild_id] = team_rolls[guild_id][0]
            roll_count[guild_id] = 1
            embed = build_team_embed(*original_teams[guild_id], guild)
        elif mode == "immortal":
            captains, pool = get_captains_and_pool(lobby_players[guild_id])
            original_teams[guild_id] = (captains, pool)
            roll_count[guild_id] = 1
            embed = build_immortal_embed(captains, pool, guild)
        await message.edit(embed=embed)
        await message.clear_reactions()
        await message.add_reaction("👍")
        await message.add_reaction("👎")
        await message.add_reaction("♻️")
        await message.remove_reaction(payload.emoji, user)
    elif emoji == "♻️" and len(lobby_players[guild_id]) == 10:
        mode = inhouse_mode.get(guild_id, "regular")
        # Only allow admins to re-roll
        if not user.guild_permissions.administrator:
            await message.remove_reaction(payload.emoji, user)
            return
        if mode == "regular":
            # REGULAR INHOUSE REROLL
            if roll_count[guild_id] >= MAX_ROLLS:
                roll_count[guild_id] = 1
            else:
                roll_count[guild_id] += 1
            team_rolls[guild_id] = calculate_balanced_teams(lobby_players[guild_id])
            original_teams[guild_id] = team_rolls[guild_id][0]
            embed = build_team_embed(*original_teams[guild_id], guild)
        elif mode == "immortal":
            # IMMORTAL DRAFT REROLL
            if roll_count[guild_id] >= MAX_ROLLS:
                roll_count[guild_id] = 1
            else:
                roll_count[guild_id] += 1
            captains, pool = get_captains_and_pool(lobby_players[guild_id])
            original_teams[guild_id] = (captains, pool)
            embed = build_immortal_embed(captains, pool, guild)
        await message.edit(embed=embed)
        await message.remove_reaction(payload.emoji, user)
    if updated:
        await update_lobby_embed(guild)
    # Always remove the user's reaction
    await message.remove_reaction(payload.emoji, user)

# Sends a welcome message with instructions when the bot joins a new server.
@bot.event
async def on_guild_join(guild):
    welcome_embed = discord.Embed(
        title="👋 Welcome to FeederBot!",
        description=(
            "Thanks for inviting me to your server!\n\n"
            "**To get started**, try using:\n"
            "`!lobby` - to create an inhouse lobby\n"
            "`!cfg <steam_id>` - to link your Steam ID\n"
            "`!add @user` - to add players\n"
            "`!help` - for full command list\n\n"
            "FeederBot keeps lobby info separate for each server. If you ever need help, run `!help`."
        ),
        color=discord.Color.green()
    )
    welcome_embed.set_footer(text="Enjoy your games!")
    # Try system channel
    if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
        try:
            await guild.system_channel.send(embed=welcome_embed)
            return
        except discord.Forbidden:
            pass  # fall through to DM
    # Try the first available text channel
    for channel in guild.text_channels:
        if channel.permissions_for(guild.me).send_messages:
            try:
                await channel.send(embed=welcome_embed)
                return
            except discord.Forbidden:
                continue
    # If all else fails, DM the server owner
    try:
        if guild.owner:
            await guild.owner.send(
                f"Hi {guild.owner.name}, I couldn't post a welcome message in `{guild.name}` "
                "due to missing permissions. Please ensure I can send messages in a channel. Here's what I'd say:",
                embed=welcome_embed
            )
    except discord.Forbidden:
        print(f"Could not DM the owner of {guild.name}.")

# ========================================================================================================================
# ============================================== 🖼️ Embed Builders Section ==============================================
# ========================================================================================================================

# ============================= 📋 Lobby Embed Functions =============================
# Builds and returns a lobby embed showing current players and the server's password.
def build_lobby_embed(guild):
    guild_id = guild.id
    embed = discord.Embed(
        title="DotA2 Inhouse",
        description=f"({len(lobby_players.get(guild_id, []))}/10)",
        color=discord.Color.purple()
    )
    for _, name, mmr in lobby_players.get(guild_id, []):
        embed.add_field(name=name, value=str(mmr), inline=True)
    password = load_lobby_password_for_guild(guild_id)
    embed.add_field(name="**Password**", value=password, inline=False)
    return embed

# Updates the current lobby embed message with the latest player list and password.
async def update_lobby_embed(guild):
    guild_id = guild.id
    if guild_id not in lobby_players or guild_id not in lobby_message:
        return
    embed = build_lobby_embed(guild)
    message = lobby_message[guild_id]
    await message.edit(embed=embed)
    if len(lobby_players[guild_id]) == 10:
        await message.add_reaction("🚀")

# Loops through all servers the bot is in and updates any existing lobby embed messages.
async def update_all_lobbies():
    for guild in bot.guilds:
        await update_lobby_embed(guild)

# ============================== ⚔️ Team Embed Function ==============================
# Creates and returns a Discord embed object displaying the two teams with their MMRs and password.
def build_team_embed(team1, team2, guild):
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
    password = load_lobby_password_for_guild(guild.id)
    embed.add_field(name="Team One", value=", ".join(f"{p[1]} ({p[2]})" for p in team1_sorted), inline=False)
    embed.add_field(name="Team Two", value=", ".join(f"{p[1]} ({p[2]})" for p in team2_sorted), inline=False)
    embed.add_field(name="**Password**", value=password, inline=False)
    return embed

def build_immortal_embed(captains, pool, guild):
    c1, c2 = captains
    embed = discord.Embed(
        title="🛡️ Immortal Draft Inhouse",
        description=f"Captains: {c1[1]} ({c1[2]}) vs {c2[1]} ({c2[2]})\nRoll #{roll_count[guild.id]}/{MAX_ROLLS}",
        color=discord.Color.orange()
    )
    embed.add_field(name="Captain 1", value=f"{c1[1]} ({c1[2]})", inline=True)
    embed.add_field(name="Captain 2", value=f"{c2[1]} ({c2[2]})", inline=True)
    embed.add_field(
        name="🧩 Draft Pool",
        value=", ".join(f"{p[1]} ({p[2]})" for p in sorted(pool, key=lambda x: x[2], reverse=True)),
        inline=False
    )
    password = load_lobby_password_for_guild(guild.id)
    embed.add_field(name="**Password**", value=password, inline=False)
    return embed

bot.run(TOKEN)