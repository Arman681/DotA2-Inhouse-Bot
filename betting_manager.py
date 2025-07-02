import firebase_setup  # ensures Firebase is initialized before anything else
from firebase_admin import firestore

db = firestore.client()

def get_balance(user_id):
    doc = db.collection("wallets").document(str(user_id)).get()
    return doc.to_dict().get("balance", 1000) if doc.exists else 1000

def update_balance(user_id, amount):
    current = get_balance(user_id)
    db.collection("wallets").document(str(user_id)).set({"balance": current + amount}, merge=True)

def place_bet(user_id, team, amount, match_key):
    if get_balance(user_id) < amount:
        return False
    db.collection("bets").document(match_key).collection("entries").document(str(user_id)).set({
        "team": team,
        "amount": amount
    })
    update_balance(user_id, -amount)
    return True

def resolve_bets(match_key, winning_team):
    entries = list(db.collection("bets").document(match_key).collection("entries").stream())
    for doc in entries:
        data = doc.to_dict()
        user_id = doc.id
        if data["team"] == winning_team:
            update_balance(user_id, data["amount"] * 2)
    # Delete all bets after resolution
    bet_ref = db.collection("bets").document(match_key)
    for doc in entries:
        bet_ref.collection("entries").document(doc.id).delete()
    print(f"[RESOLVE_BETS] User {user_id} won {data['amount'] * 2} coins on {winning_team}")