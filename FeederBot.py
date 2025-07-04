# ------------------------------------------------------------
# FeederBot - Discord Inhouse Lobby Bot
# Author: Arman Hasan
# Created: June 2025
# Location: Ft. Lauderdale, Florida
# Description: A Discord bot for managing DotA2 inhouse lobbies,
#              including MMR tracking, team balancing, and lobby alerts.
# ------------------------------------------------------------
import asyncio
import os
import json
import random
from typing import Optional
import discord
import requests
import time
import itertools
import betting_manager
import firebase_setup  # ensures Firebase is initialized before anything else
from discord.ext import commands, tasks
from dotenv import load_dotenv
from firebase_admin import firestore
from mmr_manager import adjust_mmr, get_inhouse_mmr, get_top_players
from betting_manager import clear_guild_bets, get_balance, place_bet, resolve_bets, clear_all_bets
from match_tracker import fetch_match_result

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
STRATZ_TOKEN = os.getenv("STRATZ_TOKEN")
STEAM_API_KEY = os.getenv("STEAM_API_KEY")

db = firestore.client()

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.guilds = True
intents.members = True

inhouse_mode = {}          # {guild_id: "regular" or "immortal"}
lobby_players = {}         # {guild_id: list of (user_id, name, mmr)}
lobby_message = {}         # {guild_id: message}
roll_count = {}            # {guild_id: int}
team_rolls = {}            # {guild_id: list of team tuples}
original_teams = {}        # {guild_id: team tuple}
captain_draft_state = {}   # {guild_id: {"pairs": [...], "index": 0}}
bound_league_ids = {}      # {guild_id: league_id}
LIVE_CHANNEL_IDS = {}      # {guild_id: channel_id}
live_embed_messages = {}   # {guild_id: message}
polling_tasks = {}         # {guild_id: asyncio.Task} for per-server polling
active_match_ids = {}        # guild_id: match_id

MAX_ROLLS = 5  # for regular
IMMORTAL_MAX_ROLLS = 3  # for immortal

HERO_CACHE_FILE = "hero_id_map.json"
with open("hero_id_map.json", "r") as f:
    hero_id_map = json.load(f)

# ========================================================================================================================
# ============================================ ⚙️ Core Functions & Utilities ============================================
# ========================================================================================================================

async def poll_live_match(guild_id):
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            match = fetch_live_match_for_guild(guild_id)
            if match is None:
                print(f"[INFO] No live match found for guild {guild_id}")
                await asyncio.sleep(15)
                continue

            league_id = match.get("league_id")
            guild_id_str = str(guild_id)

            bound_league_id = bound_league_ids.get(guild_id_str)
            if str(bound_league_id) != str(league_id):
                db.collection("guild_specific_info").document(guild_id_str).set({
                    "league_id": {"bound_league_id": str(league_id)}
                }, merge=True)
                bound_league_ids[guild_id_str] = str(league_id)
                print(f"[INFO] Bound league_id {league_id} to guild {guild_id_str}")
            else:
                print(f"[DEBUG] Guild {guild_id_str} already bound to league {league_id}")

            # Embed update
            channel_info = LIVE_CHANNEL_IDS.get(guild_id_str)
            guild = bot.get_guild(guild_id)
            embed = await format_live_match_embed(match, guild)

            if isinstance(channel_info, dict):
                channel_id = int(channel_info.get("live_channel_id", 0))
                channel = bot.get_channel(channel_id)
                if channel:
                    prev_msg = live_embed_messages.get(guild_id_str)
                    if prev_msg:
                        try:
                            await prev_msg.edit(embed=embed)
                        except discord.NotFound:
                            new_msg = await channel.send(embed=embed)
                            live_embed_messages[guild_id_str] = new_msg
                    else:
                        new_msg = await channel.send(embed=embed)
                        live_embed_messages[guild_id_str] = new_msg
        except Exception as e:
            print(f"[ERROR in poll_live_match()] for guild {guild_id}: {e}")
        await asyncio.sleep(15)

# ============================== 🛠️ Bot Configuration ==============================
# Resolves the correct command prefix for the bot, based on the message's guild.
async def resolve_command_prefix(bot, message):
    if message.guild:
        prefix = load_guild_prefix(str(message.guild.id))
        return prefix
    return "!"  # fallback default for DMs
bot = commands.Bot(command_prefix=resolve_command_prefix, intents=intents, help_command=None)

# =============================== 🔐 Permission Checks ===============================
# Custom check that allows admins or specific roles to use commands
def is_admin_or_has_role():
    async def predicate(ctx):
        global_admin_ids = ["187959278949105664"]  # 👈 replace with your real Discord user ID
        if str(ctx.author.id) in global_admin_ids:
            return True
        if ctx.author.guild_permissions.administrator:
            return True
        admin_roles = ["Inhouse Admin"]
        return any(role.name in admin_roles for role in ctx.author.roles)
    return commands.check(predicate)

# Utility function version of the role check (returns True/False instead of being a decorator)
async def user_is_admin_or_has_role(member):
    global_admin_ids = ["187959278949105664"]  # 👈 Replace with your actual Discord user ID
    if str(member.id) in global_admin_ids:
        return True
    if member.guild_permissions.administrator:
        return True
    allowed_roles = ["Inhouse Admin"]
    return any(role.name in allowed_roles for role in member.roles)

