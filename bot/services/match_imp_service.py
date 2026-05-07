import asyncio
from datetime import date, datetime, timedelta, timezone

import discord

import bot.storage.firebase_setup  # ensures Firebase is initialized before anything else
from firebase_admin import firestore

from bot.services.match_tracker import fetch_match_imp_data

db = firestore.client()

bot = None
get_discord_id_from_steam_id = None
update_balance = None
_imp_enrichment_tasks = {}

IMP_RETRY_INTERVAL_SECONDS = 150
IMP_RETRY_WINDOW_SECONDS = 600
IMP_MAX_ATTEMPTS = 3
IMP_MIN_MATCHES_FOR_AVERAGE = 4
MVP_FEEDERBUCKS_AWARD = 1000
MVP_FEEDERBUCKS_AWARD_ID = "mvp_highest_imp"


def configure_match_imp_service(*, bot_instance, get_discord_id_from_steam_id_fn, update_balance_fn=None):
    global bot, get_discord_id_from_steam_id, update_balance
    bot = bot_instance
    get_discord_id_from_steam_id = get_discord_id_from_steam_id_fn
    update_balance = update_balance_fn


def _match_ref(guild_id, match_id):
    return (
        db.collection("match_data")
        .document(str(guild_id))
        .collection("matches")
        .document(str(match_id))
    )


def _to_utc_datetime(value):
    if hasattr(value, "to_datetime"):
        value = value.to_datetime()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


def _next_attempt_at(started_at):
    return started_at + timedelta(days=1)


def _ensure_imp_fields(player_stats):
    updated = []
    for stat in player_stats or []:
        merged = dict(stat)
        merged.setdefault("position", None)
        merged.setdefault("imp", None)
        updated.append(merged)
    return updated


def _merge_imp_player_stats(existing_player_stats, imp_players):
    player_stats = _ensure_imp_fields(existing_player_stats)
    imp_by_steam = {
        str(player.get("steam_id")): player
        for player in (imp_players or [])
        if player.get("steam_id") is not None
    }
    merged_any = False

    for stat in player_stats:
        steam_id = str(stat.get("steam_id", ""))
        imp_player = imp_by_steam.get(steam_id)
        if not imp_player:
            continue
        stat["position"] = imp_player.get("position")
        stat["imp"] = imp_player.get("imp")
        stat["steam_name"] = imp_player.get("steam_name")
        stat["hero_id"] = imp_player.get("hero_id")
        stat["hero_name"] = imp_player.get("hero_name")
        if not stat.get("user_id") and get_discord_id_from_steam_id and steam_id:
            discord_id = get_discord_id_from_steam_id(steam_id)
            if discord_id:
                stat["user_id"] = str(discord_id)
        merged_any = True

    if merged_any:
        return player_stats

    # Fallback path for any historical record that somehow lacks player_stats.
    for imp_player in imp_players or []:
        steam_id = str(imp_player.get("steam_id") or "")
        discord_id = get_discord_id_from_steam_id(steam_id) if get_discord_id_from_steam_id and steam_id else None
        player_stats.append({
            "steam_id": steam_id,
            "user_id": str(discord_id) if discord_id else None,
            "nickname": imp_player.get("steam_name") or steam_id,
            "position": imp_player.get("position"),
            "imp": imp_player.get("imp"),
            "steam_name": imp_player.get("steam_name"),
            "hero_id": imp_player.get("hero_id"),
            "hero_name": imp_player.get("hero_name"),
        })
    return player_stats


def _null_imp_player_stats(existing_player_stats):
    player_stats = _ensure_imp_fields(existing_player_stats)
    for stat in player_stats:
        stat["position"] = None
        stat["imp"] = None
    return player_stats


def _has_complete_imp_data(match_data):
    player_stats = match_data.get("player_stats") or []
    if not player_stats:
        return False
    return all(
        stat.get("position") is not None and stat.get("imp") is not None
        for stat in player_stats
    )


def _get_mvp(player_stats):
    candidates = [stat for stat in (player_stats or []) if stat.get("imp") is not None]
    if not candidates:
        return None
    best = max(candidates, key=lambda stat: int(stat.get("imp", 0)))
    return {
        "user_id": best.get("user_id"),
        "nickname": best.get("nickname") or best.get("steam_name") or best.get("steam_id") or "Unknown",
        "imp": int(best.get("imp", 0)),
        "position": best.get("position"),
    }


def _mark_mvp_award_on_player_stats(player_stats, mvp):
    user_id = str((mvp or {}).get("user_id") or "")
    if not user_id:
        return player_stats
    for stat in player_stats or []:
        if str(stat.get("user_id") or "") == user_id:
            stat["mvp"] = True
            stat["mvp_feederbucks_award"] = MVP_FEEDERBUCKS_AWARD
            stat["mvp_award_id"] = MVP_FEEDERBUCKS_AWARD_ID
            break
    return player_stats


