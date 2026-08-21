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
import aiohttp, asyncio, os, json
from datetime import datetime, timezone
from typing import Optional
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from firebase_admin import firestore
import bot.storage.firebase_setup  # ensures Firebase is initialized before anything else
from bot.commands.commands import attach_commands
from bot.services.guild_config_service import (
    configure_guild_config,
    delete_separated_pair,
    get_captain_policy,
    get_separated_pairs,
    load_guild_prefix,
    load_inhouse_mode_for_guild,
    load_lobby_message_id,
    load_lobby_password_for_guild,
    load_lobby_players,
    load_player_config,
    load_preferred_roles_setting,
    save_guild_prefix,
    save_inhouse_mode_for_guild,
    save_league_guild_mapping,
    save_lobby_message_id,
    save_lobby_password_for_guild,
    save_lobby_players,
    save_lobby_roster_lock,
    save_player_config,
    save_preferred_roles_setting,
    save_separated_pair,
    set_captain_policy,
)
from bot.services.live_tracking_service import (
    clear_match_tracking_state,
    configure_live_tracking,
    convert_to_steam32,
    fetch_live_match_for_guild,
    fetch_mmr,
    poll_live_match,
    wait_for_match_then_start_polling,
)
from bot.services.store_service import (
    apply_pending_match_mute,
    cleanup_active_match_mutes,
    cleanup_expired_store_roles,
    configure_store_service,
    ensure_vip_feeder_role,
    get_store_cost,
    get_store_item_info,
    log_store_purchase,
    normalize_store_item_name,
    purchase_match_mute,
    purchase_store_role,
    reset_custom_store_roles,
    reset_vip_feeder_role,
    save_store_cost_override,
    unmute_match_store_mutes,
)
from bot.services.lobby_service import (
    assign_roles_with_preferences,
    calculate_balanced_teams,
    calculate_role_fit_score,
    cancel_match_wait,
    choose_captain_pair_index,
    clear_manual_if_lobby_changed,
    configure_lobby_service,
    find_lobby_tuple,
    format_lobby_player_mention,
    get_all_captain_pairs,
    get_lobby_channel_for_guild,
    get_preferred_roles,
    is_placeholder_player,
    refresh_lobby_member_mmr,
    reset_team_state_for_guild,
    full_post_rocket_reset,
    start_immortal_draft,
)
from bot.services.embed_service import (
    build_immortal_embed,
    build_lobby_embed,
    build_team_embed,
    configure_embed_service,
    format_live_match_embed,
    update_all_lobbies,
    update_lobby_embed,
)
from bot.services.mmr_manager import adjust_mmr, get_inhouse_mmr, get_top_players
from bot.services.betting_manager import (
    BETTING_MODE_CLASSIC,
    BETTING_MODE_POOL,
    MIN_BET_AMOUNT,
    MARKET_FIRST_BLOOD,
    MARKET_FIRST_TOWER,
    MARKET_FIRST_TO_10,
    MARKET_MATCH_WINNER,
    MARKET_ORDER,
    MARKET_DURATION_35,
    MARKET_TOTAL_KILLS_50,
    get_betting_settings,
    get_betting_summary,
    get_existing_market_bet,
    get_public_market_snapshots,
    is_market_open_for_betting,
    normalize_market_id,
    place_market_bet,
    process_live_betting_markets,
    save_betting_mode_for_guild,
    save_prop_markets_setting,
    void_market,
    void_markets,
    void_user_bet,
    void_user_bets,
    clear_guild_bets,
    get_balance,
    place_bet,
    resolve_bets,
    clear_all_bets,
    update_balance,
    DD_TOKEN_COST,
    get_dd_token_balance,
    update_dd_token_balance,
    has_active_double_down,
    activate_double_down,
    get_active_double_down_users,
    clear_active_double_downs,
)
from bot.services.match_tracker import fetch_match_result
from bot.services.immortal_draft import set_cancel_callback
from bot.services.processed_match_log import (
    get_bound_league_id,
    get_processed_match,
    is_match_processed,
    log_processed_match,
)
from bot.services.match_ledger_service import (
    get_all_match_ledgers,
    get_player_inhouse_record,
    get_recent_match_ledgers,
    log_match_ledger,
)
from bot.services.match_imp_service import (
    configure_match_imp_service,
    get_top_avg_imp_players,
    recalculate_avg_imp_for_all_guilds,
    schedule_due_imp_enrichments,
    schedule_match_imp_enrichment,
)
from bot.services.stratz_guard import (
    format_stratz_block,
    get_stratz_block_state,
    mark_mmr_refresh_completed,
    mark_mmr_refresh_started,
    should_skip_recent_mmr_refresh,
)
from bot.services.rsvp_service import RsvpManager
from bot.ui.manual_captain_select import (
    ManualCaptainSelectView,
    configure_manual_captain_select,
    handle_immortal_draft_cancel,
)
from bot.state.runtime_state import (
    ALLOWED_CAPTAIN_POLICIES,
    GLOBAL_ADMIN_ID,
    IMMORTAL_MAX_ROLLS,
    MAX_ROLLS,
    active_match_ids,
    bets_embed_messages,
    bets_refresh_tasks,
    captain_draft_state,
    display_name,
    immortal_draft_running,
    inhouse_mode,
    live_channel_ids,
    live_embed_messages,
    lobby_channel_ids,
    lobby_message,
    lobby_players,
    lobby_roster_locks,
    match_tracking_start_times,
    match_wait_tasks,
    original_teams,
    polling_tasks,
    prefix_cache,
    random_polling_flags,
    rocket_lock,
    roll_count,
    team_rolls,
    valid_team_combos,
)

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
STRATZ_TOKEN = os.getenv("STRATZ_TOKEN")
BOT_STARTED_AT = datetime.now(timezone.utc)
MMR_REFRESH_BOOT_GRACE_SECONDS = int(os.getenv("MMR_REFRESH_BOOT_GRACE_SECONDS", "3600"))
STEAM_API_KEY = os.getenv("STEAM_API_KEY")
# Replace this with your actual Discord user ID
GLOBAL_ADMIN_ID = 187959278949105664