# ========================== 🔥 Firestore Access & Persistence ==========================
# Saves a player's config data (Steam info, MMR, etc.) to Firestore under their Discord user ID.
def save_player_config(user_id, data):
    doc_ref = db.collection("players").document(str(user_id))
    doc_ref.set(data)

# Retrieves a player's saved config data from Firestore using their Discord user ID.
def load_player_config(user_id):
    doc = db.collection("players").document(str(user_id)).get()
    return doc.to_dict() if doc.exists else None

# Stores a custom command prefix for a specific Discord server (guild) to Firestore.
def save_guild_prefix(guild_id, prefix, server_name=None, set_by=None):
    data = {
        "prefix": prefix,
        "prefix_set_by": set_by,
        "prefix_timestamp": firestore.SERVER_TIMESTAMP,
        "server_name": server_name,
    }
    doc_ref = db.collection("guild_specific_info").document(str(guild_id))
    doc_ref.set({"prefix": data}, merge=True)

def load_guild_prefix(guild_id):
    doc = db.collection("guild_specific_info").document(str(guild_id)).get()
    if doc.exists:
        data = doc.to_dict()
        return data.get("prefix", {}).get("prefix", "!")  # nested get
    return "!"

# Saves the inhouse lobby password for a Discord server (guild) to Firestore.
def save_lobby_password_for_guild(guild_id, password, server_name=None, set_by=None):
    data = {
        "password": password,
        "password_set_by": set_by,
        "password_timestamp": firestore.SERVER_TIMESTAMP,
        "server_name": server_name,
        }
    doc_ref = db.collection("guild_specific_info").document(str(guild_id))
    doc_ref.set({"password": data}, merge=True)

# Loads the saved inhouse lobby password for a guild from Firestore; returns "penguin" if not set.
def load_lobby_password_for_guild(guild_id):
    doc = db.collection("guild_specific_info").document(str(guild_id)).get()
    if doc.exists:
        data = doc.to_dict()
        return data.get("password", {}).get("password", "penguin")
    return "penguin"

def save_inhouse_mode_for_guild(guild_id, mode, server_name=None, set_by=None):
    data = {
        "mode": mode,
        "mode_set_by": str(set_by),
        "mode_timestamp": firestore.SERVER_TIMESTAMP,
        "server_name": server_name,
        }
    doc_ref = db.collection("guild_specific_info").document(str(guild_id))
    doc_ref.set({"inhouse_mode": data}, merge=True)

def load_inhouse_mode_for_guild(guild_id):
    doc = db.collection("guild_specific_info").document(str(guild_id)).get()
    if doc.exists:
        return doc.to_dict().get("inhouse_mode", {}).get("mode", "regular")
    return "regular"

def save_league_guild_mapping(guild_id: int, league_id: int, server_name=None, bound_by=None):
    data = {
        "bound_league_id": str(league_id),
        "league_id_bound_by": str(bound_by),
        "league_bind_timestamp": firestore.SERVER_TIMESTAMP,
        "server_name": server_name,
    }
    doc_ref = db.collection("guild_specific_info").document(str(guild_id))
    doc_ref.set({"league_id": data}, merge=True)

# ============================ 🎯 MMR, STRATZ, and Steam Integration ============================
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

def fetch_live_match_for_guild(guild_id):
    """Checks the bound league for the guild, or selects a random valid match."""
    guild_id_str = str(guild_id)
    bound_league_id = bound_league_ids.get(guild_id_str)

    url = "https://api.steampowered.com/IDOTA2Match_570/GetLiveLeagueGames/v1/"
    params = {"key": STEAM_API_KEY}

    try:
        response = requests.get(url, params=params, timeout=5)
        if response.status_code != 200:
            return None

        matches = response.json().get("result", {}).get("games", [])
        valid_matches = [m for m in matches if m.get("scoreboard")]

        if not valid_matches:
            if guild_id_str in active_match_ids:
                print(f"[INFO] Clearing expired match for guild {guild_id_str}")
                del active_match_ids[guild_id_str]
            return None

        selected_match = None

        # Try finding a match in the bound league
        if bound_league_id:
            bound_matches = [m for m in valid_matches if str(m.get("league_id")) == str(bound_league_id)]

            # Try to reuse previously active match
            last_match_id = active_match_ids.get(guild_id_str)
            if last_match_id:
                selected_match = next((m for m in bound_matches if m.get("match_id") == last_match_id), None)

            # If no active match found, pick one from the league
            if not selected_match and bound_matches:
                selected_match = random.choice(bound_matches)
                active_match_ids[guild_id_str] = selected_match["match_id"]
                print(f"[INFO] Now tracking match_id {selected_match['match_id']} for guild {guild_id_str}")

        # If no bound match found, fallback to random match
        if not selected_match:
            selected_match = random.choice(valid_matches)
            league_id = selected_match.get("league_id")

            # Auto-bind this league to guild
            db.collection("guild_specific_info").document(guild_id_str).set({
                "league_id": {
                    "bound_league_id": str(league_id),
                    "auto_bound": True
                }
            }, merge=True)
            bound_league_ids[guild_id_str] = str(league_id)
            active_match_ids[guild_id_str] = selected_match["match_id"]
            print(f"[fetch_live_match_for_guild()] Guild {guild_id_str} auto-bound to league_id {league_id}")

        selected_match["guild_id"] = guild_id_str
        return selected_match

    except Exception as e:
        print(f"[fetch_live_match_for_guild()] Steam API error: {e}")
        return None


