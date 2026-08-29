import asyncio
import json
import random

import discord

from bot.services.betting_manager import ensure_match_betting_state, process_live_betting_markets
from bot.services.stratz_guard import (
    format_stratz_block,
    note_stratz_response,
    reserve_stratz_request,
)
from bot.services.store_service import unmute_match_store_mutes
from bot.state.runtime_state import (
    _last_active_match_id,
    _last_fetch_stats,
    _last_selected_match_id,
    active_match_ids,
    bets_refresh_tasks,
    live_channel_ids,
    live_embed_messages,
    lobby_players,
    match_wait_tasks,
    match_tracking_start_times,
    polling_tasks,
    random_polling_flags,
)

season_rank_to_mmr = {
    11: 77, 12: 231, 13: 385, 14: 539, 15: 693,
    21: 847, 22: 1001, 23: 1155, 24: 1309, 25: 1463,
    31: 1594, 32: 1749, 33: 1953, 34: 2081, 35: 2208,
    41: 2387, 42: 2541, 43: 2695, 44: 2849, 45: 3003,
    51: 3157, 52: 3311, 53: 3465, 54: 3619, 55: 3772,
    61: 3927, 62: 4081, 63: 4235, 64: 4389, 65: 4542,
    71: 4720, 72: 4920, 73: 5120, 74: 5320, 75: 5520
}

bot = None
db = None
steam_api_key = None
stratz_token = None
get_http_session = None
format_live_match_embed = None
fetch_match_result = None
map_steam_ids_to_discord_ids = None
resolve_bets = None
get_active_double_down_users = None
adjust_mmr = None
clear_active_double_downs = None
update_balance = None
get_processed_match = None
is_match_processed = None
log_processed_match = None
get_bound_league_id = None
log_match_ledger = None
get_discord_id_from_steam_id = None
schedule_match_imp_enrichment = None
on_inhouse_match_resolved = None
on_match_wait_outcome = None
on_inhouse_result_pending = None

PARTICIPATION_FEEDERBUCKS_AWARD = 100


def configure_live_tracking(
    *,
    bot_instance,
    db_client,
    steam_api_key_value,
    stratz_token_value,
    get_http_session_fn,
    format_live_match_embed_fn,
    fetch_match_result_fn,
    map_steam_ids_to_discord_ids_fn,
    resolve_bets_fn,
    get_active_double_down_users_fn,
    adjust_mmr_fn,
    clear_active_double_downs_fn,
    update_balance_fn,
    get_processed_match_fn,
    is_match_processed_fn,
    log_processed_match_fn,
    get_bound_league_id_fn,
    log_match_ledger_fn,
    get_discord_id_from_steam_id_fn,
    schedule_match_imp_enrichment_fn,
    on_inhouse_match_resolved_fn,
    on_match_wait_outcome_fn,
    on_inhouse_result_pending_fn,
):
    global bot, db, steam_api_key, stratz_token, get_http_session
    global format_live_match_embed, fetch_match_result, map_steam_ids_to_discord_ids
    global resolve_bets, get_active_double_down_users, adjust_mmr, clear_active_double_downs
    global update_balance, get_processed_match, is_match_processed, log_processed_match
    global get_bound_league_id, log_match_ledger, get_discord_id_from_steam_id
    global schedule_match_imp_enrichment, on_inhouse_match_resolved
    global on_match_wait_outcome, on_inhouse_result_pending
    bot = bot_instance
    db = db_client
    steam_api_key = steam_api_key_value
    stratz_token = stratz_token_value
    get_http_session = get_http_session_fn
    format_live_match_embed = format_live_match_embed_fn
    fetch_match_result = fetch_match_result_fn
    map_steam_ids_to_discord_ids = map_steam_ids_to_discord_ids_fn
    resolve_bets = resolve_bets_fn
    get_active_double_down_users = get_active_double_down_users_fn
    adjust_mmr = adjust_mmr_fn
    clear_active_double_downs = clear_active_double_downs_fn
    update_balance = update_balance_fn
    get_processed_match = get_processed_match_fn
    is_match_processed = is_match_processed_fn
    log_processed_match = log_processed_match_fn
    get_bound_league_id = get_bound_league_id_fn
    log_match_ledger = log_match_ledger_fn
    get_discord_id_from_steam_id = get_discord_id_from_steam_id_fn
    schedule_match_imp_enrichment = schedule_match_imp_enrichment_fn
    on_inhouse_match_resolved = on_inhouse_match_resolved_fn
    on_match_wait_outcome = on_match_wait_outcome_fn
    on_inhouse_result_pending = on_inhouse_result_pending_fn


def clear_match_tracking_state(guild_id: int):
    active_match_ids.pop(guild_id, None)
    polling_tasks.pop(guild_id, None)
    random_polling_flags.pop(guild_id, None)
    match_tracking_start_times.pop(guild_id, None)
    live_embed_messages.pop(guild_id, None)
    bets_task = bets_refresh_tasks.pop(guild_id, None)
    if bets_task and not bets_task.done():
        bets_task.cancel()
    _last_fetch_stats.pop(guild_id, None)
    _last_active_match_id.pop(guild_id, None)
    _last_selected_match_id.pop(guild_id, None)


def build_ledger_player_stats(guild, raw_player_stats):
    results = []
    for stat in raw_player_stats or []:
        steam_id = str(stat.get("steam_id", ""))
        discord_id = get_discord_id_from_steam_id(steam_id) if steam_id else None
        member = guild.get_member(int(discord_id)) if discord_id and guild else None
        nickname = member.display_name if member else (str(discord_id) if discord_id else steam_id)
        enriched = dict(stat)
        enriched["steam_id"] = steam_id
        enriched["user_id"] = str(discord_id) if discord_id else None
        enriched["nickname"] = nickname
        results.append(enriched)
    return results


def build_feederbucks_award(award_id, user_id, nickname, amount, reason, balance_after=None):
    payload = {
        "award_id": str(award_id),
        "user_id": str(user_id),
        "nickname": nickname,
        "amount": int(amount or 0),
        "reason": reason,
    }
    if balance_after is not None:
        payload["balance_after"] = int(balance_after)
        payload["balance_before"] = int(balance_after) - int(amount or 0)
    return payload


