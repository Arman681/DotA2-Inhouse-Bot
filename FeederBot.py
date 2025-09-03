# ------------------------------------------------------------
# FeederBot - Discord Inhouse Lobby Bot
# Author: Arman Hasan
# Created: June 2025
# Location: Ft. Lauderdale, Florida
# Description: A Discord bot for managing DotA2 inhouse lobbies,
#              including MMR tracking, team balancing, and lobby alerts.
# ------------------------------------------------------------
import warnings
warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    message="Detected filter using positional arguments",
    module="google.cloud.firestore_v1.base_collection"
)
import aiohttp, asyncio, os, json, random
from typing import Optional
import discord, itertools
import firebase_setup  # ensures Firebase is initialized before anything else
from itertools import combinations
from concurrent.futures import ThreadPoolExecutor
from discord.ext import commands, tasks
from dotenv import load_dotenv
from firebase_admin import firestore
from commands import attach_commands
from mmr_manager import adjust_mmr, get_inhouse_mmr, get_top_players
from betting_manager import clear_guild_bets, get_balance, place_bet, resolve_bets, clear_all_bets, update_balance
from match_tracker import fetch_match_result
from immortal_draft import ImmortalDraftSession, Candidate

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
STRATZ_TOKEN = os.getenv("STRATZ_TOKEN")
STEAM_API_KEY = os.getenv("STEAM_API_KEY")
# Replace this with your actual Discord user ID
GLOBAL_ADMIN_ID = 187959278949105664

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
live_channel_ids = {}      # {guild_id: channel_id}
active_match_ids = {}      # {guild_id: match_id}
live_embed_messages = {}   # {guild_id: message}
polling_tasks = {}         # {guild_id: asyncio.Task} for per-server polling
match_tracking_start_times = {}  # {guild_id: unix_timestamp}
random_polling_flags = {}        # {guild_id: True/False}
valid_team_combos = {}     # {guild_id: int} for how many valid team combinations were found
prefix_cache = {}          # {guild_id: prefix}
display_name = {}          # {steam_id: display_name}
# Debug log de-dupers to avoid noisy repeats during polling
_last_fetch_stats = {}         # {guild_id: (checked_total, passed_total)}
_last_active_match_id = {}     # {guild_id: last_match_id}
_last_selected_match_id = {}   # {guild_id: selected_match_id}
immortal_draft_running: dict[int, bool] = {}   # guild_id -> started?
# --- Captain selection policy ---
ALLOWED_CAPTAIN_POLICIES = {"min_diff", "top2_if_close", "simulate"}
captain_policy_by_guild: dict[int, str] = {}          # e.g. { 123: "min_diff" }
captain_policy_threshold_by_guild: dict[int, int] = {} # e.g. { 123: 150 } (only for top2_if_close)

MAX_ROLLS = 5  # for regular
IMMORTAL_MAX_ROLLS = 3  # for immortal
MMR_ROLE_OVERRULE_THRESHOLD = 1500
ROLE_FIT_WEIGHT = 10  # You can adjust this based on how much role fit matters

HERO_CACHE_FILE = "hero_id_map.json"
hero_id_map = {}
if os.path.exists(HERO_CACHE_FILE):
    try:
        with open(HERO_CACHE_FILE, "r") as f:
            hero_id_map = json.load(f)
    except Exception:
        hero_id_map = {}

# ============================== 🛠️ Bot Configuration ==============================

# Resolves the correct command prefix for the bot, based on the message's guild.
async def resolve_command_prefix(bot, message):
    if message.guild:
        return load_guild_prefix(message.guild.id)
    return "!"  # fallback default for DMs

bot = commands.Bot(command_prefix=resolve_command_prefix, intents=intents, help_command=None)

http_session: aiohttp.ClientSession | None = None
def get_http_session() -> aiohttp.ClientSession:
    # Simple accessor that always returns a live session
    global http_session
    if http_session is None or http_session.closed:
        # NOTE: we only hit this during very early startup or after a reconnect
        http_session = aiohttp.ClientSession()
    return http_session

async def close_http_session():
    global http_session
    if http_session and not http_session.closed:
        await http_session.close()

# ========================================================================================================================
# ============================================ ⚙️ Core Functions & Utilities ============================================
# ========================================================================================================================

# Polls a live Dota 2 match, updates Discord with status, and resolves bets/MMR after the match ends
async def poll_live_match(match_id, guild, random_mode=False):
    print(f"[MATCH] Started polling match {match_id} for guild {guild.name} (random_mode={random_mode})")
    # 💡 Prevent using any previous embed
    live_embed_messages.pop(guild.id, None)
    channel_id = live_channel_ids.get(guild.id)
    channel = bot.get_channel(channel_id) if channel_id else None
    # Poll until Steam no longer reports a live match
    while True:
        await asyncio.sleep(15)
        try:
            match = await fetch_live_match_for_guild(guild.id, random_mode=random_mode)
            if not match:
                print(f"[MATCH] Match {match_id} no longer reported as live. Stopping Steam polling.")
                break  # Exit polling loop and move to STRATZ result retry
            # Update embed every 15 seconds
            embed = await format_live_match_embed(match, guild)
            if channel:
                prev_msg = live_embed_messages.get(guild.id)
                if prev_msg:
                    try:
                        await prev_msg.edit(embed=embed)
                    except discord.NotFound:
                        new_msg = await channel.send(embed=embed)
                        live_embed_messages[guild.id] = new_msg
                else:
                    new_msg = await channel.send(embed=embed)
                    live_embed_messages[guild.id] = new_msg
        except Exception as e:
            print(f"[ERROR] poll_live_match() for guild {str(guild.id)}: {e}")
    # Match is no longer live — try STRATZ for up to ~5 minutes
    max_retries = 10
    retry_delay = 30  # seconds
    result = None
    for attempt in range(max_retries):
        result = await asyncio.to_thread(fetch_match_result, match_id)
        if result:
            break
        print(f"[RETRY] No match result yet for match {match_id}. Retrying in {retry_delay} seconds... (attempt {attempt + 1}/{max_retries})")
        await asyncio.sleep(retry_delay)
    if not result:
        print(f"[ERROR] No match result found for match {match_id} after {max_retries} attempts. Skipping bet resolution.")
        # Clean up memory even if result is missing
        active_match_ids.pop(guild.id, None)
        polling_tasks.pop(guild.id, None)
        random_polling_flags.pop(guild.id, None)
        match_tracking_start_times.pop(guild.id, None)
        live_embed_messages.pop(guild.id, None)
        _last_fetch_stats.pop(guild.id, None)
        _last_active_match_id.pop(guild.id, None)
        _last_selected_match_id.pop(guild.id, None)
        if channel:
            await channel.send("Match ended but no result was found. Polling has been stopped.")
        return
    # Proceed with resolution
    winning_team = "radiant" if result["radiant_win"] else "dire"
    winner_ids = map_steam_ids_to_discord_ids(result["radiantplayers"] if result["radiant_win"] else result["direplayers"])
    loser_ids = map_steam_ids_to_discord_ids(result["direplayers"] if result["radiant_win"] else result["radiantplayers"])
    resolve_bets(guild.id, winning_team)
    if not random_mode:
        try:
            await adjust_mmr(bot, winner_ids, loser_ids, guild.id)
        except Exception as e:
            print(f"[ERROR] Failed to adjust MMR: {e}")
    # Award 50 coins to all players who played in the match
    all_player_ids = winner_ids + loser_ids
    batch = db.batch()
    for discord_id in all_player_ids:
        wallet_ref = db.collection("wallets").document(str(guild.id)).collection("users").document(str(discord_id))
        batch.set(wallet_ref, {"balance": firestore.Increment(50)}, merge=True)
    try:
        batch.commit()
        print(f"[DEBUG] Awarded 50 coins to {len(all_player_ids)} participants in match {match_id}")
    except Exception as e:
        print(f"[ERROR] Failed to commit batch coins: {e}")
    # Send match summary
    try:
        await channel.send(f"Match `{match_id}` has ended with a {winning_team} victory. Bets have been resolved and Inhouse-MMR updated.\n All participants received **50 coins** for playing.")
        print(f"[DEBUG] Match summary sent to channel ID: {channel.id}")
    except Exception as e:
        print(f"[ERROR] Failed to send match summary: {e}")
    # Clean up memory
    active_match_ids.pop(guild.id, None)
    polling_tasks.pop(guild.id, None)
    random_polling_flags.pop(guild.id, None)
    match_tracking_start_times.pop(guild.id, None)
    live_embed_messages.pop(guild.id, None)
    _last_fetch_stats.pop(guild.id, None)
    _last_active_match_id.pop(guild.id, None)
    _last_selected_match_id.pop(guild.id, None)

# =============================== 🔐 Permission Checks ===============================

# Custom check that allows admins or specific roles to use commands
def is_admin_or_has_role():
    async def predicate(ctx):
        if ctx.author.id == GLOBAL_ADMIN_ID:
            return True
        if ctx.author.guild_permissions.administrator:
            return True
        admin_roles = ["Inhouse Admin"]
        return any(role.name in admin_roles for role in ctx.author.roles)
    return commands.check(predicate)

# Custom check that allows only global admin to use commands
def is_global_admin():
    async def predicate(ctx):
        return ctx.author.id == GLOBAL_ADMIN_ID
    return commands.check(predicate)

# Utility function version of the role check (returns True/False instead of being a decorator)
async def user_is_admin_or_has_role(member):
    if member.id == GLOBAL_ADMIN_ID:
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

# Loads a player's saved config data from Firestore using their Discord user ID.
def load_player_config(user_id):
    doc = db.collection("players").document(str(user_id)).get()
    return doc.to_dict() if doc.exists else None

# Saves a custom command prefix for a specific Discord server (guild) to Firestore.
def save_guild_prefix(guild_id, prefix, server_name=None, set_by=None):
    data = {
        "prefix": prefix,
        "prefix_set_by": set_by,
        "prefix_timestamp": firestore.SERVER_TIMESTAMP,
    }
    doc_ref = db.collection("guild_specific_info").document(str(guild_id))
    doc_ref.set({"prefix": data}, merge=True)
    prefix_cache[guild_id] = prefix

# Loads the custom command prefix for a guild from Firestore; defaults to "!" if not set
def load_guild_prefix(guild_id):
    if guild_id in prefix_cache:
        return prefix_cache[guild_id]
    doc = db.collection("guild_specific_info").document(str(guild_id)).get()
    if doc.exists:
        data = doc.to_dict()
        prefix = data.get("prefix", {}).get("prefix", "!")
        prefix_cache[guild_id] = prefix
        return prefix
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

# Saves the current inhouse mode ("regular" or "immortal") for a guild to Firestore
def save_inhouse_mode_for_guild(guild_id, mode, server_name=None, set_by=None):
    data = {
        "mode": mode,
        "mode_set_by": str(set_by),
        "mode_timestamp": firestore.SERVER_TIMESTAMP,
        "server_name": server_name,
        }
    doc_ref = db.collection("guild_specific_info").document(str(guild_id))
    doc_ref.set({"inhouse_mode": data}, merge=True)

# Loads the current inhouse mode for a guild from Firestore; defaults to "regular"
def load_inhouse_mode_for_guild(guild_id):
    doc = db.collection("guild_specific_info").document(str(guild_id)).get()
    if doc.exists:
        return doc.to_dict().get("inhouse_mode", {}).get("mode", "regular")
    return "regular"

# Saves the league ID bound to a Discord server for live match tracking
def save_league_guild_mapping(guild_id: int, league_id: int, server_name=None, bound_by=None):
    data = {
        "bound_league_id": str(league_id),
        "league_id_bound_by": str(bound_by),
        "league_bind_timestamp": firestore.SERVER_TIMESTAMP,
        "server_name": server_name,
    }
    doc_ref = db.collection("guild_specific_info").document(str(guild_id))
    doc_ref.set({"league_id": data}, merge=True)

# Saves the lobby message ID for a guild to Firestore
def save_lobby_message_id(guild_id, message_id):
    data = {
        "lobby_message_id": message_id
    }
    doc_ref = db.collection("guild_specific_info").document(str(guild_id))
    doc_ref.set({"lobby_message_id": data}, merge=True)

# Loads the saved lobby message ID for a guild; returns 0 if not set
def load_lobby_message_id(guild_id):
    doc = db.collection("guild_specific_info").document(str(guild_id)).get()
    if doc.exists:
        return doc.to_dict().get("lobby_message_id", {}).get("lobby_message_id", 0)
    return None

# Saves the list of current lobby players (ID, name, MMR) for a guild to Firestore
def save_lobby_players(guild_id, players):
    formatted = [{"id": uid, "name": name, "mmr": mmr} for uid, name, mmr in players]
    doc_ref = db.collection("guild_specific_info").document(str(guild_id))
    doc_ref.set({"lobby_players": formatted}, merge=True)

# Loads the list of saved lobby players for a guild from Firestore
def load_lobby_players(guild_id):
    doc = db.collection("guild_specific_info").document(str(guild_id)).get()
    if doc.exists:
        raw = doc.to_dict().get("lobby_players", [])
        return [(p["id"], p["name"], p["mmr"]) for p in raw if "id" in p and "name" in p and "mmr" in p]
    return []

# Saves whether preferred roles are enabled for a guild to Firestore
def save_preferred_roles_setting(guild_id, enabled, set_by=None):
    data = {
        "preferred_roles_enabled": enabled,
        "preferred_roles_set_by": str(set_by),
        "preferred_roles_timestamp": firestore.SERVER_TIMESTAMP
    }
    doc_ref = db.collection("guild_specific_info").document(str(guild_id))
    doc_ref.set({"preferred_roles_setting": data}, merge=True)

# Loads the preferred roles setting for a guild; defaults to True if not set
def load_preferred_roles_setting(guild_id):
    doc = db.collection("guild_specific_info").document(str(guild_id)).get()
    if doc.exists:
        return doc.to_dict().get("preferred_roles_setting", {}).get("preferred_roles_enabled", True)  # Default: enabled
    return True

def get_captain_policy(guild_id: int) -> tuple[str, int | None]:
    # Memory cache first
    if guild_id in captain_policy_by_guild:
        return captain_policy_by_guild[guild_id], captain_policy_threshold_by_guild.get(guild_id)
    # Load from Firestore
    doc = db.collection("guild_specific_info").document(str(guild_id)).get()
    if doc.exists:
        data = doc.to_dict() or {}
        nested = data.get("captain_policy", {})
        pol = nested.get("captain_policy", "min_diff")
        thr = nested.get("captain_policy_threshold")  # may be None
    else:
        pol, thr = "min_diff", None
    captain_policy_by_guild[guild_id] = pol
    if thr is not None:
        captain_policy_threshold_by_guild[guild_id] = int(thr)
    else:
        captain_policy_threshold_by_guild.pop(guild_id, None)
    return pol, thr

