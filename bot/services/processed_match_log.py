import bot.storage.firebase_setup  # ensures Firebase is initialized before anything else
from firebase_admin import firestore
from bot.services.match_ledger_service import (
    get_match_data,
    is_match_data_logged,
    log_match_ledger,
)

db = firestore.client()

def get_bound_league_id(guild_id):
    doc = db.collection("guild_specific_info").document(str(guild_id)).get()
    if not doc.exists:
        return None
    return doc.to_dict().get("league_id", {}).get("bound_league_id")

def get_processed_match(guild_id, match_id):
    return get_match_data(guild_id, match_id)

def is_match_processed(guild_id, match_id):
    return is_match_data_logged(guild_id, match_id)

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
    log_match_ledger(
        guild_id,
        match_id,
        league_id=league_id,
        source=source,
        winning_team=winning_team,
        random_mode=random_mode,
        processed_by=processed_by,
        mmr_changes=None,
        bet_results=None,
        player_stats=None,
    )
    db.collection("match_data").document(str(guild_id)).collection("matches").document(str(match_id)).set(
        {
            "processors": list(processors or []),
            "player_count": player_count,
            "processed_at": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )
