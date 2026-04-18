import bot.storage.firebase_setup  # ensures Firebase is initialized before anything else
from firebase_admin import firestore

db = firestore.client()


def _ledger_collection(guild_id):
    return (
        db.collection("match_data")
        .document(str(guild_id))
        .collection("matches")
    )


def _ledger_ref(guild_id, match_id):
    return _ledger_collection(guild_id).document(str(match_id))


def _legacy_processed_collection(guild_id):
    return (
        db.collection("processed_matches")
        .document(str(guild_id))
        .collection("matches")
    )


def log_match_ledger(
    guild_id,
    match_id,
    *,
    processed_at=None,
    league_id=None,
    source,
    winning_team=None,
    random_mode=False,
    processed_by=None,
    mmr_changes=None,
    bet_results=None,
    player_stats=None,
):
    payload = {
        "guild_id": str(guild_id),
        "match_id": str(match_id),
        "league_id": str(league_id) if league_id is not None else None,
        "source": source,
        "winning_team": winning_team,
        "random_mode": bool(random_mode),
        "processed_by": processed_by,
        "processed_at": processed_at if processed_at is not None else firestore.SERVER_TIMESTAMP,
        "mmr_changes": list(mmr_changes or []),
        "bet_results": list(bet_results or []),
        "player_stats": list(player_stats or []),
    }
    _ledger_ref(guild_id, match_id).set(payload, merge=True)


def get_recent_match_ledgers(guild_id, limit=5):
    query = (
        _ledger_collection(guild_id)
        .order_by("processed_at", direction=firestore.Query.DESCENDING)
        .limit(max(1, int(limit)))
    )
    results = []
    for doc in query.stream():
        data = doc.to_dict() or {}
        if "match_id" not in data:
            data["match_id"] = doc.id
        results.append(data)
    return results


def get_all_match_ledgers(guild_id):
    query = _ledger_collection(guild_id).order_by(
        "processed_at", direction=firestore.Query.DESCENDING
    )
    results = []
    for doc in query.stream():
        data = doc.to_dict() or {}
        if "match_id" not in data:
            data["match_id"] = doc.id
        results.append(data)
    return results


def get_match_data(guild_id, match_id):
    snap = _ledger_ref(guild_id, match_id).get()
    if not snap.exists:
        return None
    data = snap.to_dict() or {}
    if "match_id" not in data:
        data["match_id"] = str(match_id)
    return data


def is_match_data_logged(guild_id, match_id):
    return _ledger_ref(guild_id, match_id).get().exists


def _enrich_player_stats_for_guild(
    guild_id,
    raw_player_stats,
    *,
    get_discord_id_from_steam_id_fn=None,
    bot_instance=None,
):
    results = []
    guild = None
    if bot_instance is not None:
        try:
            guild = bot_instance.get_guild(int(guild_id))
        except Exception:
            guild = None
    for stat in raw_player_stats or []:
        steam_id = str(stat.get("steam_id", ""))
        discord_id = None
        if steam_id and get_discord_id_from_steam_id_fn is not None:
            try:
                discord_id = get_discord_id_from_steam_id_fn(steam_id)
            except Exception:
                discord_id = None
        member = guild.get_member(int(discord_id)) if guild and discord_id else None
        nickname = member.display_name if member else (str(discord_id) if discord_id else steam_id)
        enriched = dict(stat)
        enriched["steam_id"] = steam_id
        enriched["user_id"] = str(discord_id) if discord_id else None
        enriched["nickname"] = nickname
        results.append(enriched)
    return results


def migrate_processed_matches_to_match_data(
    fetch_match_result_fn=None,
    get_discord_id_from_steam_id_fn=None,
    bot_instance=None,
    guild_ids=None,
):
    migrated = 0
    enriched = 0
    skipped = 0
    errors = 0
    discovered_guild_ids = set()
    if guild_ids:
        discovered_guild_ids.update(str(gid) for gid in guild_ids if gid is not None)
    else:
        discovered_guild_ids.update(doc.id for doc in db.collection("guild_specific_info").stream())
        discovered_guild_ids.update(doc.id for doc in db.collection("processed_matches").stream())

    for guild_id in discovered_guild_ids:
        try:
            legacy_matches = _legacy_processed_collection(guild_id).stream()
            for legacy_doc in legacy_matches:
                legacy_data = legacy_doc.to_dict() or {}
                match_id = legacy_data.get("match_id") or legacy_doc.id
                target_ref = _ledger_ref(guild_id, match_id)
                target_snap = target_ref.get()
                target_data = target_snap.to_dict() if target_snap.exists else None
                player_stats = []
                if fetch_match_result_fn is not None:
                    fetched = fetch_match_result_fn(match_id)
                    if fetched:
                        player_stats = _enrich_player_stats_for_guild(
                            guild_id,
                            fetched.get("player_stats", []),
                            get_discord_id_from_steam_id_fn=get_discord_id_from_steam_id_fn,
                            bot_instance=bot_instance,
                        )
                if target_snap.exists:
                    if target_data and target_data.get("player_stats"):
                        skipped += 1
                        continue
                    if not player_stats:
                        skipped += 1
                        continue
                    target_ref.set(
                        {
                            "player_stats": player_stats,
                            "migrated_from_processed_matches": True,
                        },
                        merge=True,
                    )
                    enriched += 1
                    continue
                if target_ref.get().exists:
                    skipped += 1
                    continue
                payload = {
                    "guild_id": str(guild_id),
                    "match_id": str(match_id),
                    "league_id": legacy_data.get("league_id"),
                    "source": legacy_data.get("source"),
                    "winning_team": legacy_data.get("winning_team"),
                    "random_mode": bool(legacy_data.get("random_mode", False)),
                    "processed_by": legacy_data.get("processed_by"),
                    "processed_at": legacy_data.get("processed_at", firestore.SERVER_TIMESTAMP),
                    "processors": list(legacy_data.get("processors") or []),
                    "player_count": legacy_data.get("player_count"),
                    "bet_results": [],
                    "mmr_changes": [],
                    "player_stats": player_stats,
                    "migrated_from_processed_matches": True,
                }
                target_ref.set(payload, merge=True)
                migrated += 1
        except Exception as e:
            print(f"[match_data migration] Failed for guild {guild_id}: {e}")
            errors += 1
    print(
        f"[match_data migration] migrated={migrated} enriched={enriched} skipped={skipped} errors={errors}"
    )
    return {"migrated": migrated, "enriched": enriched, "skipped": skipped, "errors": errors}