def set_captain_policy(guild_id: int, policy: str, threshold: int | None = None, set_by: str | None = None) -> None:
    if policy not in ALLOWED_CAPTAIN_POLICIES:
        raise ValueError(f"Invalid policy: {policy}")
    # update in-memory cache
    captain_policy_by_guild[guild_id] = policy
    if threshold is not None:
        captain_policy_threshold_by_guild[guild_id] = int(threshold)
    elif policy != "top2_if_close":
        captain_policy_threshold_by_guild.pop(guild_id, None)
    data = {
        "captain_policy": policy,
        "captain_policy_set_by": set_by,
        "captain_policy_timestamp": firestore.SERVER_TIMESTAMP,
    }
    if policy == "top2_if_close":
        data["captain_policy_threshold"] = int(threshold or 150)
    doc_ref = db.collection("guild_specific_info").document(str(guild_id))
    doc_ref.set({"captain_policy": data}, merge=True)

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
def convert_to_steam32(steam_id_input):
    try:
        # If input is a string, remove spaces and convert to int
        if isinstance(steam_id_input, str):
            steam_id_input = int(steam_id_input.strip().replace(" ", ""))
        elif not isinstance(steam_id_input, int):
            # Reject unsupported types early
            return None
        # Perform 64-bit → 32-bit conversion
        if steam_id_input > 76561197960265728:
            return steam_id_input - 76561197960265728
        return steam_id_input
    except (ValueError, TypeError):
        return None

# Sends a GraphQL query to STRATZ and then OpenDota to fetch a user's seasonRank and maps it to an estimated MMR.
async def fetch_mmr(steam_id, max_retries: int = 2):
    """
    Returns (mmr:int|None, season_or_tier:int|None).
    - Primary: STRATZ seasonRank -> map via season_rank_to_mmr
    - Fallback: OpenDota rank_tier -> map via season_rank_to_mmr
    Accepts either 64-bit or 32-bit steam_id.
    """
    steam_id = convert_to_steam32(steam_id)
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
    # ---------- 1) STRATZ ----------
    for attempt in range(max_retries):
        try:
            async with get_http_session().post(url, json=query, headers=headers, timeout=8) as response:
                if response.status == 200:
                    data = await response.json()
                    player_data = data.get("data", {}).get("player", {})
                    if player_data and player_data.get("steamAccount"):
                        season_rank = player_data["steamAccount"].get("seasonRank")
                        if season_rank:
                            mmr = season_rank_to_mmr.get(season_rank)
                            return mmr, season_rank, "STRATZ"
                        else:
                            # <-- 200 but no rank: log why we're falling back
                            print(f"[INFO] STRATZ 200 but no seasonRank for steam_id={steam_id}; falling back to OpenDota")
                            break  # No seasonRank -> skip retries and go fallback
                else:
                    # Log non-200 response
                    txt = (await response.text()).strip()
                    print(
                        f"[WARN] STRATZ {response.status} "
                        f"(attempt {attempt+1}/{max_retries}) "
                        f"for steam_id={steam_id}: {txt[:180]}"
                    )
                    if response.status in (403, 429, 500, 502, 503, 504):
                        continue  # Retry on transient/blocked statuses
                    break  # Non-retryable error, break out
        except Exception as e:
            print(
                f"[ERROR] STRATZ request failed "
                f"(attempt {attempt+1}/{max_retries}) for steam_id={steam_id}: {e}"
            )
    # ---------- 2) OpenDota fallback ----------
    try:
        od_url = f"https://api.opendota.com/api/players/{steam_id}"
        async with get_http_session().get(od_url, timeout=8) as r:
            if r.status != 200:
                txt = (await r.text()).strip()
                print(f"[WARN] OpenDota {r.status} for steam_id={steam_id}: {txt[:180]}")
                return None, None, None
            j = await r.json()
            rank_tier = j.get("rank_tier")  # e.g. 55 => Legend 5
            if not rank_tier:
                return None, None, None
            mmr = season_rank_to_mmr.get(rank_tier)
            return mmr, rank_tier, "OpenDota"
    except Exception as e:
        print(f"[ERROR] OpenDota fallback failed for steam_id={steam_id}: {e}")
    return None, None, None

# Fetches the current live Dota 2 match for a guild using Steam API, filtered by bound league ID or in random mode
async def fetch_live_match_for_guild(guild_id, random_mode=False):
    """Fetches a live match for the manually bound league_id in this guild."""
    # ✅ Step 1: Fetch bound_league_id from Firestore
    doc_ref = db.collection("guild_specific_info").document(str(guild_id))
    doc = doc_ref.get()
    if not doc.exists:
        print(f"[WARN] No guild_specific_info found for guild {guild_id}")
        return None
    league_info = doc.to_dict().get("league_id", {})
    bound_league_id = league_info.get("bound_league_id")
    if not random_mode and not bound_league_id:
        print(f"[WARN] No bound_league_id found in Firestore for guild {guild_id}")
        return None
    # ✅ Step 2: Fetch matches from Steam API
    url = "https://api.steampowered.com/IDOTA2Match_570/GetLiveLeagueGames/v1/"
    params = {"key": STEAM_API_KEY}
    try:
        async with get_http_session().get(url, params=params, timeout=5) as response:
            if response.status != 200:
                print(f"[ERROR] Steam API returned {response.status}")
                return None
            result = await response.json()
            matches = result.get("result", {}).get("games", [])
            valid_matches = []
            for m in matches:
                has_scoreboard = bool(m.get("scoreboard"))
                if random_mode:
                    # In random mode: require a scoreboard and duration >= 1800s
                    if not has_scoreboard:
                        continue
                    dur = m["scoreboard"].get("duration", 0)
                    if dur < 1800:
                        continue
                    valid_matches.append(m)
                else:
                    # Normal mode: allow draft/lobby matches (no scoreboard yet)
                    if not has_scoreboard:
                        valid_matches.append(m)
                        continue
                    # Scoreboard present -> accept (no duration gate in normal mode)
                    valid_matches.append(m)
            if not valid_matches:
                if guild_id in active_match_ids:
                    print(f"[INFO] Clearing expired match for guild {guild_id}")
                    del active_match_ids[guild_id]
                    _last_fetch_stats.pop(guild_id, None)
                    _last_active_match_id.pop(guild_id, None)
                    _last_selected_match_id.pop(guild_id, None)
                return None
            stats = (len(matches), len(valid_matches))
            if _last_fetch_stats.get(guild_id) != stats:
                print(f"[DEBUG] Checked {stats[0]} total live matches from Steam API.")
                print(f"[DEBUG] {stats[1]} passed scoreboard and duration filters.")
                _last_fetch_stats[guild_id] = stats
            # ✅ Step 3: Filter by league ID
            bound_matches = valid_matches if random_mode else [
                m for m in valid_matches if str(m.get("league_id")) == str(bound_league_id)
            ]
            if not bound_matches:
                print(f"[INFO] No live matches found for bound league_id {bound_league_id} in guild {guild_id}")
                return None
            # ✅ Step 4: Reuse previous match ID if still valid
            last_match_id = active_match_ids.get(guild_id)
            prev_active_id = _last_active_match_id.get(guild_id)
            if prev_active_id != last_match_id:
                if last_match_id:
                    print(f"[DEBUG] Step 4 triggered: active_match_ids[{guild_id}] = {last_match_id}")
                else:
                    print(f"[DEBUG] Step 4 skipped: No active match found for guild {guild_id}")
                _last_active_match_id[guild_id] = last_match_id
            selected_match = next((m for m in bound_matches if m.get("match_id") == last_match_id), None)
            if selected_match is None:
                if random_mode and last_match_id:
                    print(f"[INFO] Tracked random match {last_match_id} is no longer valid. Waiting for match resolution.")
                    return None
                selected_match = random.choice(bound_matches)
                active_match_ids[guild_id] = selected_match["match_id"]
            # ---- Part C: only log when the selected match id changes ----
            sel_id = selected_match["match_id"]
            prev_sel_id = _last_selected_match_id.get(guild_id)
            if prev_sel_id != sel_id:
                if last_match_id and sel_id == last_match_id:
                    print(f"[DEBUG] Reusing existing match_id {last_match_id} for guild {guild_id}")
                else:
                    print(f"[DEBUG] Picked new match_id {sel_id} for guild {guild_id}")
                _last_selected_match_id[guild_id] = sel_id
            # -------------------------------------------------------------
            selected_match["guild_id"] = guild_id
            return selected_match
    except Exception as e:
        print(f"[ERROR] fetch_live_match_for_guild() Steam API error: {e}")
        return None