db = firestore.client()
configure_guild_config(db_client=db, firestore_module=firestore)
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.guilds = True
intents.members = True
HERO_CACHE_FILE = os.path.join(os.path.dirname(__file__), "data", "hero_id_map.json")
hero_id_map = {}
if os.path.exists(HERO_CACHE_FILE):
    try:
        with open(HERO_CACHE_FILE, "r") as f:
            hero_id_map = json.load(f)
    except Exception:
        hero_id_map = {}

# ============================== Bot Configuration ==============================

# Resolves the correct command prefix for the bot, based on the message's guild.
async def resolve_command_prefix(bot, message):
    if message.guild:
        return load_guild_prefix(message.guild.id)
    return "!"  # fallback default for DMs

bot = commands.Bot(command_prefix=resolve_command_prefix, intents=intents, help_command=None)
rsvp_manager = RsvpManager(
    bot=bot,
    db=db,
    load_player_config=load_player_config,
    load_guild_prefix=load_guild_prefix,
    load_inhouse_record=get_player_inhouse_record,
)

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
# ============================================ Core Functions & Utilities ============================================
# ========================================================================================================================

# Polls a live Dota 2 match, updates Discord with status, and resolves bets/MMR after the match ends
# =============================== Permission Checks ===============================

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

# Loads hero ID-to-name mapping from local cache or Steam API if cache is missing or invalid
async def fetch_hero_id_to_name_map():
    # Try loading from local cache first
    if os.path.exists(HERO_CACHE_FILE):
        try:
            with open(HERO_CACHE_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
                    print("[fetch_hero_id_to_name_map] Loaded hero ID cache from local file.")
                    return data
                else:
                    print("[fetch_hero_id_to_name_map] Invalid hero cache format. Refetching from API...")
        except Exception as e:
            print(f"[fetch_hero_id_to_name_map] Failed to load local hero cache: {e}")
    print("[fetch_hero_id_to_name_map] 📡 Fetching hero data from Steam API...")
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
            print("[fetch_hero_id_to_name_map] Saved hero ID map to cache.")
            return hero_map
    except Exception as e:
        print(f"[fetch_hero_id_to_name_map] Failed to fetch hero data from Steam API: {e}")
        return {}

# Gets the stored MMR value for a given Discord user, or returns 0 if not found.
def get_mmr(user):
    user_id = str(user.id)
    info = load_player_config(user_id)
    if info and isinstance(info, dict):
        return info.get("mmr", 0)
    return 0

# ============================ Player & Lobby Utilities ============================

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
        print(f"[get_discord_id_from_steam_id] Invalid Steam ID input: {steam_id}")
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
                print(f"[map_steam_ids_to_discord_ids] No Discord user found for Steam ID {steam_id}")
        print(f"[map_steam_ids_to_discord_ids] Mapped {len(discord_ids)}/{len(steam_ids)} Steam IDs to Discord IDs")
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
        print(f"[get_steam_display_name] Error: {e}")
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
    uptime_seconds = (datetime.now(timezone.utc) - BOT_STARTED_AT).total_seconds()
    if uptime_seconds < MMR_REFRESH_BOOT_GRACE_SECONDS:
        remaining = int(MMR_REFRESH_BOOT_GRACE_SECONDS - uptime_seconds)
        print(f"[refresh_all_mmrs] Skipping during boot grace period ({remaining}s remaining).")
        return

    skip_recent, last_refresh_at = should_skip_recent_mmr_refresh()
    if skip_recent:
        print(f"[refresh_all_mmrs] Skipping; last MMR refresh started at {last_refresh_at.isoformat()} within the 4h window.")
        return

    blocked, block_reason, blocked_until = get_stratz_block_state()
    if blocked:
        print(f"[refresh_all_mmrs] Skipping; {format_stratz_block(block_reason, blocked_until)}.")
        return

    mark_mmr_refresh_started()
    print("Refreshing MMRs (Firebase)...")
    updates_log = []  # collected and printed once at the end
    refreshed_players = 0
    stopped_reason = None
    players_ref = db.collection("players").stream()
    for doc in players_ref:
        blocked, block_reason, blocked_until = get_stratz_block_state()
        if blocked:
            stopped_reason = format_stratz_block(block_reason, blocked_until)
            print(f"[refresh_all_mmrs] Stopping early; {stopped_reason}.")
            break
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
            refreshed_players += 1
            # throttle between users
            await asyncio.sleep(0.3) # ensure stratz api call limit is not exceeded
        except Exception as e:
            print(f"[refresh_all_mmrs] Failed to fetch MMR for {steam_id} ({user_id}): {e}")
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
                print(f"[refresh_all_mmrs] Failed to update Firestore for {user_id}: {e}")
    mark_mmr_refresh_completed(
        refreshed_players=refreshed_players,
        updated_players=len(updates_log),
        stopped_reason=stopped_reason,
    )
    # Refresh lobby embeds across all servers
    try:
        await update_all_lobbies()
    except Exception as e:
        print(f"[refresh_all_mmrs] Failed to update lobby embeds: {e}")
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


@refresh_all_mmrs.before_loop
async def before_refresh_all_mmrs():
    if MMR_REFRESH_BOOT_GRACE_SECONDS > 0:
        print(f"[refresh_all_mmrs] Waiting {MMR_REFRESH_BOOT_GRACE_SECONDS}s boot grace before first refresh.")
        await asyncio.sleep(MMR_REFRESH_BOOT_GRACE_SECONDS)


@tasks.loop(hours=1)
async def cleanup_expired_store_roles_task():
    await cleanup_expired_store_roles()


@tasks.loop(hours=1)
async def retry_pending_match_imp_task():
    await schedule_due_imp_enrichments()

# ========================================================================================================================
# ================================================ Bot Event Handlers ================================================
# ========================================================================================================================

# Runs once when the bot starts and begins the MMR refresh task.
@bot.event
async def on_ready():
    global hero_id_map
    print(f"{bot.user} is online!")
    active_match_ids.clear()
    clear_all_bets(bot)
    # Cache hero IDs
    hero_id_map = await fetch_hero_id_to_name_map()
    # Load live_channel_ids from Firestore
    docs = db.collection("guild_specific_info").stream()
    for doc in docs:
        data = doc.to_dict()
        guild_id = int(doc.id)
        guild = bot.get_guild(guild_id)
        if not guild:
            print(f"[on_ready] Could not find guild object for guild ID {guild_id}")
            continue
        # Load live channel ID
        live_channel_id = data.get("live_channel_id", {}).get("live_channel_id", 0)
        try: 
            live_channel_ids[int(doc.id)] = int(live_channel_id)
            """print(f"[on_ready] Guild {doc.id} (type: {type(doc.id).__name__}) → Channel {live_channel_id} (type: {type(live_channel_id).__name__})")"""
        except (ValueError, TypeError):
            print(f"[on_ready] Skipping guild {doc.id} due to invalid live_channel_id: {live_channel_id}")
        # Load lobby channel ID
        lobby_channel_id = data.get("lobby_channel_id", {}).get("lobby_channel_id", 0)
        try:
            if lobby_channel_id:
                lobby_channel_ids[int(doc.id)] = int(lobby_channel_id)
        except (ValueError, TypeError):
            print(f"[on_ready] Skipping guild {doc.id} due to invalid lobby_channel_id: {lobby_channel_id}")
        roster_lock = data.get("lobby_roster_lock", {}) or {}
        try:
            if roster_lock.get("locked") and roster_lock.get("message_id"):
                lobby_roster_locks[guild_id] = int(roster_lock["message_id"])
            else:
                lobby_roster_locks.pop(guild_id, None)
        except (ValueError, TypeError):
            lobby_roster_locks.pop(guild_id, None)
            print(f"[on_ready] Skipping invalid lobby roster lock for guild {doc.id}")
        # Restore lobby players
        restored_players = load_lobby_players(guild_id)
        if restored_players:
            lobby_players[guild_id] = restored_players
            print(f"[on_ready] Restored {len(restored_players)} players in lobby for guild {guild_id}")
        # Restore lobby message
        lobby_msg_id = load_lobby_message_id(guild_id)
        if lobby_msg_id:
            for channel in guild.text_channels:
                try:
                    msg = await channel.fetch_message(int(lobby_msg_id))
                    if msg.author.id == bot.user.id:
                        lobby_message[guild_id] = msg
                        print(f"[on_ready] Restored lobby message for guild {guild_id}")
                        break
                except:
                    continue
    await rsvp_manager.restore_active_events()
    for guild in bot.guilds:
        await ensure_vip_feeder_role(guild)
    await cleanup_expired_store_roles()
    await cleanup_active_match_mutes()
    # Start the periodic MMR refresh task **after** restores finish
    if not refresh_all_mmrs.is_running():
        refresh_all_mmrs.start()
        print("[on_ready] Started refresh_all_mmrs task.")
    if not cleanup_expired_store_roles_task.is_running():
        cleanup_expired_store_roles_task.start()
        print("[on_ready] Started cleanup_expired_store_roles_task.")
    if not retry_pending_match_imp_task.is_running():
        retry_pending_match_imp_task.start()
        print("[on_ready] Started retry_pending_match_imp_task.")
    _ = get_http_session()  # ensure session exists
    print("[on_ready] Shared aiohttp session is ready")
    await recalculate_avg_imp_for_all_guilds()
    await schedule_due_imp_enrichments()


@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot or after.channel is None:
        return
    guild_id = member.guild.id
    if random_polling_flags.get(guild_id, False):
        return
    match_id = active_match_ids.get(guild_id)
    polling_task = polling_tasks.get(guild_id)
    if not match_id or polling_task is None or polling_task.done():
        return
    await apply_pending_match_mute(member, match_id)

# Listens for any messages containing "dota" and replies with a generic response.
"""@bot.event
async def on_message(msg):
    if msg.author.bot:
        return
    if "dota" in msg.content.lower():
        await msg.channel.send(f"Interesting message, {msg.author.mention}")
    await bot.process_commands(msg)"""


async def roll_lobby_to_post_rocket_state(guild_id, guild, channel, message, *, roster_locked=False):
    mode = inhouse_mode.get(guild_id, "regular")
    if mode == "regular":
        team_rolls[guild_id], valid_combo_count = calculate_balanced_teams(
            lobby_players[guild_id],
            guild_id,
        )
        if not team_rolls[guild_id]:
            await channel.send(
                "Cannot form teams with the current MMR/separation constraints. "
                "Either set missing MMRs (`!cfg <steam_id>`) or let me try a relaxed threshold..."
            )
            team_rolls[guild_id], valid_combo_count = calculate_balanced_teams(
                lobby_players[guild_id],
                guild_id,
                max_mmr_diff=400,
            )
        if not team_rolls[guild_id]:
            await channel.send(
                "Still no valid combos. Please set MMRs, adjust separated pairs, or disable the strict threshold."
            )
            return False
        valid_team_combos[guild_id] = valid_combo_count
        team1, team2, score1, score2, roles1, roles2 = team_rolls[guild_id][0]
        original_teams[guild_id] = (team1, team2, score1, score2, roles1, roles2)
        roll_count[guild_id] = 1
        embed = build_team_embed(team1, team2, score1, score2, roles1, roles2, guild)
    elif mode == "immortal":
        all_pairs = get_all_captain_pairs(lobby_players[guild_id])
        policy, threshold = get_captain_policy(guild_id)
        preferred_index = choose_captain_pair_index(
            lobby_players[guild_id],
            all_pairs,
            policy=policy,
            threshold=(threshold if isinstance(threshold, int) else 200),
        )
        captain_draft_state[guild_id] = {
            "pairs": all_pairs,
            "index": preferred_index,
        }
        captains, pool, _ = all_pairs[preferred_index]
        original_teams[guild_id] = (captains, pool)
        embed = build_immortal_embed(captains, pool, guild, 1)
    else:
        await channel.send(f"Unknown inhouse mode `{mode}`; use `!lobby regular` or `!lobby immortal`.")
        return False

    await message.edit(embed=embed)
    cancel_match_wait(guild_id)
    match_wait_tasks[guild_id] = asyncio.create_task(
        wait_for_match_then_start_polling(guild_id, guild, channel)
    )
    try:
        await message.clear_reactions()
    except Exception as exc:
        print(f"[roll_lobby] Failed to clear reactions in guild {guild_id}: {exc}")

    reaction_emojis = ["♻️"]
    if not roster_locked:
        reaction_emojis = ["👍", "👎", *reaction_emojis]
    if mode == "immortal":
        reaction_emojis.extend(["⚔️", "🎯"])
    for reaction_emoji in reaction_emojis:
        try:
            await message.add_reaction(reaction_emoji)
        except Exception as exc:
            print(f"[roll_lobby] Failed to add reaction {reaction_emoji} in guild {guild_id}: {exc}")
    return True


async def open_confirmed_rsvp_lobby(event):
    guild_id = int(event.get("guild_id", 0) or 0)
    guild = bot.get_guild(guild_id)
    if guild is None:
        raise RuntimeError("FeederBot can no longer access this event's server.")
    polling_task = polling_tasks.get(guild_id)
    if guild_id in active_match_ids or (polling_task is not None and not polling_task.done()):
        raise RuntimeError("Another inhouse match is still active, so its lobby was not overwritten.")
    if immortal_draft_running.get(guild_id):
        raise RuntimeError("An Immortal Draft is still active, so its lobby was not overwritten.")

    signup_rows = [
        (str(user_id), data)
        for user_id, data in (event.get("signups", {}) or {}).items()
        if isinstance(data, dict) and data.get("status") == "rsvp"
    ]
    signup_rows.sort(key=lambda row: (int(row[1].get("joined_at", 0) or 0), row[0]))
    signup_rows = signup_rows[:10]
    if len(signup_rows) != 10:
        raise RuntimeError(f"The confirmed roster is only {len(signup_rows)}/10.")

    roster = []
    unavailable = []
    for user_id, signup in signup_rows:
        member = guild.get_member(int(user_id))
        if member is None:
            try:
                member = await guild.fetch_member(int(user_id))
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                member = None
        config = load_player_config(user_id) or {}
        mmr = config.get("mmr")
        if member is None or not isinstance(mmr, (int, float)) or isinstance(mmr, bool):
            unavailable.append(str(signup.get("display_name") or user_id))
            continue
        roster.append((member.id, member.display_name, int(mmr)))
    if unavailable:
        raise RuntimeError(
            "These confirmed players are no longer available or have no usable MMR: "
            + ", ".join(unavailable)
        )

    target_channel = get_lobby_channel_for_guild(guild)
    if target_channel is None:
        rsvp_channel_id = int(event.get("channel_id", 0) or 0)
        target_channel = bot.get_channel(rsvp_channel_id)
    if target_channel is None:
        raise RuntimeError("FeederBot cannot access the configured lobby or RSVP channel.")

    await full_post_rocket_reset(guild_id, lobby_message.get(guild_id))
    old_message = lobby_message.get(guild_id)
    if old_message:
        try:
            await old_message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass

    selected_mode = load_inhouse_mode_for_guild(guild_id)
    inhouse_mode[guild_id] = selected_mode
    lobby_players[guild_id] = roster
    base_embed = build_lobby_embed(guild, selected_mode)
    message = await target_channel.send(embed=base_embed)
    lobby_message[guild_id] = message
    save_lobby_message_id(guild_id, message.id)
    save_lobby_players(guild_id, roster)
    lobby_roster_locks[guild_id] = message.id
    save_lobby_roster_lock(
        guild_id,
        message.id,
        source="rsvp",
        event_start_at=event.get("start_at"),
    )

    try:
        auto_rolled = await roll_lobby_to_post_rocket_state(
            guild_id,
            guild,
            target_channel,
            message,
            roster_locked=True,
        )
    except Exception as exc:
        print(f"[rsvp] Post-rocket generation failed in guild {guild_id}: {exc}")
        auto_rolled = False
    if not auto_rolled:
        try:
            await message.clear_reactions()
            await message.add_reaction("🚀")
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass
    return {
        "message_id": message.id,
        "jump_url": message.jump_url,
        "mode": selected_mode,
        "auto_rolled": auto_rolled,
    }

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
    roll_count.setdefault(guild_id, 1)
    team_rolls.setdefault(guild_id, [])
    original_teams.setdefault(guild_id, None)
    roster_locked = lobby_roster_locks.get(guild_id) == message.id
    if roster_locked and emoji in {"👍", "👎"}:
        await channel.send(
            f"{user.mention}, this roster came from a confirmed scheduled RSVP and is locked. "
            "Contact an Inhouse Admin if an emergency replacement is needed.",
            delete_after=8,
        )
        try:
            await message.remove_reaction(payload.emoji, user)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass
        return
    if emoji == "👍":
        if len(lobby_players[guild_id]) >= 10:
            await channel.send(
                f"{user.mention}, the lobby is already full (10/10). Please wait for someone to leave.",
                delete_after=8,
            )
            await message.remove_reaction(payload.emoji, user)
            return
        if not any(uid == user.id for uid, _, _ in lobby_players[guild_id]):
            mmr = get_mmr(user)
            display_name = user.display_name
            lobby_players[guild_id].append((user.id, display_name, mmr))
            updated = True
            save_lobby_players(guild_id, lobby_players[guild_id])
            # Lobby changed after teams/rocket may have been started — allow re-🚀
            await full_post_rocket_reset(guild_id, message)
            if clear_manual_if_lobby_changed(guild_id):
                await channel.send("⚠️ Lobby changed—manual captain selection cleared.")
    elif emoji == "👎":
        was_full = len(lobby_players[guild_id]) == 10
        for i, (uid, _, _) in enumerate(lobby_players[guild_id]):
            if uid == user.id:
                del lobby_players[guild_id][i]
                updated = True
                save_lobby_players(guild_id, lobby_players[guild_id])
                # Lobby changed after teams/rocket may have been started — allow re-🚀
                await full_post_rocket_reset(guild_id, message)
                if clear_manual_if_lobby_changed(guild_id):
                    await channel.send("⚠️ Lobby changed—manual captain selection cleared.")
                if len(lobby_players[guild_id]) == 9 and was_full:
                    await channel.send(f"{user.mention} left the full lobby. Lobby is now 9/10.")
                    # Remove all post-rocket reactions so the lobby must be re-rocket'd
                    for reaction in message.reactions:
                        if str(reaction.emoji) in ["🚀", "♻️", "⚔️", "🎯"]:
                            await message.clear_reaction(reaction.emoji)
                break
    elif emoji == "🚀" and len(lobby_players[guild_id]) == 10:
        # --- 🚀 double-click guard -----------------------------------------
        # If we're already processing a rocket for this guild, ignore extras
        if rocket_lock.get(guild_id, False):
            print(f"[on_raw_reaction_add] Ignoring extra 🚀 press in guild {guild_id} (already processing).")
            await message.remove_reaction(payload.emoji, user)
            return
        # Also ignore if a match is already being tracked/polled
        if guild_id in active_match_ids or guild_id in polling_tasks:
            print(f"[on_raw_reaction_add] Ignoring 🚀 press in guild {guild_id} (match already active).")
            await message.remove_reaction(payload.emoji, user)
            return
        if not await user_is_admin_or_has_role(user):
            return
        # Lock this guild's rocket press
        rocket_lock[guild_id] = True
        try:
            await roll_lobby_to_post_rocket_state(
                guild_id,
                guild,
                channel,
                message,
                roster_locked=roster_locked,
            )
        finally:
            rocket_lock[guild_id] = False
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
    elif emoji == "🎯" and len(lobby_players[guild_id]) == 10:
        # Only in Immortal mode
        if inhouse_mode.get(guild_id, "regular") != "immortal":
            await channel.send("Manual captain selection is only for **Immortal** mode.")
            return
        # Permission gate
        if not await user_is_admin_or_has_role(user):
            await channel.send(f"{user.mention} you need **Inhouse Admin** or server admin to select captains.")
            return
        # Post the selection view (admin chooses both captains)
        view = ManualCaptainSelectView(guild, user, lobby_players[guild_id])
        await channel.send(
            "Select **two captains** for Immortal Draft (you have 2 minutes).",
            view=view
        )
    elif emoji == "♻️" and len(lobby_players[guild_id]) == 10:
        mode = inhouse_mode.get(guild_id, "regular")
        if not await user_is_admin_or_has_role(user):
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
            if draft_state and draft_state.get("manual"):
                # Keep same captains/pool
                captains, pool, _ = draft_state["pairs"][draft_state["index"]]
                original_teams[guild_id] = (captains, pool)
            else:
                draft_state["index"] = (draft_state["index"] + 1) % max_rolls
                captains, pool, _ = draft_state["pairs"][draft_state["index"]]
                original_teams[guild_id] = (captains, pool)
            embed = build_immortal_embed(captains, pool, guild, draft_state["index"] + 1)
        await message.edit(embed=embed)
    if updated:
        await update_lobby_embed(guild)
    # Always remove the user's reaction
    try:
        await message.remove_reaction(payload.emoji, user)
    except Exception as e:
        print(f"[on_raw_reaction_add] Final remove_reaction failed in guild {guild_id}: {e}")

# Sends a welcome message with instructions when the bot joins a new server.
@bot.event
async def on_guild_join(guild):
    await ensure_vip_feeder_role(guild)
    welcome_embed = discord.Embed(
        title="👋 Welcome to FeederBot!",
        description=(
            "Thanks for inviting me to your server.\n"
            "An admin should run these setup commands first."
        ),
        color=discord.Color.green()
    )
    welcome_embed.add_field(
        name="Server setup",
        value=(
            "`!setlobbychannel` - set where lobby embeds are posted\n"
            "`!setlivechannel` - set where live match updates are posted\n"
            "`!bindleague <league_id>` - bind your Dota 2 league id for live match tracking\n"
            "`!lobby <regular|immortal>` - create a lobby and choose the inhouse mode"
        ),
        inline=False
    )
    welcome_embed.add_field(
        name="Player setup",
        value=(
            "`!cfg <steam_id>` - link your Steam ID and fetch MMR\n"
            "`!setpreferredroles <1 2 3 4 5>` - set role preferences for balancing"
        ),
        inline=False
    )
    welcome_embed.add_field(
        name="More commands",
        value=(
            "`!help` - player commands\n"
            "`!help admin` - admin setup and match tracking commands"
        ),
        inline=False
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
        print(f"[on_guild_join] Could not DM the owner of {guild.name}.")

# ========================================================================================================================
# ============================================== Embed Builders Section ==============================================
# ========================================================================================================================


configure_store_service(db_client=db, firestore_module=firestore, bot_instance=bot)
configure_embed_service(
    bot_instance=bot,
    hero_id_map_cache=hero_id_map,
    get_display_name_fn=get_display_name,
    assign_roles_with_preferences_fn=assign_roles_with_preferences,
)
configure_lobby_service(db_client=db, update_lobby_embed_fn=update_lobby_embed)
rsvp_manager.configure_lobby_handoff(open_confirmed_rsvp_lobby)
configure_manual_captain_select(
    find_lobby_tuple_fn=find_lobby_tuple,
    is_placeholder_player_fn=is_placeholder_player,
    build_immortal_embed_fn=build_immortal_embed,
)
configure_match_imp_service(
    bot_instance=bot,
    get_discord_id_from_steam_id_fn=get_discord_id_from_steam_id,
    update_balance_fn=update_balance,
)
configure_live_tracking(
    bot_instance=bot,
    db_client=db,
    steam_api_key_value=STEAM_API_KEY,
    stratz_token_value=STRATZ_TOKEN,
    get_http_session_fn=get_http_session,
    format_live_match_embed_fn=format_live_match_embed,
    fetch_match_result_fn=fetch_match_result,
    map_steam_ids_to_discord_ids_fn=map_steam_ids_to_discord_ids,
    resolve_bets_fn=resolve_bets,
    get_active_double_down_users_fn=get_active_double_down_users,
    adjust_mmr_fn=adjust_mmr,
    clear_active_double_downs_fn=clear_active_double_downs,
    update_balance_fn=update_balance,
    get_processed_match_fn=get_processed_match,
    is_match_processed_fn=is_match_processed,
    log_processed_match_fn=log_processed_match,
    get_bound_league_id_fn=get_bound_league_id,
    log_match_ledger_fn=log_match_ledger,
    get_discord_id_from_steam_id_fn=get_discord_id_from_steam_id,
    schedule_match_imp_enrichment_fn=schedule_match_imp_enrichment,
)

deps = {
    # checks
    "user_is_admin_or_has_role": user_is_admin_or_has_role,
    "is_admin_or_has_role": is_admin_or_has_role,
    "is_global_admin": is_global_admin,
    # firestore
    "db": db,
    "firestore": firestore,
    "rsvp_manager": rsvp_manager,
    # player/MMR
    "convert_to_steam32": convert_to_steam32,
    "fetch_mmr": fetch_mmr,
    "save_player_config": save_player_config,
    "get_mmr": get_mmr,
    "get_inhouse_mmr": get_inhouse_mmr,
    "get_top_players": get_top_players,
    # Feederbucks/betting
    "get_balance": get_balance,
    "place_bet": place_bet,
    "place_market_bet": place_market_bet,
    "update_balance": update_balance,
    "resolve_bets": resolve_bets,
    "clear_guild_bets": clear_guild_bets,
    "BETTING_MODE_CLASSIC": BETTING_MODE_CLASSIC,
    "BETTING_MODE_POOL": BETTING_MODE_POOL,
    "MIN_BET_AMOUNT": MIN_BET_AMOUNT,
    "MARKET_MATCH_WINNER": MARKET_MATCH_WINNER,
    "MARKET_FIRST_BLOOD": MARKET_FIRST_BLOOD,
    "MARKET_FIRST_TO_10": MARKET_FIRST_TO_10,
    "MARKET_FIRST_TOWER": MARKET_FIRST_TOWER,
    "MARKET_DURATION_35": MARKET_DURATION_35,
    "MARKET_TOTAL_KILLS_50": MARKET_TOTAL_KILLS_50,
    "MARKET_ORDER": MARKET_ORDER,
    "get_betting_settings": get_betting_settings,
    "get_betting_summary": get_betting_summary,
    "get_existing_market_bet": get_existing_market_bet,
    "get_public_market_snapshots": get_public_market_snapshots,
    "is_market_open_for_betting": is_market_open_for_betting,
    "normalize_market_id": normalize_market_id,
    "save_betting_mode_for_guild": save_betting_mode_for_guild,
    "process_live_betting_markets": process_live_betting_markets,
    "save_prop_markets_setting": save_prop_markets_setting,
    "void_market": void_market,
    "void_markets": void_markets,
    "void_user_bet": void_user_bet,
    "void_user_bets": void_user_bets,
    "DD_TOKEN_COST": DD_TOKEN_COST,
    "get_dd_token_balance": get_dd_token_balance,
    "update_dd_token_balance": update_dd_token_balance,
    "has_active_double_down": has_active_double_down,
    "activate_double_down": activate_double_down,
    "get_active_double_down_users": get_active_double_down_users,
    "clear_active_double_downs": clear_active_double_downs,
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
    "bets_embed_messages": bets_embed_messages,
    "bets_refresh_tasks": bets_refresh_tasks,
    "match_wait_tasks": match_wait_tasks,
    "original_teams": original_teams,
    # lobby + helpers
    "lobby_players": lobby_players,
    "lobby_message": lobby_message,
    "lobby_roster_locks": lobby_roster_locks,
    "inhouse_mode": inhouse_mode,
    "captain_draft_state": captain_draft_state,
    "immortal_draft_running": immortal_draft_running,
    "rocket_lock": rocket_lock,
    "update_lobby_embed": update_lobby_embed,
    "build_lobby_embed": build_lobby_embed,
    "build_immortal_embed": build_immortal_embed,
    "save_lobby_players": save_lobby_players,
    "save_lobby_roster_lock": save_lobby_roster_lock,
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
    "cancel_match_wait": cancel_match_wait,
    "reset_team_state_for_guild": reset_team_state_for_guild,
    "full_post_rocket_reset": full_post_rocket_reset,
    "is_placeholder_player": is_placeholder_player,
    "format_lobby_player_mention": format_lobby_player_mention,
    # guild settings
    "save_guild_prefix": save_guild_prefix,
    "load_guild_prefix": load_guild_prefix,
    "get_store_item_info": get_store_item_info,
    "normalize_store_item_name": normalize_store_item_name,
    "get_store_cost": get_store_cost,
    "save_store_cost_override": save_store_cost_override,
    "purchase_match_mute": purchase_match_mute,
    "purchase_store_role": purchase_store_role,
    "unmute_match_store_mutes": unmute_match_store_mutes,
    "log_store_purchase": log_store_purchase,
    "reset_vip_feeder_role": reset_vip_feeder_role,
    "reset_custom_store_roles": reset_custom_store_roles,
    "save_league_guild_mapping": save_league_guild_mapping,
    "live_channel_ids": live_channel_ids,
    "lobby_channel_ids": lobby_channel_ids,
    "get_lobby_channel_for_guild": get_lobby_channel_for_guild,
    "prefix_cache": prefix_cache,
    # misc
    "get_discord_id_from_steam_id": get_discord_id_from_steam_id,
    "adjust_mmr": adjust_mmr,
    "get_bound_league_id": get_bound_league_id,
    "get_processed_match": get_processed_match,
    "is_match_processed": is_match_processed,
    "log_processed_match": log_processed_match,
    "log_match_ledger": log_match_ledger,
    "get_all_match_ledgers": get_all_match_ledgers,
    "get_recent_match_ledgers": get_recent_match_ledgers,
    "schedule_match_imp_enrichment": schedule_match_imp_enrichment,
    "get_top_avg_imp_players": get_top_avg_imp_players,
    "save_preferred_roles_setting": save_preferred_roles_setting,
    "save_separated_pair": save_separated_pair,
    "delete_separated_pair": delete_separated_pair,
    "get_separated_pairs": get_separated_pairs,
}
set_cancel_callback(handle_immortal_draft_cancel)
attach_commands(bot, deps)

if __name__ == "__main__":
    try:
        bot.run(TOKEN)  # blocks until SIGTERM / shutdown
    finally:
        # event loop used by bot.run() is gone; create a tiny loop to close cleanly
        asyncio.run(close_http_session())