def fetch_hero_id_to_name_map(api_key):
    # Try loading from local cache first
    if os.path.exists(HERO_CACHE_FILE):
        with open(HERO_CACHE_FILE, "r") as f:
            return json.load(f)

    # Otherwise, fetch from Steam API
    url = "https://api.steampowered.com/IEconDOTA2_570/GetHeroes/v1/"
    params = {
        "language": "en_us",
        "key": api_key
    }

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        heroes = response.json().get("result", {}).get("heroes", [])
        hero_map = {str(hero["id"]): hero["localized_name"] for hero in heroes}

        # Save to cache
        with open(HERO_CACHE_FILE, "w") as f:
            json.dump(hero_map, f)

        return hero_map
    except requests.exceptions.RequestException as e:
        print(f"Error fetching hero data: {e}")
        return {}
    
def is_league_live(league_id):
    """Returns match data if a live match for league_id is found, else None."""
    url = "https://api.steampowered.com/IDOTA2Match_570/GetLiveLeagueGames/v1/"
    params = {"key": STEAM_API_KEY}
    try:
        response = requests.get(url, params=params, timeout=5)
        if response.status_code == 200:
            matches = response.json().get("result", {}).get("games", [])
            for match in matches:
                if str(match.get("league_id")) == str(league_id) and match.get("scoreboard"):
                    return match
        return None
    except Exception as e:
        print(f"[ERROR in is_league_live()] Steam API error: {e}")
        return None

# Gets the stored MMR value for a given Discord user, or returns 0 if not found.
def get_mmr(user):
    user_id = str(user.id)
    info = load_player_config(user_id)
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

def get_discord_id_from_steam_id(steam_id: str) -> Optional[str]:
    try:
        steam_id_int = int(steam_id)
    except ValueError:
        print(f"[ERROR] Invalid Steam ID input: {steam_id}")
        return None
    players_ref = db.collection("players")
    query = players_ref.where(field_path="steam_id", op_string="==", value=steam_id_int).stream()
    for doc in query:
        return doc.id  # Discord ID is stored as the doc ID
    return None

async def format_team_players(players, guild):
    formatted = []
    for p in players:
        hero_id = str(p.get("hero_id", 0))
        if hero_id == "0":
            print(f"[WARN] Skipping player with hero_id 0: {p}")
            continue  # Skip if no hero assigned

        steam_id = str(p.get("account_id", "Unknown"))
        hero_name = hero_id_map.get(hero_id, f"Hero {hero_id}")

        # Check Firestore for player mapping
        player_doc = db.collection("players").document(steam_id).get()
        if player_doc.exists:
            discord_id = player_doc.to_dict().get("discord_id")
            member = guild.get_member(int(discord_id)) if discord_id else None
            display_name = member.display_name if member else f"<@{discord_id}>"
        else:
            display_name = get_steam_display_name(steam_id)

        formatted.append(f"**{display_name}**\n{hero_name}")
    return formatted

def get_steam_display_name(account_id_32):
    try:
        steam_id_64 = str(int(account_id_32) + 76561197960265728)
        url = f"https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
        params = {
            "key": STEAM_API_KEY,
            "steamids": steam_id_64
        }
        response = requests.get(url, params=params)
        data = response.json()
        players = data.get("response", {}).get("players", [])
        if players:
            return players[0].get("personaname", f"SteamID {account_id_32}")
    except:
        pass
    return f"SteamID {account_id_32}"

async def get_display_name_or_steam(account_id_32, guild):
    discord_id = get_discord_id_from_steam_id(account_id_32)
    if discord_id and guild:
        member = guild.get_member(int(discord_id))
        if member:
            return member.display_name
    return get_steam_display_name(account_id_32)

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

def get_all_captain_pairs(players):
    sorted_players = sorted(players, key=lambda p: p[2])  # sort by MMR
    pairs = []
    for i in range(len(sorted_players)):
        for j in range(i + 1, len(sorted_players)):
            p1 = sorted_players[i]
            p2 = sorted_players[j]
            diff = abs(p1[2] - p2[2])
            pool = [p for p in sorted_players if p not in (p1, p2)]
            pairs.append(((p1, p2), pool, diff))
    # Sort by smallest mmr difference
    pairs.sort(key=lambda x: x[2])  # sort by diff
    return pairs  # List of (captain_pair, pool, diff)

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
        is_authorized = await user_is_admin_or_has_role(ctx.author)
        if not is_authorized:
            await ctx.send("❌ You do not have permission to configure another user. Only admins or users with the 'Inhouse Admin' role may do that.")
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
@cfg_cmd.error
async def cfg_cmd_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❗ Usage: `!cfg <steam_id>` (optional: `@user`)")

