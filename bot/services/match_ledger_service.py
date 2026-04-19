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
