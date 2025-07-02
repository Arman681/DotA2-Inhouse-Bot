import firebase_setup  # ensures Firebase is initialized before anything else
from firebase_admin import firestore

db = firestore.client()

def get_inhouse_mmr(user_id):
    doc = db.collection("inhouse_mmr").document(str(user_id)).get()
    return doc.to_dict().get("mmr", 1000) if doc.exists else 1000

def set_inhouse_mmr(user_id, mmr, guild_id=None):
    data = {"mmr": mmr}
    if guild_id:
        data["guild_id"] = str(guild_id)
    db.collection("inhouse_mmr").document(str(user_id)).set(data, merge=True)

def adjust_mmr(winner_ids, loser_ids, gain=50, loss=50, guild_id=None):
    for uid in winner_ids:
        current = get_inhouse_mmr(uid)
        set_inhouse_mmr(uid, current + gain, guild_id=guild_id)
    for uid in loser_ids:
        current = get_inhouse_mmr(uid)
        set_inhouse_mmr(uid, current - loss, guild_id=guild_id)

def get_top_players(guild_id, limit=10):
    docs = db.collection("inhouse_mmr").where("guild_id", "==", str(guild_id)).order_by("mmr", direction=firestore.Query.DESCENDING).limit(limit).stream()
    return [(doc.id, doc.to_dict().get("mmr", 1000)) for doc in docs]