# Displays the stored MMR for the user or another mentioned member.
@bot.command(name="mmr")
async def mmr_lookup(ctx, member: discord.Member = None):
    user = member or ctx.author
    mmr = get_mmr(user)
    await ctx.send(f"{user.display_name}'s MMR is **{mmr}**.")

# Displays a user's current inhouse MMR.
@bot.command(name="inhouse_mmr")
async def inhouse_mmr(ctx, member: discord.Member = None):
    member = member or ctx.author
    mmr = get_inhouse_mmr(ctx.guild.id, str(member.id))
    await ctx.send(f"{member.display_name}'s inhouse MMR is **{mmr}**.")

# Displays the top 10 inhouse MMR players in the server.
@bot.command(name="leaderboard")
async def leaderboard(ctx):
    top_players = get_top_players(ctx.guild.id)
    if not top_players:
        await ctx.send("No leaderboard data found for this server.")
        return
    lines = []
    for rank, (user_id, mmr) in enumerate(top_players, start=1):
        member = ctx.guild.get_member(int(user_id))
        name = member.display_name if member else f"User {user_id}"
        lines.append(f"**#{rank}** - {name}: {mmr} MMR")
    await ctx.send("🏆 **Top 10 Inhouse Players**\n" + "\n".join(lines))

# Places a bet on Radiant or Dire for the current inhouse match in this server.
@bot.command(name="bet")
async def bet(ctx, amount: int, team: str):
    team = team.lower()
    if team not in ["radiant", "dire"]:
        await ctx.send("❌ Invalid team. Choose `radiant` or `dire`.")
        return
    if amount <= 0:
        await ctx.send("❌ Bet amount must be greater than 0.")
        return
    user_id = str(ctx.author.id)
    guild_id = str(ctx.guild.id)
    nickname = ctx.author.nick if ctx.author.nick else ctx.author.display_name
    # Check for existing bet
    entry_ref = db.collection("guild_specific_info").document(guild_id).collection("bets").document(str(ctx.author.id))
    existing_bet_doc = entry_ref.get()
    previous_amount = 0
    is_update = False
    if existing_bet_doc.exists:
        existing_bet = existing_bet_doc.to_dict()
        previous_amount = existing_bet.get("amount", 0)
        previous_team = existing_bet.get("team", "")
        if team != previous_team:
            await ctx.send(
                f"❌ You already bet on **{previous_team.capitalize()}**. "
                f"You cannot change teams once your bet is placed."
            )
            return
        if amount <= previous_amount:
            await ctx.send(
                f"❌ You already bet `{previous_amount}`. You can only **increase** your bet amount."
            )
            return
        is_update = True
    old_balance = get_balance(guild_id, ctx.author.id)
    success = place_bet(user_id, team, amount, guild_id, nickname)
    new_balance = get_balance(guild_id, ctx.author.id)
    if not success:
        await ctx.send("❌ You don’t have enough balance.")
    else:
        if is_update:
            await ctx.send(
                f"🔁 You updated your bet from `{previous_amount}` to `{amount}` on **{team.capitalize()}**. "
                f"Your balance went from {old_balance} to {new_balance}."
            )
        else:
            await ctx.send(
                f"✅ You bet `{amount}` on **{team.capitalize()}** for this match. "
                f"Your balance went from {old_balance} to {new_balance}."
            )
@bet.error
async def bet_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❗ Usage: `!bet <amount> <radiant|dire>`")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❗ Invalid argument. Usage: `!bet <amount> <radiant|dire>` — make sure `<amount>` is a number.")
    else:
        await ctx.send("⚠️ An unexpected error occurred while placing your bet.")

# Displays the user's current coin balance.
@bot.command(name="balance")
async def balance(ctx, member: discord.Member = None):
    member = member or ctx.author
    user_id = str(member.id)
    guild_id = str(ctx.guild.id)
    coins = get_balance(guild_id, user_id)
    await ctx.send(f"💰 {member.display_name}'s balance: `{coins}` coins.")

# ========================== 🏠 Lobby Management Commands =========================
# Adds one or more users to the current lobby for the server.
@bot.command(name="add")
async def add_to_lobby(ctx, *members: discord.Member):
    # Since *members: discord.Member is being used, the command technically accepts zero or more members, 
    # which means a missing argument won't raise a MissingRequiredArgument error
    if not members:
        await ctx.send("❗ Usage: `!add @player1 [@player2 ...]`")
        return
    guild_id = ctx.guild.id
    # Initialize lobby for this guild if not already present
    if guild_id not in lobby_players:
        lobby_players[guild_id] = []
    # Prevent adding if lobby already has 10 players
    if len(lobby_players[guild_id]) >= 10:
        await ctx.send("Lobby is already full. Cannot add more players.")
        return
    added = []
    for member in members:
        if any(uid == member.id for uid, _, _ in lobby_players[guild_id]):
            continue
        mmr = get_mmr(member)
        display_name = member.display_name  # prefers nickname if available
        lobby_players[guild_id].append((member.id, display_name, mmr))
        added.append(display_name)
    if added:
        await update_lobby_embed(ctx.guild)
        await ctx.send(f"Added to lobby: {', '.join(added)}")
    else:
        await ctx.send("No new members were added.")