def _mvp_award_already_recorded(match_data):
    if bool(match_data.get("mvp_feederbucks_awarded")):
        return True
    return any(
        str(award.get("award_id") or "") == MVP_FEEDERBUCKS_AWARD_ID
        for award in (match_data.get("feederbucks_awards") or [])
        if isinstance(award, dict)
    )


def _build_mvp_feederbucks_update(guild_id, match_id, match_data, mvp, player_stats):
    if not mvp:
        return {}

    user_id = str(mvp.get("user_id") or "")
    if not user_id.isdigit():
        return {}

    existing_awards = [
        award for award in (match_data.get("feederbucks_awards") or [])
        if isinstance(award, dict)
    ]
    if _mvp_award_already_recorded(match_data):
        player_stats = _mark_mvp_award_on_player_stats(player_stats, mvp)
        return {
            "player_stats": player_stats,
            "mvp_feederbucks_awarded": True,
            "mvp_feederbucks_award_amount": MVP_FEEDERBUCKS_AWARD,
            "feederbucks_awards": existing_awards,
        }

    if update_balance is None:
        print(f"[match_imp] Cannot award MVP Feederbucks for match {match_id}: update_balance is not configured.")
        return {}

    nickname = mvp.get("nickname") or f"User {user_id}"
    try:
        balance_after = update_balance(guild_id, user_id, MVP_FEEDERBUCKS_AWARD, nickname=nickname)
    except Exception as e:
        print(f"[match_imp] Failed to award MVP Feederbucks for guild={guild_id} match={match_id} user={user_id}: {e}")
        return {}

    player_stats = _mark_mvp_award_on_player_stats(player_stats, mvp)
    existing_awards.append({
        "award_id": MVP_FEEDERBUCKS_AWARD_ID,
        "user_id": user_id,
        "nickname": nickname,
        "amount": MVP_FEEDERBUCKS_AWARD,
        "reason": "MVP Bonus",
        "source": "highest_imp",
        "imp": mvp.get("imp"),
        "position": mvp.get("position"),
        "balance_before": int(balance_after) - MVP_FEEDERBUCKS_AWARD,
        "balance_after": int(balance_after),
    })
    return {
        "player_stats": player_stats,
        "mvp_feederbucks_awarded": True,
        "mvp_feederbucks_award_amount": MVP_FEEDERBUCKS_AWARD,
        "mvp_feederbucks_awarded_at": firestore.SERVER_TIMESTAMP,
        "feederbucks_awards": existing_awards,
    }


async def _announce_mvp(guild_id, match_id, channel_id, winning_team, player_stats):
    if not bot or not channel_id:
        return
    try:
        channel = bot.get_channel(int(channel_id))
    except Exception:
        channel = None
    if channel is None:
        return

    mvp = _get_mvp(player_stats)
    if not mvp:
        return

    mention = f"<@{mvp['user_id']}>" if str(mvp.get("user_id") or "").isdigit() else mvp["nickname"]
    position_text = mvp.get("position") or "unknown position"
    awarded = any(
        str(stat.get("user_id") or "") == str(mvp.get("user_id") or "")
        and bool(stat.get("mvp_feederbucks_award"))
        for stat in player_stats or []
    )
    award_text = f" and **+{MVP_FEEDERBUCKS_AWARD} Feederbucks**" if awarded else ""
    await channel.send(
        f"Match `{match_id}` update:\n"
        f"MVP: {mention} with **IMP {mvp['imp']}** ({position_text}){award_text}."
    )