async def poll_live_match(match_id, guild, random_mode=False):
    print(f"[poll_live_match] Started polling match {match_id} for guild {guild.name} (random_mode={random_mode})")
    live_embed_messages.pop(guild.id, None)
    channel_id = live_channel_ids.get(guild.id)
    channel = bot.get_channel(channel_id) if channel_id else None
    while True:
        await asyncio.sleep(15)
        try:
            match = await fetch_live_match_for_guild(guild.id, random_mode=random_mode)
            if not match:
                print(f"[poll_live_match] Match {match_id} no longer reported as live. Stopping Steam polling.")
                break
            process_live_betting_markets(guild.id, match_id, match)
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
            print(f"[poll_live_match] Error for guild {str(guild.id)}: {e}")
    if not random_mode:
        try:
            await unmute_match_store_mutes(
                guild,
                match_id,
                reason=f"Mute a Feeder ended because match {match_id} is no longer live on Steam.",
            )
        except Exception as e:
            print(f"[poll_live_match] Failed to release match mute(s) for match {match_id}: {e}")
    max_retries = 10
    retry_delay = 30
    result = None
    for attempt in range(max_retries):
        result = await asyncio.to_thread(fetch_match_result, match_id)
        if result:
            break
        print(f"[poll_live_match] No match result yet for match {match_id}. Retrying in {retry_delay} seconds... (attempt {attempt + 1}/{max_retries})")
        await asyncio.sleep(retry_delay)
    if not result:
        print(f"[poll_live_match] No match result found for match {match_id} after {max_retries} attempts. Skipping bet resolution.")
        clear_match_tracking_state(guild.id)
        if channel:
            try:
                await channel.send("Match ended but no result was found. Polling has been stopped.")
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as e:
                print(f"[poll_live_match] Failed to report missing result for match {match_id}: {e}")
        if not random_mode and on_inhouse_result_pending:
            try:
                await on_inhouse_result_pending(guild, channel, match_id)
            except Exception as e:
                print(f"[poll_live_match] Failed to pause RSVP series for missing result {match_id}: {e}")
        return
    if is_match_processed(guild.id, match_id):
        existing = get_processed_match(guild.id, match_id) or {}
        print(f"[poll_live_match] Match {match_id} was already processed for guild {guild.id}. Skipping duplicate resolution.")
        clear_match_tracking_state(guild.id)
        if channel:
            processed_at = existing.get("processed_at", "unknown time")
            try:
                await channel.send(
                    f"Match `{match_id}` was already processed earlier"
                    f" (source: `{existing.get('source', 'unknown')}`, processed_at: `{processed_at}`). "
                    "Skipping duplicate resolution."
                )
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as e:
                print(f"[poll_live_match] Failed to report duplicate result for match {match_id}: {e}")
        if not random_mode and on_inhouse_match_resolved:
            try:
                await on_inhouse_match_resolved(
                    guild,
                    channel,
                    match_id,
                    require_current_match=True,
                )
            except Exception as e:
                print(f"[poll_live_match] Failed to reconcile RSVP series for match {match_id}: {e}")
        return
    winning_team = "radiant" if result["radiant_win"] else "dire"
    winner_ids = map_steam_ids_to_discord_ids(result["radiantplayers"] if result["radiant_win"] else result["direplayers"])
    loser_ids = map_steam_ids_to_discord_ids(result["direplayers"] if result["radiant_win"] else result["radiantplayers"])
    player_stats = build_ledger_player_stats(guild, result.get("player_stats", []))
    bet_results = resolve_bets(guild.id, winning_team, match_id=match_id, match_result=result)
    mmr_changes = []
    if not random_mode:
        try:
            doubled_user_ids = get_active_double_down_users(guild.id)
            mmr_changes = await adjust_mmr(bot, winner_ids, loser_ids, guild.id, doubled_user_ids=doubled_user_ids)
            clear_active_double_downs(guild.id)
        except Exception as e:
            print(f"[poll_live_match] Failed to adjust MMR: {e}")
    all_player_ids = winner_ids + loser_ids
    feederbucks_awards = []
    for discord_id in all_player_ids:
        member = guild.get_member(int(discord_id))
        nickname = member.display_name if member else str(discord_id)
        balance_after = update_balance(
            guild.id,
            str(discord_id),
            PARTICIPATION_FEEDERBUCKS_AWARD,
            nickname=nickname,
        )
        feederbucks_awards.append(build_feederbucks_award(
            f"participation_{discord_id}",
            str(discord_id),
            nickname,
            PARTICIPATION_FEEDERBUCKS_AWARD,
            "Participation",
            balance_after=balance_after,
        ))
    should_log_random_match = bool(bet_results)
    if not random_mode:
        try:
            log_processed_match(
                guild.id,
                match_id,
                league_id=get_bound_league_id(guild.id),
                source="auto_poll",
                processors=["betting", "inhouse_mmr", "feederbucks"],
                winning_team=winning_team,
                random_mode=False,
                processed_by="system",
                player_count=len(all_player_ids),
            )
        except Exception as e:
            print(f"[poll_live_match] Failed to log processed match {match_id}: {e}")
        try:
            log_match_ledger(
                guild.id,
                match_id,
                league_id=get_bound_league_id(guild.id),
                source="auto_poll",
                winning_team=winning_team,
                random_mode=False,
                processed_by="system",
                mmr_changes=mmr_changes,
                bet_results=bet_results,
                player_stats=player_stats,
                feederbucks_awards=feederbucks_awards,
            )
        except Exception as e:
            print(f"[poll_live_match] Failed to log ledger entry for match {match_id}: {e}")
        if schedule_match_imp_enrichment:
            try:
                schedule_match_imp_enrichment(
                    guild.id,
                    match_id,
                    channel_id=channel.id if channel else None,
                    notify_on_success=True,
                )
            except Exception as e:
                print(f"[poll_live_match] Failed to schedule IMP enrichment for match {match_id}: {e}")
    elif should_log_random_match:
        try:
            log_match_ledger(
                guild.id,
                match_id,
                league_id=None,
                source="auto_poll",
                winning_team=winning_team,
                random_mode=True,
                processed_by="system",
                mmr_changes=[],
                bet_results=bet_results,
                player_stats=[],
                feederbucks_awards=feederbucks_awards,
            )
        except Exception as e:
            print(f"[poll_live_match] Failed to log random bet ledger entry for match {match_id}: {e}")
    else:
        print(f"[poll_live_match] Random match {match_id} had no bets. Skipping match_data logging.")
    print(
        f"[poll_live_match] Awarded {PARTICIPATION_FEEDERBUCKS_AWARD} Feederbucks "
        f"to {len(all_player_ids)} participants in match {match_id}"
    )
    try:
        if random_mode:
            await channel.send(
                f"Match `{match_id}` has ended with a {winning_team} victory. Bets have been resolved.\n"
                f"All participants received **{PARTICIPATION_FEEDERBUCKS_AWARD} Feederbucks** for playing."
            )
        else:
            await channel.send(
                f"Match `{match_id}` has ended with a {winning_team} victory. Bets have been resolved and Inhouse-MMR updated.\n"
                f"All participants received **{PARTICIPATION_FEEDERBUCKS_AWARD} Feederbucks** for playing."
            )
        print(f"[poll_live_match] Match summary sent to channel ID: {channel.id}")
    except Exception as e:
        print(f"[poll_live_match] Failed to send match summary: {e}")
    clear_match_tracking_state(guild.id)
    if not random_mode and on_inhouse_match_resolved:
        try:
            await on_inhouse_match_resolved(guild, channel, match_id)
        except Exception as e:
            print(f"[poll_live_match] Failed to advance RSVP series after match {match_id}: {e}")