# Removes one or more users from the current lobby for the server.
@bot.command(name="remove")
async def remove_from_lobby(ctx, *members: discord.Member):
    if not members:
        await ctx.send("❗ Usage: `!remove @player1 [@player2 ...]`")
        return
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
        # Re-fetch message to get updated reaction state
        channel = ctx.channel
        message = await channel.fetch_message(lobby_message[guild_id].id)
        # Clear special reactions if lobby is no longer full
        if len(lobby_players[guild_id]) < 10:
            # Clear reactions only after embed update to prevent race conditions
            await update_lobby_embed(ctx.guild)  # Ensure the embed is updated first
            for reaction in message.reactions:
                if str(reaction.emoji) in ["🚀", "♻️"]:  # clear both rocket and re-roll reactions
                    await message.clear_reaction(reaction.emoji)
            await ctx.send(f"Removed from lobby: {', '.join(removed)}")
    else:
        await ctx.send("None of the specified members were in the lobby.")

# Launches the inhouse lobby message and embed, or refreshes the existing one.
# Accepts optional mode: 'regular' or 'immortal'. If mode is not provided, the bot will load the last-used mode from Firestore.
@bot.command(name="lobby")
async def lobby_cmd(ctx, mode: str = None):
    guild_id = ctx.guild.id
    # Preserve current players if they exist
    existing_players = lobby_players.get(guild_id, [])
    if mode:
        # Restrict mode changes to admins and allowed roles only
        if not await user_is_admin_or_has_role(ctx.author):
            await ctx.send("❌ You don't have permission to change the inhouse mode.")
            return
        # Save and use the provided mode (if valid)
        selected_mode = mode.lower() if mode.lower() in ["regular", "immortal"] else "regular"
        save_inhouse_mode_for_guild(guild_id, selected_mode, server_name=ctx.guild.name, set_by=str(ctx.author))
    else:
        # Load last used mode from Firestore
        selected_mode = load_inhouse_mode_for_guild(guild_id)
    # Store mode in memory for reaction handling
    inhouse_mode[guild_id] = selected_mode
    # Initialize structures if not already present
    if guild_id not in lobby_players:
        lobby_players[guild_id] = existing_players
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
    embed = build_lobby_embed(ctx.guild, mode)
    message = await ctx.send(embed=embed)
    lobby_message[guild_id] = message
    # Add reactions
    await message.add_reaction("👍")
    await message.add_reaction("👎")
    if len(lobby_players[guild_id]) == 10:
        await message.add_reaction("🚀")

# Clears the current lobby list and creates a new lobby message embed.
@bot.command(name="reset")
async def reset(ctx, *args):
    if args:
        await ctx.send("❗ Usage: `!reset` (no extra arguments allowed)")
        return
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
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❗ Usage: `!setmmr <mmr> @user`")
    elif isinstance(error, commands.CheckFailure):
        await ctx.send("❌ You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")

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
        await ctx.send("❌ You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")

# Admin-only: changes the inhouse lobby password and updates the lobby embed.
@bot.command(name="setpassword")
@is_admin_or_has_role()
async def set_password(ctx, *, new_password: str):
    save_lobby_password_for_guild(ctx.guild.id, new_password, server_name=ctx.guild.name, set_by=str(ctx.author))
    await update_lobby_embed(ctx.guild)
    await ctx.send(f"Password updated to: `{new_password}`")
@set_password.error
async def set_password_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❗ Usage: `!setpassword <new_password>`")
    elif isinstance(error, commands.CheckFailure):
        await ctx.send("❌ You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")

# Admin-only: changes the bot's command prefix for the server.
@bot.command(name="changeprefix")
@is_admin_or_has_role()
async def change_prefix(ctx, new_prefix: str):
    save_guild_prefix(ctx.guild.id, new_prefix, server_name=ctx.guild.name, set_by=str(ctx.author))
    await ctx.send(f"✅ Command prefix changed to `{new_prefix}` for this server.")
@change_prefix.error
async def change_prefix_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❗ Usage: `!changeprefix <new_prefix>`")
    elif isinstance(error, commands.CheckFailure):
        await ctx.send("❌ You do not have permission to change the prefix. You must be a server admin or have the 'Inhouse Admin' role.")

