import bot.storage.firebase_setup  # ensures Firebase is initialized before anything else
from firebase_admin import firestore

db = firestore.client()

def _match_ref(guild_id, match_id):
    return (
        db.collection("processed_matches")
        .document(str(guild_id))
        .collection("matches")
        .document(str(match_id))
    )

def get_bound_league_id(guild_id):
    doc = db.collection("guild_specific_info").document(str(guild_id)).get()
    if not doc.exists:
        return None
    return doc.to_dict().get("league_id", {}).get("bound_league_id")

def get_processed_match(guild_id, match_id):
    snap = _match_ref(guild_id, match_id).get()
    if not snap.exists:
        return None
    return snap.to_dict() or {}

def is_match_processed(guild_id, match_id):
    return _match_ref(guild_id, match_id).get().exists

def log_processed_match(
    guild_id,
    match_id,
    *,
    league_id=None,
    source,
    processors=None,
    winning_team=None,
    random_mode=False,
    processed_by=None,
    player_count=None,
):
    payload = {
        "guild_id": str(guild_id),
        "match_id": str(match_id),
        "league_id": str(league_id) if league_id is not None else None,
        "source": source,
        "processors": list(processors or []),
        "winning_team": winning_team,
        "random_mode": bool(random_mode),
        "processed_by": processed_by,
        "player_count": player_count,
        "processed_at": firestore.SERVER_TIMESTAMP,
    }
    _match_ref(guild_id, match_id).set(payload, merge=True)