def convert_to_steam32(steam_id_input):
    try:
        if isinstance(steam_id_input, str):
            steam_id_input = int(steam_id_input.strip().replace(" ", ""))
        elif not isinstance(steam_id_input, int):
            return None
        if steam_id_input > 76561197960265728:
            return steam_id_input - 76561197960265728
        return steam_id_input
    except (ValueError, TypeError):
        return None


async def fetch_mmr(steam_id, max_retries: int = 2):
    steam_id = convert_to_steam32(steam_id)
    url = "https://api.stratz.com/graphql"
    headers = {
        "Authorization": f"Bearer {stratz_token}",
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
            blocked, block_reason, blocked_until = await reserve_stratz_request()
            if blocked:
                print(f"[fetch_mmr] Skipping STRATZ for steam_id={steam_id}: {format_stratz_block(block_reason, blocked_until)}")
                break
            async with get_http_session().post(url, json=query, headers=headers, timeout=8) as response:
                if response.status == 200:
                    data = await response.json()
                    player_data = data.get("data", {}).get("player", {})
                    if player_data and player_data.get("steamAccount"):
                        season_rank = player_data["steamAccount"].get("seasonRank")
                        if season_rank:
                            mmr = season_rank_to_mmr.get(season_rank)
                            return mmr, season_rank, "STRATZ"
                        print(f"[fetch_mmr] STRATZ 200 but no seasonRank for steam_id={steam_id}; falling back to OpenDota")
                        break
                else:
                    txt = (await response.text()).strip()
                    print(
                        f"[fetch_mmr] STRATZ {response.status} "
                        f"(attempt {attempt+1}/{max_retries}) "
                        f"for steam_id={steam_id}: {txt[:180]}"
                    )
                    if note_stratz_response(response.status, txt, headers=response.headers, endpoint="fetch_mmr"):
                        break
                    if response.status in (403, 429, 500, 502, 503, 504):
                        continue
                    break
        except Exception as e:
            print(
                f"[fetch_mmr] STRATZ request failed "
                f"(attempt {attempt+1}/{max_retries}) for steam_id={steam_id}: {e}"
            )
    try:
        od_url = f"https://api.opendota.com/api/players/{steam_id}"
        async with get_http_session().get(od_url, timeout=8) as r:
            if r.status != 200:
                txt = (await r.text()).strip()
                print(f"[fetch_mmr] OpenDota {r.status} for steam_id={steam_id}: {txt[:180]}")
                return None, None, None
            j = await r.json()
            rank_tier = j.get("rank_tier")
            if not rank_tier:
                return None, None, None
            mmr = season_rank_to_mmr.get(rank_tier)
            return mmr, rank_tier, "OpenDota"
    except Exception as e:
        print(f"[fetch_mmr] OpenDota fallback failed for steam_id={steam_id}: {e}")
    return None, None, None


async def fetch_live_match_for_guild(guild_id, random_mode=False, excluded_match_ids=None):
    doc_ref = db.collection("guild_specific_info").document(str(guild_id))
    doc = doc_ref.get()
    if not doc.exists:
        print(f"[fetch_live_match_for_guild] No guild_specific_info found for guild {guild_id}")
        return None
    league_info = doc.to_dict().get("league_id", {})
    bound_league_id = league_info.get("bound_league_id")
    if not random_mode and not bound_league_id:
        print(f"[fetch_live_match_for_guild] No bound_league_id found in Firestore for guild {guild_id}")
        return None
    url = "https://api.steampowered.com/IDOTA2Match_570/GetLiveLeagueGames/v1/"
    params = {"key": steam_api_key}
    try:
        async with get_http_session().get(url, params=params, timeout=5) as response:
            if response.status != 200:
                print(f"[fetch_live_match_for_guild] Steam API returned {response.status}")
                return None
            raw = await response.read()
            try:
                result = json.loads(raw.decode("utf-8"))
            except UnicodeDecodeError as e:
                if random.random() < 0.1:
                    print(f"[fetch_live_match_for_guild] Steam API UTF-8 decode error (sampled): {e}")
                try:
                    result = json.loads(raw.decode("utf-8", errors="ignore"))
                except Exception as fallback_error:
                    print(f"[fetch_live_match_for_guild] Steam API fallback parse failed: {fallback_error}")
                    return None
            except json.JSONDecodeError as e:
                print(f"[fetch_live_match_for_guild] Steam API JSON decode error: {e}")
                try:
                    result = json.loads(raw.decode("utf-8", errors="ignore"))
                except Exception as fallback_error:
                    print(f"[fetch_live_match_for_guild] Steam API fallback parse failed: {fallback_error}")
                    return None
            matches = result.get("result", {}).get("games", [])
        matches = result.get("result", {}).get("games", [])
        valid_matches = []
        for m in matches:
            has_scoreboard = bool(m.get("scoreboard"))
            if random_mode:
                if not has_scoreboard:
                    continue
                dur = m["scoreboard"].get("duration", 0)
                if dur < 1800:
                    continue
                valid_matches.append(m)
            else:
                if not has_scoreboard:
                    valid_matches.append(m)
                    continue
                valid_matches.append(m)
        if not valid_matches:
            if guild_id in active_match_ids:
                print(f"[fetch_live_match_for_guild] Clearing expired match for guild {guild_id}")
                del active_match_ids[guild_id]
                _last_fetch_stats.pop(guild_id, None)
                _last_active_match_id.pop(guild_id, None)
                _last_selected_match_id.pop(guild_id, None)
            return None
        stats = (len(matches), len(valid_matches))
        if _last_fetch_stats.get(guild_id) != stats:
            print(f"[fetch_live_match_for_guild] Checked {stats[0]} total live matches from Steam API.")
            print(f"[fetch_live_match_for_guild] {stats[1]} passed scoreboard and duration filters.")
            _last_fetch_stats[guild_id] = stats
        bound_matches = valid_matches if random_mode else [
            m for m in valid_matches if str(m.get("league_id")) == str(bound_league_id)
        ]
        excluded_ids = {str(value) for value in (excluded_match_ids or [])}
        if excluded_ids and not random_mode:
            bound_matches = [
                match for match in bound_matches
                if str(match.get("match_id")) not in excluded_ids
            ]
        if not bound_matches:
            print(
                f"[fetch_live_match_for_guild] No new live matches found for bound league_id "
                f"{bound_league_id} in guild {guild_id}"
            )
            return None
        last_match_id = active_match_ids.get(guild_id)
        prev_active_id = _last_active_match_id.get(guild_id)
        if prev_active_id != last_match_id:
            if last_match_id:
                print(f"[fetch_live_match_for_guild] Step 4 triggered: active_match_ids[{guild_id}] = {last_match_id}")
            else:
                print(f"[fetch_live_match_for_guild] Step 4 skipped: No active match found for guild {guild_id}")
            _last_active_match_id[guild_id] = last_match_id
        selected_match = next((m for m in bound_matches if m.get("match_id") == last_match_id), None)
        if selected_match is None:
            if last_match_id:
                match_kind = "random match" if random_mode else "inhouse match"
                print(
                    f"[fetch_live_match_for_guild] Tracked {match_kind} {last_match_id} is no longer live. "
                    "Waiting for match resolution."
                )
                return None
            selected_match = random.choice(bound_matches)
            active_match_ids[guild_id] = selected_match["match_id"]
        sel_id = selected_match["match_id"]
        prev_sel_id = _last_selected_match_id.get(guild_id)
        if prev_sel_id != sel_id:
            if last_match_id and sel_id == last_match_id:
                print(f"[fetch_live_match_for_guild] Reusing existing match_id {last_match_id} for guild {guild_id}")
            else:
                print(f"[fetch_live_match_for_guild] Picked new match_id {sel_id} for guild {guild_id}")
            _last_selected_match_id[guild_id] = sel_id
        selected_match["guild_id"] = guild_id
        ensure_match_betting_state(guild_id, selected_match["match_id"], random_mode=random_mode)
        process_live_betting_markets(guild_id, selected_match["match_id"], selected_match)
        return selected_match
    except Exception as e:
        print(f"[fetch_live_match_for_guild] Steam API error: {e}")
        return None


async def _notify_match_wait_outcome(guild, channel, outcome, *, game_number=None, match_id=None):
    if on_match_wait_outcome is None:
        return
    try:
        await on_match_wait_outcome(
            guild,
            channel,
            outcome,
            game_number=game_number,
            match_id=match_id,
        )
    except Exception as exc:
        print(f"[wait_for_match] Failed to record `{outcome}` outcome for guild {guild.id}: {exc}")


async def _send_match_wait_message(channel, content):
    try:
        await channel.send(content)
    except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
        print(f"[wait_for_match] Failed to send wait update: {exc}")


async def wait_for_match_then_start_polling(
    guild_id: int,
    guild: discord.Guild,
    channel: discord.TextChannel,
    *,
    timeout_seconds: int = 15 * 60,
    excluded_match_ids=None,
    game_number: int | None = None,
    scheduled_series: bool = False,
):
    timeout = max(1, int(timeout_seconds))
    interval = 30
    elapsed = 0
    low_lobby_time = 0
    current_task = asyncio.current_task()
    try:
        timeout_minutes = max(1, (timeout + 59) // 60)
        await _send_match_wait_message(
            channel,
            f"Waiting for the in-game match to appear on Steam... (up to {timeout_minutes} minutes)"
        )
        while elapsed < timeout:
            current_lobby = lobby_players.get(guild_id, [])
            if len(current_lobby) < 10:
                low_lobby_time += interval
                print(f"[wait_for_match] Lobby underfilled ({len(current_lobby)}/10) for {low_lobby_time} seconds")
                if low_lobby_time >= 30:
                    await _send_match_wait_message(
                        channel,
                        "Lobby has not been full for 30 seconds. Match polling cancelled.",
                    )
                    await _notify_match_wait_outcome(
                        guild,
                        channel,
                        "underfilled",
                        game_number=game_number,
                    )
                    return
            else:
                if low_lobby_time > 0:
                    print("[wait_for_match] Lobby refilled to 10/10 — resetting grace timer.")
                low_lobby_time = 0
            match = await fetch_live_match_for_guild(
                guild.id,
                excluded_match_ids=excluded_match_ids,
            )
            if match:
                match_id = match.get("match_id")
                await _notify_match_wait_outcome(
                    guild,
                    channel,
                    "match_found",
                    game_number=game_number,
                    match_id=match_id,
                )
                if guild_id not in polling_tasks:
                    active_match_ids[guild_id] = match_id
                    polling_tasks[guild_id] = asyncio.create_task(poll_live_match(match_id, guild))
                    await _send_match_wait_message(
                        channel,
                        f"Started match polling for match ID {match_id} in {guild.name}",
                    )
                return
            await asyncio.sleep(interval)
            elapsed += interval
        if scheduled_series:
            timeout_message = (
                f"No new live match was found within {timeout_minutes} minutes. "
                "The same scheduled game is still pending; an Inhouse Admin can press 🚀 to retry."
            )
        else:
            timeout_message = (
                f"No live match was found within {timeout_minutes} minutes. Please restart the lobby."
            )
        await _send_match_wait_message(channel, timeout_message)
        await _notify_match_wait_outcome(
            guild,
            channel,
            "timeout",
            game_number=game_number,
        )
    except asyncio.CancelledError:
        print(f"[wait_for_match] Cancelled wait task for guild {guild_id}")
        return
    finally:
        if match_wait_tasks.get(guild_id) is current_task:
            match_wait_tasks.pop(guild_id, None)