# Admin-only: Displays the most recent prefix and lobby password logs for the current server. By default, shows a clean summary with who set each value and when.
# Use "--verbose" to display detailed metadata including user IDs, timestamps, and full Firestore document data.
@bot.command(name="viewlogs")
@is_admin_or_has_role()
async def viewlogs(ctx, *, flags: str = ""):
    guild_id = ctx.guild.id
    guild_name = ctx.guild.name
    verbose = '--verbose' in (flags or "").lower()
    # ✅ Unified Firestore document for this guild
    doc = db.collection("guild_specific_info").document(str(guild_id)).get()
    lines = []
    if verbose:
        lines.append(f"📜 **Admin Logs (Verbose)** for `{guild_name}` (Guild ID: `{guild_id}`)\n")
    else:
        lines.append(f"📜 **Admin Logs for `{guild_name}`**\n")
    if doc.exists:
        data = doc.to_dict()
        # PREFIX LOG
        prefix = data.get("prefix", "Unknown")
        prefix_set_by = data.get("prefix_set_by", "Unknown")
        prefix_time = data.get("prefix_timestamp", "Unknown")
        if verbose:
            lines.append(f"🔧 **Prefix**:\n  • Value: `{prefix}`\n  • Set by: {prefix_set_by}\n  • Timestamp: `{prefix_time}`\n  • Full Doc: `{data}`")
        else:
            lines.append(f"🔧 **Prefix**: `{prefix}`\nSet by: {prefix_set_by}\nTime: {prefix_time}")
        # PASSWORD LOG
        password = data.get("password", "Unknown")
        password_set_by = data.get("password_set_by", "Unknown")
        password_time = data.get("password_timestamp", "Unknown")
        if verbose:
            lines.append(f"\n🔐 **Lobby Password**:\n  • Value: `{password}`\n  • Set by: {password_set_by}\n  • Timestamp: `{password_time}`\n  • Full Doc: `{data}`")
        else:
            lines.append(f"\n🔐 **Lobby Password**: `{password}`\nSet by: {password_set_by}\nTime: {password_time}")
        # INHOUSE MODE LOG
        mode = data.get("mode", "Unknown")
        mode_set_by = data.get("mode_set_by", "Unknown")
        mode_time = data.get("mode_timestamp", "Unknown")
        if verbose:
            lines.append(f"\n🛠️ **Inhouse Mode**:\n  • Value: `{mode}`\n  • Set by: {mode_set_by}\n  • Timestamp: `{mode_time}`\n  • Full Doc: `{data}`")
        else:
            lines.append(f"\n🛠️ **Inhouse Mode**: `{mode}`\nSet by: {mode_set_by}\nTime: {mode_time}")
    else:
        lines.append("❌ No Firestore data found for this guild.")
    await ctx.send("\n".join(lines))

# Admin-only: Submits and processes a match ID manually for MMR and bet resolution.
@bot.command(name="submitmatch")
@is_admin_or_has_role()
async def submitmatch(ctx, match_id: str):
    await ctx.send("📊 Processing submitted match...")
    result = fetch_match_result(match_id)
    if not match_id.isdigit():
        await ctx.send("❗ Match ID must be a number.")
        return
    if not result:
        await ctx.send("❌ Could not fetch match result. Check the match ID.")
        return
    def map_steam_ids_to_discord_ids(steam_ids):
        discord_ids = []

        for steam_id in steam_ids:
            discord_id = get_discord_id_from_steam_id(steam_id)
            if discord_id:
                discord_ids.append(discord_id)
            else:
                print(f"[WARN] No Discord user found for Steam ID {steam_id}")
        print(f"[INFO] Mapped {len(discord_ids)}/{len(steam_ids)} Steam IDs to Discord IDs")
        return discord_ids
    winner_ids = map_steam_ids_to_discord_ids(result["radiant"] if result["radiant_win"] else result["dire"])
    loser_ids = map_steam_ids_to_discord_ids(result["dire"] if result["radiant_win"] else result["radiant"])
    winning_team = "radiant" if result["radiant_win"] else "dire"
    await adjust_mmr(winner_ids, loser_ids, ctx.guild.id, ctx.guild)
    resolve_bets(ctx.guild.id, winning_team)
    clear_guild_bets(ctx)
    await ctx.send(f"✅ Match submitted. `{winning_team.capitalize()}` won. MMRs and bets updated.")
@submitmatch.error
async def submitmatch_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❗ Usage: `!submitmatch <match_id>`")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❗ Invalid match ID. It should be a numeric string like `8351234567`.")
    else:
        await ctx.send("⚠️ An unexpected error occurred while submitting the match.")

# Admin-only: Binds a Steam league ID to the current Discord server for live match tracking.
@bot.command(name="bindleague")
@is_admin_or_has_role()
async def bind_league_to_guild(ctx, league_id: str):
    save_league_guild_mapping(ctx.guild.id, league_id, server_name=ctx.guild.name, bound_by=str(ctx.author))
    await ctx.send(f"✅ League `{league_id}` bound to this server (Guild ID: `{ctx.guild.id}`).")

# Admin-only: Sets the current text channel as the destination for live match embed updates.
@bot.command(name="setlivechannel")
@is_admin_or_has_role()
async def set_live_channel(ctx):
    guild_id = str(ctx.guild.id)
    channel_id = ctx.channel.id
    # Save to Firestore
    data = {
        "live_channel_id": str(channel_id),
        "live_channel_timestamp": firestore.SERVER_TIMESTAMP,
    }
    doc_ref = db.collection("guild_specific_info").document(str(guild_id))
    doc_ref.set({"live_channel_id": data}, merge=True)
    # Update local cache
    LIVE_CHANNEL_IDS[guild_id] = channel_id
    await ctx.send(f"✅ This channel has been set to receive live match updates.")

