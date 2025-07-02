import firebase_setup  # ensures Firebase is initialized before anything else
from firebase_admin import firestore

db = firestore.client()

def get_inhouse_mmr(guild_id, user_id):
    doc = db.collection("inhouse_mmr").document(str(guild_id)) \
            .collection("users").document(str(user_id)).get()
    return doc.to_dict().get("mmr", 1000) if doc.exists else 1000

def set_inhouse_mmr(guild_id, user_id, nickname, mmr):
    db.collection("inhouse_mmr").document(str(guild_id)) \
      .collection("users").document(str(user_id)) \
      .set({"nickname": nickname,
            "mmr": mmr}, merge=True)

def adjust_mmr(winner_ids, loser_ids, guild_id, guild, gain=50, loss=50):
    for uid in winner_ids:
        current = get_inhouse_mmr(guild_id, uid)
        member = guild.get_member(int(uid))
        nickname = member.display_name if member else "Unknown"
        set_inhouse_mmr(guild_id, uid, nickname, current + gain)
    for uid in loser_ids:
        current = get_inhouse_mmr(guild_id, uid)
        member = guild.get_member(int(uid))
        nickname = member.display_name if member else "Unknown"
        set_inhouse_mmr(guild_id, uid, nickname, current - loss)

def get_top_players(guild_id, limit=10):
    docs = db.collection("inhouse_mmr").document(str(guild_id)) \
             .collection("users").order_by("mmr", direction=firestore.Query.DESCENDING).limit(limit).stream()
    return [(doc.id, doc.to_dict().get("mmr", 1000)) for doc in docs]