# Loads hero ID-to-name mapping from local cache or Steam API if cache is missing or invalid
async def fetch_hero_id_to_name_map():
    # Try loading from local cache first
    if os.path.exists(HERO_CACHE_FILE):
        try:
            with open(HERO_CACHE_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
                    print("[INFO] ✅ Loaded hero ID cache from local file.")
                    return data
                else:
                    print("[WARN] Invalid hero cache format. Refetching from API...")
        except Exception as e:
            print(f"[ERROR] Failed to load local hero cache: {e}")
    print("[INFO] 📡 Fetching hero data from Steam API...")
    url = "https://api.steampowered.com/IEconDOTA2_570/GetHeroes/v1/"
    params = {
        "language": "en_us",
        "key": STEAM_API_KEY
    }
    try:
        async with get_http_session().get(url, params=params) as response:
            response.raise_for_status()
            data = await response.json()
            heroes = data.get("result", {}).get("heroes", [])
            hero_map = {str(hero["id"]): hero["localized_name"] for hero in heroes}
            with open(HERO_CACHE_FILE, "w") as f:
                json.dump(hero_map, f)
            print("[INFO] 💾 Saved hero ID map to cache.")
            return hero_map
    except Exception as e:
        print(f"[ERROR] Failed to fetch hero data from Steam API: {e}")
        return {}

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

# Retrieves the Discord user ID linked to a given Steam ID from Firestore
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

# Converts a list of Steam IDs into their corresponding Discord user IDs
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

# Gets a player's Steam display name using their 32-bit account ID
async def get_steam_display_name(account_id_32):
    try:
        steam_id_64 = str(int(account_id_32) + 76561197960265728)
        url = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
        params = {
            "key": STEAM_API_KEY,
            "steamids": steam_id_64
        }
        async with get_http_session().get(url, params=params, timeout=5) as response:
            data = await response.json()
            players = data.get("response", {}).get("players", [])
            if players:
                steam_display_name = players[0].get("personaname", f"SteamID {account_id_32}")
                display_name[account_id_32] = steam_display_name
                return steam_display_name
    except Exception as e:
        print(f"[ERROR] get_steam_display_name: {e}")
    return f"SteamID {account_id_32}"

# Gets a player's display name from Discord if mapped, otherwise fetches from Steam
async def get_display_name(account_id_32, guild):
    if account_id_32 in display_name:
        return display_name[account_id_32]
    discord_id = get_discord_id_from_steam_id(account_id_32)
    if discord_id and guild:
        member = guild.get_member(int(discord_id))
        if member:
            discord_display_name = member.display_name
            display_name[account_id_32] = discord_display_name
            return discord_display_name
    return await get_steam_display_name(account_id_32)

# Periodic background task that updates all players' MMR values from STRATZ in Firebase, and refreshes lobby embeds across all servers.
@tasks.loop(hours=18)
async def refresh_all_mmrs():
    print("Refreshing MMRs (Firebase)...")
    updates_log = []  # collected and printed once at the end
    players_ref = db.collection("players").stream()
    for doc in players_ref:
        user_id = doc.id
        data = doc.to_dict()
        steam_id = data.get("steam_id")
        if not steam_id:
            continue
        discord_nickname = data.get("discord_nickname", str(user_id))
        old_mmr    = data.get("mmr")
        old_season = data.get("seasonRank")
        old_source = data.get("mmrSource")
        # Fetch (mmr, season_or_tier, source), where season_or_tier is STRATZ seasonRank or OpenDota rank_tier
        try:
            mmr, season_or_tier, source = await fetch_mmr(steam_id)
            # throttle between users
            await asyncio.sleep(0.3) # ensure stratz api call limit is not exceeded
        except Exception as e:
            print(f"[ERROR] Failed to fetch MMR for {steam_id} ({user_id}): {e}")
            await asyncio.sleep(0.3)
            continue
        # Skip if: no rank, Immortal-or-higher, or no mapped MMR
        if (season_or_tier is None) or (season_or_tier >= 80) or (mmr is None):
            continue
        # Only write if something actually changed
        changed = (
            old_mmr != mmr or
            old_season != season_or_tier or
            old_source != source
        )
        if changed:
            try:
                db.collection("players").document(str(user_id)).update({
                    "mmr": mmr,
                    "seasonRank": season_or_tier,   # stores either seasonRank or rank_tier
                    "mmrSource": source,            # "STRATZ" or "OpenDota"
                    "mmrUpdatedAt": firestore.SERVER_TIMESTAMP
                })
                updates_log.append({
                    "discord_nickname": discord_nickname,
                    "user_id": user_id,
                    "steam_id": steam_id,
                    "old_mmr": old_mmr, "new_mmr": mmr,
                    "old_season": old_season, "new_season": season_or_tier,
                    "old_source": old_source, "new_source": source,
                })
            except Exception as e:
                print(f"[ERROR] Failed to update Firestore for {user_id}: {e}")
    # Refresh lobby embeds across all servers
    try:
        await update_all_lobbies()
    except Exception as e:
        print(f"[ERROR] Failed to update lobby embeds: {e}")
    # Final one-shot debug summary
    if updates_log:
        print("[MMR REFRESH CHANGES]")
        for u in updates_log:
            print(
                f" {u['discord_nickname']} (user_id={u['user_id']}, steam_id={u['steam_id']}) | "
                f"MMR: {u['old_mmr']} -> {u['new_mmr']} | "
                f"Season: {u['old_season']} -> {u['new_season']} | "
                f"Source: {u['old_source']} -> {u['new_source']}"
            )
    else:
        print("[MMR REFRESH] No changes detected.")
    print("Refreshed all MMRs and lobby embeds.")

# Assigns players to roles based on their preferences and MMR, prioritizing optimal fit
def assign_roles_with_preferences(team, preference_map=None, mmr_map=None):
    assigned = {}
    unassigned_players = list(team)
    if preference_map is None or mmr_map is None:
        preference_map = {}
        mmr_map = {}
        for player in team:
            uid = str(player[0])
            mmr_map[uid] = player[2]
            doc = db.collection("players").document(uid).get()
            data = doc.to_dict() if doc.exists else None
            preference_map[uid] = data.get("preferred_roles", [1, 2, 3, 4, 5]) if (data and isinstance(data.get("preferred_roles"), list)) else [1, 2, 3, 4, 5]
    for role in range(1, 6):
        best_candidate = None
        best_rank = 999
        best_mmr = -1
        for player in unassigned_players:
            uid = str(player[0])
            prefs = preference_map.get(uid, [])
            mmr = mmr_map.get(uid, 0)
            if role in prefs:
                rank = prefs.index(role)
                if (rank < best_rank) or (rank == best_rank and mmr > best_mmr):
                    best_candidate = player
                    best_rank = rank
                    best_mmr = mmr
        # Check if any higher-MMR player deserves override
        for player in unassigned_players:
            uid = str(player[0])
            mmr = mmr_map[uid]
            prefs = preference_map.get(uid, [])
            if role in prefs:
                if best_candidate:
                    best_uid = str(best_candidate[0])
                    if (uid != best_uid and mmr - mmr_map[best_uid] >= MMR_ROLE_OVERRULE_THRESHOLD and prefs.index(role) <= 2):
                        best_candidate = player
                        break
        if best_candidate:
            assigned[role] = best_candidate
            unassigned_players.remove(best_candidate)
    return assigned

# Generates all possible unique captain pairs from the player list, sorted by MMR difference
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

# Choose which captain pair to start on among all_pairs ([(captains, pool, diff), ...])
def choose_captain_pair_index(
    players: list[tuple[int, str, int]],   # [(user_id, name, mmr)]
    all_pairs: list[tuple[tuple[tuple[int, str, int], tuple[int, str, int]], list[tuple[int, str, int]], int]],
    policy: str = "min_diff",              # "min_diff" | "top2_if_close" | "simulate"
    threshold: int = 150                   # used by top2_if_close
) -> int:
    """
    Return the index into all_pairs for the preferred captain pair.
    all_pairs[i] = (captains=(pA, pB), pool=[...8 players...], diff=<mmr gap between captains>)
    """
    # Safety: empty/all sanity
    if not all_pairs:
        return 0
    # min_diff -> all_pairs already sorted by diff asc in your current code
    if policy == "min_diff":
        return 0
    # top2_if_close -> if top-2 gap <= threshold, pick that pair; else min_diff (index 0)
    if policy == "top2_if_close":
        sorted_players = sorted(players, key=lambda x: x[2])  # low->high
        top2 = (sorted_players[-2], sorted_players[-1])
        top2_ids = {top2[0][0], top2[1][0]}
        top2_diff = abs(top2[1][2] - top2[0][2])
        if top2_diff <= threshold:
            for i, (caps, _pool, _d) in enumerate(all_pairs):
                if {caps[0][0], caps[1][0]} == top2_ids:
                    return i
        return 0
    # simulate -> evaluate all pairs by expected team MMR diff after greedy 1-2-2-2-1 picks
    if policy == "simulate":
        def simulate_score(caps, pool):
            # Include captain MMRs in team totals (they play on the teams they captain)
            cap1, cap2 = caps
            totals = {"cap1": cap1[2], "cap2": cap2[2]}
            remaining = sorted(pool, key=lambda x: x[2])  # low->high
            pick_order = [("cap1", 1), ("cap2", 2), ("cap1", 2), ("cap2", 2), ("cap1", 1)]
            for who, cnt in pick_order:
                for _ in range(cnt):
                    if not remaining:
                        break
                    pick = remaining.pop()  # greedy: take highest remaining
                    totals[who] += pick[2]
            return abs(totals["cap1"] - totals["cap2"])
        best_i, best_score = 0, None
        for i, (caps, pool, _d) in enumerate(all_pairs):
            s = simulate_score(caps, pool)
            if best_score is None or s < best_score:
                best_i, best_score = i, s
        return best_i
    # Fallback
    return 0

# Retrieves a player's saved role preferences from Firestore
def get_preferred_roles(player_id):
    doc = db.collection("players").document(str(player_id)).get()
    if doc.exists:
        data = doc.to_dict()
        if "preferred_roles" in data and isinstance(data["preferred_roles"], list):
            return data["preferred_roles"]
    return None  # Neutral: no preferences set

async def refresh_lobby_member_mmr(guild: discord.Guild, member: discord.Member, new_mmr=None):
    """If member is in the current guild's lobby, update their MMR (if provided
    or fetch from Firestore) and refresh the lobby embed."""
    gid = guild.id
    if gid not in lobby_players:
        return  # nothing to do
    # Find user in the lobby list [(user_id, name, mmr), ...]
    for idx, (uid, name, old_mmr) in enumerate(list(lobby_players[gid])):
        if uid == member.id:
            mmr_val = new_mmr
            if mmr_val is None:
                snap = db.collection("players").document(str(member.id)).get()
                data = snap.to_dict() if snap.exists else {}
                mmr_val = data.get("mmr", old_mmr)
            lobby_players[gid][idx] = (uid, name, mmr_val)
            save_lobby_players(gid, lobby_players[gid])
            await update_lobby_embed(guild)   # edits the existing lobby message
            break

# FeederBot.py
async def start_immortal_draft(bot, guild: discord.Guild, channel: discord.TextChannel):
    """
    Launch an Immortal Draft using the captains/pool chosen when 🚀 was pressed.
    Reads from captain_draft_state[guild_id] and lobby_players[guild_id].
    """
    gid = guild.id
    # Require immortal mode + a full lobby
    mode = inhouse_mode.get(gid)
    if mode != "immortal":
        await channel.send("This command only works after starting an **Immortal** lobby.")
        return
    players = lobby_players.get(gid, [])
    if len(players) != 10:
        await channel.send("Immortal Draft requires exactly **10** players in the lobby.")
        return
    # Pull captains + pool from the state set when 🚀 was pressed
    state = captain_draft_state.get(gid)
    if not state or "pairs" not in state or "index" not in state:
        await channel.send("No captains found. Press 🚀 in the Immortal lobby first.")
        return
    try:
        captains, pool, _diff = state["pairs"][state["index"]]
    except Exception:
        await channel.send("Could not read captain pair. Try pressing 🚀 again.")
        return
    # Resolve captains (tuples are (user_id, name, mmr))
    c1_id, _c1_name, _c1_mmr = captains[0]
    c2_id, _c2_name, _c2_mmr = captains[1]
    cap1 = guild.get_member(int(c1_id))
    cap2 = guild.get_member(int(c2_id))
    if not cap1 or not cap2:
        await channel.send("One or both captains are no longer in the server.")
        return
    # Build Candidate objects from the 8-player pool (tuples are (user_id, name, mmr))
    candidates = []
    for uid, _name, mmr in pool:
        m = guild.get_member(int(uid))
        if m and not m.bot:
            candidates.append(Candidate(member=m, mmr=int(mmr)))
    if len(candidates) != 8:
        await channel.send("Need **8 non-captain** players available for the draft.")
        return
    # Announce and start
    header = discord.Embed(
        title="Starting Immortal Draft",
        description=(
            f"Captains: {cap1.mention} vs {cap2.mention}\n"
            f"Draft order: **1–2–2–2–1**\n"
            f"Pick clock: **5s per pick** + **60s personal reserve** (cumulative)\n"
            f"Players are shown low→high by **actual MMR**."
        ),
        color=discord.Color.gold()
    )
    await channel.send(embed=header)
    session = ImmortalDraftSession(
        bot=bot,
        guild=guild,
        channel=channel,
        cap1=cap1,
        cap2=cap2,
        candidates=candidates,
    )
    await session.start()
    # Reset the "running" flag when the draft session completes
    if session.timer_task:
        session.timer_task.add_done_callback(
            lambda _t, gid=guild.id: immortal_draft_running.__setitem__(gid, False)
        )
    return session

# ================================ ⚖️ Team Balancing ================================

# Finds all possible 5v5 team splits from a 10-player list and sorts them by MMR balance.
def calculate_balanced_teams(players, guild_id, max_mmr_diff=100):
    # Cache preferences and MMR
    preference_map = {}
    mmr_map = {}
    for uid, _, mmr in players:
        uid_str = str(uid)
        mmr_map[uid_str] = mmr
        doc = db.collection("players").document(uid_str).get()
        data = doc.to_dict() if doc.exists else None
        preferred = data.get("preferred_roles", [1, 2, 3, 4, 5]) if (data and isinstance(data.get("preferred_roles"), list)) else [1, 2, 3, 4, 5]
        preference_map[uid_str] = preferred
    use_roles = load_preferred_roles_setting(guild_id)
    combos_to_score = []
    # Generate valid MMR-balanced combos
    for team1 in itertools.combinations(players, 5):
        team2 = tuple(p for p in players if p not in set(team1))
        mmr1 = sum(p[2] for p in team1) / 5
        mmr2 = sum(p[2] for p in team2) / 5
        mmr_diff = abs(mmr1 - mmr2)
        if mmr_diff > max_mmr_diff:
            continue
        combos_to_score.append((team1, team2, mmr_diff))
    print(f"[INFO] Found {len(combos_to_score)} valid team combinations (MMR diff ≤ {max_mmr_diff})")
    if not combos_to_score:
        # Nothing to score—callers must guard against empty results
        team_rolls[guild_id] = []
        return [], 0
    # Parallel role fit scoring
    def score_combo(combo):
        team1, team2, mmr_diff = combo
        if use_roles:
            score1, roles1 = calculate_role_fit_score(team1, preference_map, mmr_map)
            score2, roles2 = calculate_role_fit_score(team2, preference_map, mmr_map)
        else:
            score1 = score2 = 0
            roles1 = roles2 = None
        total_score = (mmr_diff / 5) - ROLE_FIT_WEIGHT * (score1 + score2)
        return (total_score, team1, team2, score1, score2, roles1, roles2)
    with ThreadPoolExecutor() as executor:
        results = list(executor.map(score_combo, combos_to_score))
    # Sort teams so the most balanced (lowest total_score) come first, then take the best 5
    results.sort(key=lambda x: x[0])
    # Each entry contains: (team1, team2, score1, score2, roles1, roles2)
    top_teams = [(r[1], r[2], r[3], r[4], r[5], r[6]) for r in results[:5]]
    # Cache results for re-rolls
    team_rolls[guild_id] = top_teams
    valid_team_combos[guild_id] = len(results)
    return top_teams, len(results)

# Calculates a team's role fit score by summing how well assigned roles match player preferences
def calculate_role_fit_score(team, preference_map=None, mmr_map=None):
    assignments = assign_roles_with_preferences(team, preference_map, mmr_map)
    total_score = 0
    for role, player in assignments.items():
        uid = str(player[0])
        preferences = preference_map.get(uid) if preference_map else get_preferred_roles(uid)
        if not preferences:
            continue
        try:
            preference_rank = preferences.index(role) + 1
        except ValueError:
            preference_rank = 6
        total_score += preference_rank
    return total_score, assignments

# ========================================================================================================================
# ================================================ 🎯 Bot Event Handlers ================================================
# ========================================================================================================================

# Runs once when the bot starts and begins the MMR refresh task.
@bot.event
async def on_ready():
    global hero_id_to_name
    print(f"{bot.user} is online!")
    active_match_ids.clear()
    clear_all_bets(bot)
    # Cache hero IDs
    hero_id_to_name = await fetch_hero_id_to_name_map()
    # Load live_channel_ids from Firestore
    docs = db.collection("guild_specific_info").stream()
    for doc in docs:
        data = doc.to_dict()
        guild_id = int(doc.id)
        guild = bot.get_guild(guild_id)
        if not guild:
            print(f"[WARN] Could not find guild object for guild ID {guild_id}")
            continue
        # Load live channel ID
        live_channel_id = data.get("live_channel_id", {}).get("live_channel_id", 0)
        try: 
            live_channel_ids[int(doc.id)] = int(live_channel_id)
            """print(f"[DEBUG] Guild {doc.id} (type: {type(doc.id).__name__}) → Channel {live_channel_id} (type: {type(live_channel_id).__name__})")"""
        except (ValueError, TypeError):
            print(f"[WARNING] Skipping guild {doc.id} due to invalid live_channel_id: {live_channel_id}")
        # Restore lobby players
        restored_players = load_lobby_players(guild_id)
        if restored_players:
            lobby_players[guild_id] = restored_players
            print(f"[INIT] Restored {len(restored_players)} players in lobby for guild {guild_id}")
        # Restore lobby message
        lobby_msg_id = load_lobby_message_id(guild_id)
        if lobby_msg_id:
            for channel in guild.text_channels:
                try:
                    msg = await channel.fetch_message(int(lobby_msg_id))
                    if msg.author.id == bot.user.id:
                        lobby_message[guild_id] = msg
                        print(f"[INIT] Restored lobby message for guild {guild_id}")
                        break
                except:
                    continue
    # Start the periodic MMR refresh task **after** restores finish
    if not refresh_all_mmrs.is_running():
        refresh_all_mmrs.start()
        print("[INIT] Started refresh_all_mmrs task.")
    _ = get_http_session()  # ensure session exists
    print("[HTTP] Shared aiohttp session is ready")

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
            save_lobby_players(guild_id, lobby_players[guild_id])
    elif emoji == "👎":
        was_full = len(lobby_players[guild_id]) == 10
        for i, (uid, _, _) in enumerate(lobby_players[guild_id]):
            if uid == user.id:
                del lobby_players[guild_id][i]
                updated = True
                save_lobby_players(guild_id, lobby_players[guild_id])
                if len(lobby_players[guild_id]) == 9 and was_full:
                    await channel.send(f"{user.mention} left the full lobby. Lobby is now 9/10.")
                    # Remove 🚀 and ♻️
                    for reaction in message.reactions:
                        if str(reaction.emoji) in ["🚀", "♻️", "⚔️"]:
                            await message.clear_reaction(reaction.emoji)
                break
    elif emoji == "🚀" and len(lobby_players[guild_id]) == 10:
        mode = inhouse_mode.get(guild_id, "regular")
        if mode == "regular":
            team_rolls[guild_id], valid_combo_count = calculate_balanced_teams(lobby_players[guild_id], guild_id)
            if not team_rolls[guild_id]:
                await channel.send(
                    "Cannot form teams with the current MMR threshold (≤100). "
                    "Either set missing MMRs (`!cfg <steam_id>`) or let me try a relaxed threshold…"
                )
                # optional automatic fallback (see #2 below)
                team_rolls[guild_id], valid_combo_count = calculate_balanced_teams(
                    lobby_players[guild_id], guild_id, max_mmr_diff=400  # try 400 first
                )
            if not team_rolls[guild_id]:
                await channel.send("Still no valid combos. Please set MMRs or disable the strict threshold.")
                return
            valid_team_combos[guild_id] = valid_combo_count
            team1, team2, score1, score2, roles1, roles2 = team_rolls[guild_id][0]
            original_teams[guild_id] = (team1, team2, score1, score2, roles1, roles2)
            embed = build_team_embed(team1, team2, score1, score2, roles1, roles2, guild)
            roll_count[guild_id] = 1
        elif mode == "immortal":
            all_pairs = get_all_captain_pairs(lobby_players[guild_id])
            pol, thr = get_captain_policy(guild_id)
            # pick the starting pair index according to your policy
            # choices: "min_diff" (current), "top2_if_close", "simulate"
            preferred_index = choose_captain_pair_index(
                lobby_players[guild_id],
                all_pairs,
                policy=pol,
                threshold=(thr if isinstance(thr, int) else 200)
            )
            captain_draft_state[guild_id] = {
                "pairs": all_pairs,
                "index": preferred_index
            }
            captains, pool, _ = all_pairs[preferred_index]
            original_teams[guild_id] = (captains, pool)
            embed = build_immortal_embed(captains, pool, guild, preferred_index)
        await message.edit(embed=embed)
        await message.clear_reactions()
        await message.add_reaction("👍")
        await message.add_reaction("👎")
        await message.add_reaction("♻️")
        if mode == "immortal":
            await message.add_reaction("⚔️")
        await message.remove_reaction(payload.emoji, user)
        # Start live match polling if not already started
        match = None
        timeout = 15 * 60  # 15 minutes
        interval = 30  # polling interval
        elapsed = 0
        low_lobby_time = 0  # tracks how long lobby is underfilled
        await channel.send("Waiting for the in-game match to appear on Steam... (up to 15 minutes)")
        while elapsed < timeout:
            current_lobby = lobby_players.get(guild_id, [])
            if len(current_lobby) < 10:
                low_lobby_time += interval
                print(f"[INFO] Lobby underfilled ({len(current_lobby)}/10) for {low_lobby_time} seconds")
                if low_lobby_time >= 30:  # now 30 seconds
                    await channel.send("Lobby has not been full for 30 seconds. Match polling cancelled.")
                    return
            else:
                if low_lobby_time > 0:
                    print("[INFO] Lobby refilled to 10/10 — resetting grace timer.")
                low_lobby_time = 0
            match = await fetch_live_match_for_guild(guild.id)
            if match:
                break
            await asyncio.sleep(interval)
            elapsed += interval
        if match:
            match_id = match.get("match_id")
            if guild_id not in polling_tasks:
                active_match_ids[guild_id] = match_id
                polling_tasks[guild_id] = asyncio.create_task(poll_live_match(match_id, guild))
                await channel.send(f"[🚀] Started match polling for match ID {match_id} in guild {guild.name}")
        else:
            await channel.send("No live match was found within 15 minutes. Please restart the lobby.")
    elif emoji == "⚔️":
        # Only allow in immortal mode
        if inhouse_mode.get(guild_id, "regular") != "immortal":
            # silently ignore; final "always remove" will clear the reaction
            pass
        else:
            # Must have a captain pair chosen by the 🚀 flow
            state = captain_draft_state.get(guild_id)
            if not state or "pairs" not in state or "index" not in state:
                # no captain pair configured; do nothing (final remove below)
                pass
            else:
                # Resolve the two captain IDs allowed to start the draft
                try:
                    captains, pool, _ = state["pairs"][state["index"]]
                    c1_id, _, _ = captains[0]
                    c2_id, _, _ = captains[1]
                    allowed_ids = {int(c1_id), int(c2_id)}
                except Exception:
                    allowed_ids = set()
                if user_id not in allowed_ids:
                    # Non-captain pressed ⚔️ — do nothing (final remove below)
                    pass
                else:
                    # Guard against double-starts
                    if not immortal_draft_running.get(guild_id):
                        immortal_draft_running[guild_id] = True
                        # Optional: clear ⚔️ from the message so it can't be pressed again
                        try:
                            await message.clear_reaction("⚔️")
                        except Exception:
                            pass
                        # Start the Immortal Draft in this channel
                        try:
                            await start_immortal_draft(bot, guild, channel)
                        except Exception as e:
                            immortal_draft_running[guild_id] = False  # allow retry if failed
                            try:
                                await channel.send(f"Failed to start Immortal Draft: `{e}`")
                            except Exception:
                                pass
    elif emoji == "♻️" and len(lobby_players[guild_id]) == 10:
        mode = inhouse_mode.get(guild_id, "regular")
        # Get the member object from the guild
        member = guild.get_member(payload.user_id)
        # Check if they are admin or have special roles
        if not await user_is_admin_or_has_role(member):
            return
        if immortal_draft_running.get(guild_id):
            return
        # REGULAR INHOUSE REROLL
        if mode == "regular":
            max_rolls = MAX_ROLLS
            if roll_count[guild_id] >= max_rolls:
                roll_count[guild_id] = 1
            else:
                roll_count[guild_id] += 1
            if guild_id not in team_rolls or not team_rolls[guild_id]:
                await message.channel.send("No team combinations found. Please press 🚀 first.")
                return
            index = roll_count[guild_id] - 1
            if index >= len(team_rolls[guild_id]):
                index = 0
            team1, team2, score1, score2, roles1, roles2 = team_rolls[guild_id][index]
            original_teams[guild_id] = (team1, team2, score1, score2, roles1, roles2)
            embed = build_team_embed(team1, team2, score1, score2, roles1, roles2, guild)
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
def build_lobby_embed(guild, mode: Optional[str] = None):
    guild_id = guild.id
    if mode is None:
        mode = inhouse_mode.get(guild_id, "regular")
        if mode is None:
            mode = load_inhouse_mode_for_guild(guild.id)
            inhouse_mode[guild_id] = mode
    embed = discord.Embed(
        title="DotA2 Inhouse Lobby",
        #description=f"**Mode:** `{mode.capitalize()}`\n({len(lobby_players[guild.id])}/10)",
        color=discord.Color.purple()
    )
    embed.add_field(
        name=f"**Mode** `{mode.capitalize()}`",
        value=f"\n({len(lobby_players[guild.id])}/10)",
        inline=True
        )
    embed.add_field(name="\u200b", value="\u200b", inline=True)  # Empty field
    if mode != "immortal":
        roles_enabled = load_preferred_roles_setting(guild.id)
        embed.add_field(
            name="Preferred Roles",
            value="✅ Enabled" if roles_enabled else "❌ Disabled",
            inline=True
        )
    else:
        embed.add_field(name="\u200b", value="\u200b", inline=True)  # Empty field
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
def build_team_embed(team1, team2, score1, score2, roles1=None, roles2=None, guild=None, preference_map=None, mmr_map=None):
    global roll_count
    roles_enabled = load_preferred_roles_setting(guild.id)
    avg1 = sum(p[2] for p in team1) / 5
    avg2 = sum(p[2] for p in team2) / 5
    description = f"(10/10): T1: {int(avg1)}, T2: {int(avg2)}, Roll #{roll_count.get(guild.id, 1)}/{MAX_ROLLS}"
    if guild.id in valid_team_combos:
        description += f"\n🧮 Valid team combinations found: {valid_team_combos[guild.id]}"
    embed = discord.Embed(
        title="DotA2 Inhouse Lobby",
        description=description,
        color=discord.Color.gold()
    )
    team1_sorted = sorted(team1, key=lambda x: x[2], reverse=True)
    team2_sorted = sorted(team2, key=lambda x: x[2], reverse=True)
    if roles_enabled:
        # Only assign roles if not already passed in
        if roles1 is None:
            print("[BUILD_TEAM_EMBED] Assigning roles for team 1")
            roles1 = assign_roles_with_preferences(team1_sorted, preference_map, mmr_map)
        else:
            print("[BUILD_TEAM_EMBED] Using passed roles for team 1")
        if roles2 is None:
            print("[BUILD_TEAM_EMBED] Assigning roles for team 2")
            roles2 = assign_roles_with_preferences(team2_sorted, preference_map, mmr_map)
        else:
            print("[BUILD_TEAM_EMBED] Using passed roles for team 2")
        def format_player_list(assignments):
            return ", ".join(
                f"{p[1]} ({p[2]}) [Pos {role}]"
                for role, p in sorted(assignments.items())
            )
        team1_desc = format_player_list(roles1) + f"\n🍀 Role Fit Score: {score1}"
        team2_desc = format_player_list(roles2) + f"\n🍀 Role Fit Score: {score2}"
    else:
        def format_player_list(team):
            return ", ".join(
                f"{p[1]} ({p[2]})"
                for p in team
            )
        team1_desc = format_player_list(team1_sorted)
        team2_desc = format_player_list(team2_sorted)
    password = load_lobby_password_for_guild(guild.id)
    embed.add_field(name="Team One", value=team1_desc, inline=False)
    embed.add_field(name="Team Two", value=team2_desc, inline=False)
    embed.add_field(name="**Password**", value=password, inline=False)
    return embed

# Builds the embed message for an Immortal Mode draft lobby, showing captains, pool, and reroll info
def build_immortal_embed(captains, pool, guild, reroll_count):
    c1, c2 = captains
    embed = discord.Embed(
        title="🛡️ Immortal Draft Inhouse Lobby",
        description=f"Captains: {c1[1]} ({c1[2]}) vs {c2[1]} ({c2[2]})\nRoll #{reroll_count}/{IMMORTAL_MAX_ROLLS}",
        color=discord.Color.orange()
    )
    embed.add_field(name="Captain 1", value=f"{c1[1]} ({c1[2]})", inline=True)
    embed.add_field(name="Captain 2", value=f"{c2[1]} ({c2[2]})", inline=True)
    embed.add_field(
        name="🧩 Draft Pool",
        value=", ".join(f"{p[1]} ({p[2]})" for p in sorted(pool, key=lambda x: x[2])),
        inline=False
    )
    password = load_lobby_password_for_guild(guild.id)
    embed.add_field(name="**Password**", value=password, inline=False)
    return embed

# Formats a live match embed that works even when the Steam scoreboard hasn't populated yet (draft phase)
async def format_live_match_embed(match, guild):
    sb = match.get("scoreboard")  # may be None in draft
    # Timer (mm:ss) and scores are only reliable when scoreboard exists
    if sb:
        try:
            dur_raw = sb.get("duration", 0) or 0
            dur = int(dur_raw)
        except (TypeError, ValueError):
            dur = 0
        minutes = dur // 60
        seconds = dur % 60
        timer = f"{minutes}:{seconds:02d}"
        radiant_kills = sum(p.get("kills", 0) for p in sb.get("radiant", {}).get("players", []))
        dire_kills    = sum(p.get("kills", 0) for p in sb.get("dire",    {}).get("players", []))
    else:
        timer = "—"
        radiant_kills = 0
        dire_kills = 0
    league_id = match.get("league_id", "N/A")
    match_id = match.get("match_id", "N/A")
    # Determine embed color
    if radiant_kills > dire_kills:
        color = discord.Color.green()
    elif dire_kills > radiant_kills:
        color = discord.Color.red()
    else:
        color = discord.Color.blurple()
    # Title/description adapt to draft phase
    desc = (
        f"⏱️ **{timer}** — **Radiant {radiant_kills} : {dire_kills} Dire**"
        if sb else
        "Draft phase (scoreboard not available yet)"
    )
    embed = discord.Embed(
        title="🏆 Live League Match",
        description=desc,
        color=color
    )
    # Build player lists from `match['players']` which is present during draft
    radiant_players, dire_players = [], []
    for p in match.get("players", []) or []:
        team = p.get("team", 0)  # 0=radiant, 1=dire
        hero_id = p.get("hero_id", 0) or 0
        hero_name = hero_id_map.get(str(hero_id), "—" if hero_id == 0 else f"Hero {hero_id}")
        steam32 = p.get("account_id")
        if steam32:
            name = await get_display_name(steam32, guild)
        else:
            name = "Unknown"
        entry = f"{name} ({hero_name})"
        if team == 0 and len(radiant_players) < 5:
            radiant_players.append(entry)
        elif team == 1 and len(dire_players) < 5:
            dire_players.append(entry)
    # Only warn about incomplete teams once the scoreboard exists (i.e., post-draft)
    if sb and (len(radiant_players) != 5 or len(dire_players) != 5):
        print(f"[WARN] Expected 5 players per team. Got Radiant={len(radiant_players)}, Dire={len(dire_players)}")
    # Pad with placeholders so columns remain stable during draft
    while len(radiant_players) < 5: radiant_players.append("—")
    while len(dire_players) < 5:    dire_players.append("—")
    embed.add_field(name="**Radiant**", value="\n".join(radiant_players), inline=True)
    embed.add_field(name="**Dire**",    value="\n".join(dire_players),    inline=True)
    # Info block is always useful
    embed.add_field(
        name="Info",
        value=f"League ID: `{league_id}`\nMatch ID: `{match_id}`",
        inline=False
    )
    return embed

deps = {
    # checks
    "user_is_admin_or_has_role": user_is_admin_or_has_role,
    "is_admin_or_has_role": is_admin_or_has_role,
    "is_global_admin": is_global_admin,
    # firestore
    "db": db,
    "firestore": firestore,
    # player/MMR
    "convert_to_steam32": convert_to_steam32,
    "fetch_mmr": fetch_mmr,
    "save_player_config": save_player_config,
    "get_mmr": get_mmr,
    "get_inhouse_mmr": get_inhouse_mmr,
    "get_top_players": get_top_players,
    # coins/betting
    "get_balance": get_balance,
    "place_bet": place_bet,
    "update_balance": update_balance,
    "resolve_bets": resolve_bets,
    "clear_guild_bets": clear_guild_bets,
    # match/live
    "fetch_live_match_for_guild": fetch_live_match_for_guild,
    "poll_live_match": poll_live_match,
    "format_live_match_embed": format_live_match_embed,
    "map_steam_ids_to_discord_ids": map_steam_ids_to_discord_ids,
    "fetch_match_result": fetch_match_result,
    # state dicts
    "active_match_ids": active_match_ids,
    "polling_tasks": polling_tasks,
    "random_polling_flags": random_polling_flags,
    "match_tracking_start_times": match_tracking_start_times,
    "live_embed_messages": live_embed_messages,
    # lobby + helpers
    "lobby_players": lobby_players,
    "lobby_message": lobby_message,
    "inhouse_mode": inhouse_mode,
    "captain_draft_state": captain_draft_state,
    "update_lobby_embed": update_lobby_embed,
    "build_lobby_embed": build_lobby_embed,
    "build_immortal_embed": build_immortal_embed,
    "save_lobby_players": save_lobby_players,
    "save_lobby_message_id": save_lobby_message_id,
    "save_lobby_password_for_guild": save_lobby_password_for_guild,
    "load_inhouse_mode_for_guild": load_inhouse_mode_for_guild,
    "save_inhouse_mode_for_guild": save_inhouse_mode_for_guild,
    "refresh_lobby_member_mmr": refresh_lobby_member_mmr,
    "start_immortal_draft": start_immortal_draft,
    "get_captain_policy": get_captain_policy,
    "set_captain_policy": set_captain_policy,
    "choose_captain_pair_index": choose_captain_pair_index,
    "get_all_captain_pairs": get_all_captain_pairs,
    # guild settings
    "save_guild_prefix": save_guild_prefix,
    "load_guild_prefix": load_guild_prefix,
    "save_league_guild_mapping": save_league_guild_mapping,
    "live_channel_ids": live_channel_ids,
    "prefix_cache": prefix_cache,
    # misc
    "get_discord_id_from_steam_id": get_discord_id_from_steam_id,
    "adjust_mmr": adjust_mmr,
    "save_preferred_roles_setting": save_preferred_roles_setting,
}
attach_commands(bot, deps)

if __name__ == "__main__":
    try:
        bot.run(TOKEN)  # blocks until SIGTERM / shutdown
    finally:
        # event loop used by bot.run() is gone; create a tiny loop to close cleanly
        asyncio.run(close_http_session())