@bot.command(name="startpolling")
@is_admin_or_has_role()
async def start_polling(ctx):
    guild_id = ctx.guild.id
    if guild_id in polling_tasks and not polling_tasks[guild_id].done():
        await ctx.send("⚠️ Polling is already running for this server.")
    else:
        task = asyncio.create_task(poll_live_match(guild_id))
        polling_tasks[guild_id] = task
        await ctx.send("✅ Started polling for live matches in this server.")

@bot.command(name="stoppolling")
@is_admin_or_has_role()
async def stop_polling(ctx):
    guild_id = ctx.guild.id
    if guild_id in polling_tasks and not polling_tasks[guild_id].done():
        polling_tasks[guild_id].cancel()
        await ctx.send("🛑 Stopped polling for this server.")
    else:
        await ctx.send("ℹ️ No polling is currently running for this server.")

# ================================ ℹ️ Help Command ================================
# Displays a list of all bot commands and their usage.
@bot.command(name="help")
async def help_command(ctx, *, category: str = ""):
    category = category.lower().strip()
    if category == "":
        help_text = (
            "\n**📜 Available Commands:**\n\n"
            "__**👥 General Commands**__\n"
            "**!cfg `steam_id` `@user`** - Link your Steam ID to fetch your MMR from STRATZ.\n"
            "**!mmr `@user`** - Show your MMR or another user's MMR.\n"
            "**!inhouse_mmr `@user`** - Show inhouse MMR for yourself or another user\n"
            "**!balance `@user`** - Show your or another user's coin balance\n"
            "**!leaderboard** - View top 10 inhouse MMR players in this server\n\n"
            "__**🏠 Lobby Management**__\n"
            "**!add `@user1` `@user2` ...** - Manually add one or more users to the lobby.\n"
            "**!remove `@user1` `@user2` ...** - Manually remove one or more users from the lobby.\n"
            "**!lobby** - Create or refresh the inhouse lobby.\n"
            "**!reset** - Clear the current lobby and start fresh.\n\n"
            "__**🎲 Betting Commands**__\n"
            "**!bet `amt` `radiant|dire`** - Bet coins on the current inhouse match\n"
            "**!balance `@user`** - Show your or another user’s coin balance\n\n"
            "__**🔐 Admin Commands**__\n"
            "Use `!help admin` to see the list of admin-only commands.\n"
        )
    elif category == "admin":
        help_text = (
            "\n__**🔐 Admin Commands**__\n"
            "**!lobby `mode`** - (Admin only) Sets the lobby mode for the inhouse \n"
            "Modes: • `regular` — Regular Captain’s Mode (MMR-balanced teams) \n"
            "           • `immortal` — Captain’s Mode with Immortal Draft (captains pick teams) \n"
            "**!setmmr `mmr` `@user`** - (Admin only) Manually set a user's MMR.\n"
            "**!setpassword `new_password`** - (Admin only) Change the inhouse lobby password.\n"
            "**!changeprefix `new_prefix`** - (Admin only) Changes the prefix of the bot commands.\n"
            "**!submitmatch `match_id`** - Admin-only: Report match and resolve MMR + bets\n"
            "**!alert** - (Admin only) Mention all 10 players when the lobby is full.\n"
            "**!viewlogs** - (Admin only) View recent lobby or user config logs.\n"
            "**!viewlogs --verbose** - (Admin only) View full detailed logs for this server.\n"
            "**!bindleague `league_id`** - (Admin only) Binds a Steam league ID to the current Discord server for live match tracking.\n"
            "**!setlivechannel** - (Admin only) Sets the current text channel as the destination for live match embed updates.\n"
    )
    else:
        help_text = "❌ Unknown help category. Try `!help` or `!help admin`."
    await ctx.send(f"{help_text}")

# ========================================================================================================================
# ================================================ 🎯 Bot Event Handlers ================================================
# ========================================================================================================================

# Runs once when the bot starts and begins the MMR refresh task.
@bot.event
async def on_ready():
    global hero_id_to_name
    print(f"{bot.user} is online!")
    refresh_all_mmrs.start()
    clear_all_bets(bot)
    # Cache hero IDs
    hero_id_to_name = fetch_hero_id_to_name_map(STEAM_API_KEY)
    # Load LIVE_CHANNEL_IDS from Firestore
    docs = db.collection("guild_specific_info").stream()
    for doc in docs:
        data = doc.to_dict()
        live_channel_id = data.get("live_channel_id")
        if live_channel_id:
            LIVE_CHANNEL_IDS[doc.id] = live_channel_id

