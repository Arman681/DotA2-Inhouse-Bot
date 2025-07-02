import firebase_setup  # ensures Firebase is initialized before anything else
from firebase_admin import firestore
from datetime import datetime
import re

db = firestore.client()

def get_balance(user_id):
    doc = db.collection("wallets").document(str(user_id)).get()
    return doc.to_dict().get("balance", 1000) if doc.exists else 1000

def update_balance(user_id, amount):
    current = get_balance(user_id)
    db.collection("wallets").document(str(user_id)).set({"balance": current + amount}, merge=True)

def place_bet(user_id, team, amount, match_key, nickname):
    if get_balance(user_id) < amount:
        return False
    # Sanitize nickname to be Firestore-safe
    sanitized_nick = re.sub(r'[^\w\-]', '_', nickname.lower())
    entry_ref = db.collection("bets").document(match_key).collection("entries").document(sanitized_nick)
    entry_ref.set({
        "nickname": nickname,
        "user_id": user_id,
        "team": team,
        "amount": amount,
        "timestamp": firestore.SERVER_TIMESTAMP
    })
    update_balance(user_id, -amount)
    return True

def resolve_bets(match_key, winning_team):
    entries = list(db.collection("bets").document(match_key).collection("entries").stream())
    for doc in entries:
        data = doc.to_dict()
        user_id = data.get("user_id")  # use user_id from the data, not doc.id
        nickname = data.get("nickname", "Unknown")  # Fallback in case it's missing
        if data["team"] == winning_team:
            update_balance(user_id, data["amount"] * 2)
            print(f"[RESOLVE_BETS] {nickname} ({user_id}) won {data['amount'] * 2} coins on {winning_team}")
        else:
            print(f"[RESOLVE_BETS] ❌ {nickname} ({user_id}) lost {data['amount']} coins on {data['team']}")
    # Delete all bets after resolution
    bet_ref = db.collection("bets").document(match_key)
    for doc in entries:
        bet_ref.collection("entries").document(doc.id).delete()

def clear_all_bets():
    bets_ref = db.collection("bets").stream()
    for doc in bets_ref:
        match_key = doc.id
        entries_ref = db.collection("bets").document(match_key).collection("entries").stream()
        for entry in entries_ref:
            db.collection("bets").document(match_key).collection("entries").document(entry.id).delete()
        db.collection("bets").document(match_key).delete()
    print("[INIT] 🔄 Cleared all bets from Firestore on startup.")