def _recalculate_avg_imp_for_guild_sync(guild_id):
    totals = {}
    query = (
        db.collection("match_data")
        .document(str(guild_id))
        .collection("matches")
        .stream()
    )
    for doc in query:
        match_data = doc.to_dict() or {}
        if match_data.get("random_mode"):
            continue
        for stat in match_data.get("player_stats") or []:
            user_id = str(stat.get("user_id") or "")
            imp_value = stat.get("imp")
            if not user_id.isdigit() or imp_value is None:
                continue
            bucket = totals.setdefault(user_id, {"total": 0, "count": 0, "nickname": stat.get("nickname") or f"User {user_id}"})
            bucket["total"] += int(imp_value)
            bucket["count"] += 1
            if stat.get("nickname"):
                bucket["nickname"] = stat["nickname"]

    user_collection = db.collection("inhouse_mmr").document(str(guild_id)).collection("users")
    batch = db.batch()
    for user_doc in user_collection.stream():
        if user_doc.id not in totals:
            batch.set(
                user_doc.reference,
                {
                    "avg_imp": None,
                    "imp_match_count": 0,
                    "avg_imp_updated_at": firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )

    for user_id, payload in totals.items():
        avg_imp = round(payload["total"] / payload["count"], 2)
        batch.set(
            user_collection.document(str(user_id)),
            {
                "nickname": payload["nickname"],
                "avg_imp": avg_imp,
                "imp_match_count": payload["count"],
                "avg_imp_updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
    batch.commit()


async def recalculate_avg_imp_for_guild(guild_id):
    await asyncio.to_thread(_recalculate_avg_imp_for_guild_sync, guild_id)


async def recalculate_avg_imp_for_all_guilds():
    if not bot:
        return
    for guild in bot.guilds:
        try:
            await recalculate_avg_imp_for_guild(guild.id)
        except Exception as e:
            print(f"[match_imp] Failed to recalculate avg IMP for guild {guild.id}: {e}")


def get_top_avg_imp_players(guild_id, min_matches=IMP_MIN_MATCHES_FOR_AVERAGE):
    docs = (
        db.collection("inhouse_mmr")
        .document(str(guild_id))
        .collection("users")
        .stream()
    )
    results = []
    for doc in docs:
        data = doc.to_dict() or {}
        match_count = int(data.get("imp_match_count", 0) or 0)
        avg_imp = data.get("avg_imp")
        if avg_imp is None or match_count < int(min_matches):
            continue
        nickname = data.get("nickname", f"User {doc.id}")
        results.append((doc.id, nickname, float(avg_imp), match_count))
    results.sort(key=lambda item: item[2], reverse=True)
    return results


async def _run_imp_attempt(guild_id, match_id, *, channel_id=None, notify_on_success=False):
    key = (str(guild_id), str(match_id))
    try:
        match_ref = _match_ref(guild_id, match_id)
        snap = match_ref.get()
        if not snap.exists:
            return
        match_data = snap.to_dict() or {}
        if match_data.get("random_mode"):
            return
        if not (match_data.get("player_stats") or []):
            return
        if _has_complete_imp_data(match_data):
            player_stats = match_data.get("player_stats") or []
            mvp = _get_mvp(player_stats)
            update_payload = {
                "imp_positions_pending": False,
                "imp_positions_status": "complete",
                "imp_positions_completed_at": firestore.SERVER_TIMESTAMP,
            }
            if mvp:
                update_payload.update({
                    "mvp_user_id": str(mvp.get("user_id")) if mvp.get("user_id") else None,
                    "mvp_nickname": mvp.get("nickname"),
                    "mvp_imp": mvp.get("imp"),
                    "mvp_position": mvp.get("position"),
                })
                update_payload.update(_build_mvp_feederbucks_update(
                    guild_id,
                    match_id,
                    match_data,
                    mvp,
                    player_stats,
                ))
            match_ref.set(update_payload, merge=True)
            await recalculate_avg_imp_for_guild(guild_id)
            return

        current_attempt_count = int(match_data.get("imp_positions_attempt_count", 0) or 0)
        if current_attempt_count >= IMP_MAX_ATTEMPTS and not match_data.get("imp_positions_pending"):
            return

        attempt_number = current_attempt_count + 1 if not match_data.get("imp_positions_pending") else current_attempt_count + 1
        if attempt_number > IMP_MAX_ATTEMPTS:
            return

        processed_at = _to_utc_datetime(match_data.get("processed_at"))
        started_at = processed_at if attempt_number == 1 and processed_at else datetime.now(timezone.utc)
        stored_channel_id = channel_id or match_data.get("imp_notification_channel_id")
        should_notify = bool(match_data.get("imp_notify_pending", False) or notify_on_success)

        match_ref.set(
            {
                "player_stats": _ensure_imp_fields(match_data.get("player_stats") or []),
                "imp_positions_pending": True,
                "imp_positions_status": "pending",
                "imp_positions_attempt_count": attempt_number,
                "imp_positions_first_attempt_at": match_data.get("imp_positions_first_attempt_at") or started_at,
                "imp_positions_last_attempt_at": started_at,
                "imp_notification_channel_id": str(stored_channel_id) if stored_channel_id else None,
                "imp_notify_pending": should_notify,
            },
            merge=True,
        )

        max_checks = max(1, IMP_RETRY_WINDOW_SECONDS // IMP_RETRY_INTERVAL_SECONDS) + 1
        imp_players = None
        for check_index in range(max_checks):
            imp_players = await asyncio.to_thread(fetch_match_imp_data, match_id)
            if imp_players:
                break
            if check_index < max_checks - 1:
                await asyncio.sleep(IMP_RETRY_INTERVAL_SECONDS)

        refreshed_snap = match_ref.get()
        refreshed_data = refreshed_snap.to_dict() or {}
        current_player_stats = refreshed_data.get("player_stats") or match_data.get("player_stats") or []

        if imp_players:
            merged_stats = _merge_imp_player_stats(current_player_stats, imp_players)
            mvp = _get_mvp(merged_stats)
            update_payload = {
                "player_stats": merged_stats,
                "imp_positions_pending": False,
                "imp_positions_status": "complete",
                "imp_positions_completed_at": firestore.SERVER_TIMESTAMP,
                "imp_positions_next_attempt_at": None,
                "imp_notify_pending": False,
            }
            if mvp:
                update_payload.update({
                    "mvp_user_id": str(mvp.get("user_id")) if mvp.get("user_id") else None,
                    "mvp_nickname": mvp.get("nickname"),
                    "mvp_imp": mvp.get("imp"),
                    "mvp_position": mvp.get("position"),
                })
                update_payload.update(_build_mvp_feederbucks_update(
                    guild_id,
                    match_id,
                    refreshed_data,
                    mvp,
                    merged_stats,
                ))
            match_ref.set(update_payload, merge=True)
            await recalculate_avg_imp_for_guild(guild_id)
            if should_notify and stored_channel_id:
                await _announce_mvp(
                    guild_id,
                    match_id,
                    stored_channel_id,
                    refreshed_data.get("winning_team") or match_data.get("winning_team"),
                    merged_stats,
                )
            return

        failed_stats = _null_imp_player_stats(current_player_stats)
        if attempt_number >= IMP_MAX_ATTEMPTS:
            match_ref.set(
                {
                    "player_stats": failed_stats,
                    "imp_positions_pending": False,
                    "imp_positions_status": "failed",
                    "imp_positions_next_attempt_at": None,
                    "imp_notify_pending": False,
                },
                merge=True,
            )
            return

        match_ref.set(
            {
                "player_stats": failed_stats,
                "imp_positions_pending": True,
                "imp_positions_status": "pending",
                "imp_positions_next_attempt_at": _next_attempt_at(started_at),
            },
            merge=True,
        )
    except Exception as e:
        print(f"[match_imp] Failed IMP enrichment for guild={guild_id} match={match_id}: {e}")
    finally:
        _imp_enrichment_tasks.pop(key, None)


def schedule_match_imp_enrichment(guild_id, match_id, *, channel_id=None, notify_on_success=False):
    key = (str(guild_id), str(match_id))
    existing = _imp_enrichment_tasks.get(key)
    if existing and not existing.done():
        return False
    _imp_enrichment_tasks[key] = asyncio.create_task(
        _run_imp_attempt(
            guild_id,
            match_id,
            channel_id=channel_id,
            notify_on_success=notify_on_success,
        )
    )
    return True


def _is_due_for_imp_retry(match_data):
    if match_data.get("random_mode"):
        return False
    if not (match_data.get("player_stats") or []):
        return False
    if _has_complete_imp_data(match_data):
        return False
    status = match_data.get("imp_positions_status")
    attempt_count = int(match_data.get("imp_positions_attempt_count", 0) or 0)
    if status == "failed" or attempt_count >= IMP_MAX_ATTEMPTS:
        return False
    next_attempt_at = _to_utc_datetime(match_data.get("imp_positions_next_attempt_at"))
    if attempt_count == 0:
        return True
    if next_attempt_at is not None:
        return datetime.now(timezone.utc) >= next_attempt_at
    legacy_next_attempt_date = match_data.get("imp_positions_next_attempt_date")
    if legacy_next_attempt_date:
        return str(legacy_next_attempt_date) <= date.today().isoformat()
    return True


async def schedule_due_imp_enrichments():
    if not bot:
        return 0
    scheduled = 0
    for guild in bot.guilds:
        try:
            docs = (
                db.collection("match_data")
                .document(str(guild.id))
                .collection("matches")
                .stream()
            )
            for doc in docs:
                match_data = doc.to_dict() or {}
                if not _is_due_for_imp_retry(match_data):
                    continue
                if schedule_match_imp_enrichment(
                    guild.id,
                    match_data.get("match_id") or doc.id,
                    channel_id=match_data.get("imp_notification_channel_id"),
                    notify_on_success=bool(match_data.get("imp_notify_pending", False)),
                ):
                    scheduled += 1
        except Exception as e:
            print(f"[match_imp] Failed to scan guild {guild.id} for pending IMP retries: {e}")
    print(f"[match_imp] Scheduled {scheduled} pending IMP enrichment task(s).")
    return scheduled