# Listens for any messages containing "dota" and replies with a generic response.
"""@bot.event
async def on_message(msg):
    if msg.author.bot:
        return
    if "dota" in msg.content.lower():
        await msg.channel.send(f"Interesting message, {msg.author.mention}")
    await bot.process_commands(msg)"""

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
        if len(lobby_players[guild_id]) >= 10:
            await channel.send(f"{user.mention}, the lobby is already full (10/10). Please wait for someone to leave.")
            await message.remove_reaction(payload.emoji, user)
            return
        if not any(uid == user.id for uid, _, _ in lobby_players[guild_id]):
            mmr = get_mmr(user)
            display_name = user.display_name
            lobby_players[guild_id].append((user.id, display_name, mmr))
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
            all_pairs = get_all_captain_pairs(lobby_players[guild_id])
            captain_draft_state[guild_id] = {
                "pairs": all_pairs,
                "index": 0
            }
            captains, pool, _ = all_pairs[0]
            original_teams[guild_id] = (captains, pool)
            embed = build_immortal_embed(captains, pool, guild, 0)
        await message.edit(embed=embed)
        await message.clear_reactions()
        await message.add_reaction("👍")
        await message.add_reaction("👎")
        await message.add_reaction("♻️")
        await message.remove_reaction(payload.emoji, user)
    elif emoji == "♻️" and len(lobby_players[guild_id]) == 10:
        mode = inhouse_mode.get(guild_id, "regular")
        # Get the member object from the guild
        member = guild.get_member(payload.user_id)
        # Check if they are admin or have special roles
        if not await user_is_admin_or_has_role(member):
            return
        # REGULAR INHOUSE REROLL
        if mode == "regular":
            max_rolls = 3 if mode == "immortal" else MAX_ROLLS
            if roll_count[guild_id] >= max_rolls:
                roll_count[guild_id] = 1
            else:
                roll_count[guild_id] += 1
            team_rolls[guild_id] = calculate_balanced_teams(lobby_players[guild_id])
            original_teams[guild_id] = team_rolls[guild_id][0]
            embed = build_team_embed(*original_teams[guild_id], guild)
        # IMMORTAL INHOUSE REROLL
        elif mode == "immortal":
            max_rolls = IMMORTAL_MAX_ROLLS
            if guild_id not in captain_draft_state:
                all_pairs = get_all_captain_pairs(lobby_players[guild_id])
                captain_draft_state[guild_id] = {
                    "pairs": all_pairs,
                    "index": 0
                }
            draft_state = captain_draft_state[guild_id]
            draft_state["index"] = (draft_state["index"] + 1) % (max_rolls + 1)
            captains, pool, _ = draft_state["pairs"][draft_state["index"]]
            original_teams[guild_id] = (captains, pool)
            embed = build_immortal_embed(captains, pool, guild, draft_state["index"])
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
def build_lobby_embed(guild, mode="regular"):
    guild_id = guild.id
    if guild_id not in inhouse_mode:
        inhouse_mode[guild_id] = load_inhouse_mode_for_guild(guild.id)
    mode = inhouse_mode[guild_id]
    embed = discord.Embed(
        title="DotA2 Inhouse",
        description=f"**Mode:** `{mode.capitalize()}`\n({len(lobby_players[guild.id])}/10)",
        color=discord.Color.purple()
    )
    for _, name, mmr in lobby_players.get(guild_id, []):
        embed.add_field(name=name, value=str(mmr), inline=True)
    password = load_lobby_password_for_guild(guild.id)
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

def build_immortal_embed(captains, pool, guild, reroll_count):
    c1, c2 = captains
    embed = discord.Embed(
        title="🛡️ Immortal Draft Inhouse",
        description=f"Captains: {c1[1]} ({c1[2]}) vs {c2[1]} ({c2[2]})\nRoll #{reroll_count}/{IMMORTAL_MAX_ROLLS}",
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

async def format_live_match_embed(match, guild):
    radiant_score = match["scoreboard"]["radiant"]["score"]
    dire_score = match["scoreboard"]["dire"]["score"]
    match_time = match["scoreboard"]["duration"]
    minutes = int(match_time) // 60
    seconds = int(match_time) % 60
    timer = f"{minutes}:{seconds:02d}"

    league_id = match.get("league_id", "N/A")
    match_id = match.get("match_id", "N/A")

    # Determine embed color
    if radiant_score > dire_score:
        color = discord.Color.green()
    elif dire_score > radiant_score:
        color = discord.Color.red()
    else:
        color = discord.Color.blurple()

    embed = discord.Embed(
        title="🏆 Live League Match",
        description=f"⏱️ **{timer}** — **Radiant {radiant_score} : {dire_score} Dire**",
        color=color
    )

    radiant_players = []
    dire_players = []

    for player in match.get("players", []):
        hero_id = player.get("hero_id", 0)
        if hero_id == 0:
            continue

        team = player.get("team", 0)
        steam_id = player.get("account_id", 0)
        name = await get_display_name_or_steam(steam_id, guild)

        hero_name = hero_id_map.get(str(hero_id), f"Hero {hero_id}")
        player_entry = f"{name} ({hero_name})"

        if team == 0 and len(radiant_players) < 5:
            radiant_players.append(player_entry)
        elif team == 1 and len(dire_players) < 5:
            dire_players.append(player_entry)

    # Catch unexpected counts
    if len(radiant_players) != 5 or len(dire_players) != 5:
        print(f"[WARN] Expected 5 players per team. Got Radiant={len(radiant_players)}, Dire={len(dire_players)}")

    embed.add_field(name="**Radiant**", value="\n".join(radiant_players), inline=True)
    embed.add_field(name="**Dire**", value="\n".join(dire_players), inline=True)

    embed.add_field(
        name="Info",
        value=f"🆔 League ID: `{league_id}`\n🧾 Match ID: `{match_id}`",
        inline=False
    )

    return embed


bot.run(TOKEN)