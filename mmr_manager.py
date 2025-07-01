from firebase_admin import firestore

db = firestore.client()

def get_inhouse_mmr(user_id):
    doc = db.collection("inhouse_mmr").document(str(user_id)).get()
    return doc.to_dict().get("mmr", 1000) if doc.exists else 1000

def set_inhouse_mmr(user_id, mmr):
    db.collection("inhouse_mmr").document(str(user_id)).set({"mmr": mmr}, merge=True)

def adjust_mmr(winner_ids, loser_ids, gain=50, loss=50):
    for uid in winner_ids:
        current = get_inhouse_mmr(uid)
        set_inhouse_mmr(uid, current + gain)
    for uid in loser_ids:
        current = get_inhouse_mmr(uid)
        set_inhouse_mmr(uid, current - loss)