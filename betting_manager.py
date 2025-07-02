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
    # Sanitize nickname to be Firestore-safe
    sanitized_nick = re.sub(r'[^\w\-]', '_', nickname.lower())
    entry_ref = db.collection("bets").document(match_key).collection("entries").document(sanitized_nick)
    # Check if this user already has a bet placed
    existing_bet_doc = entry_ref.get()
    previous_amount = 0
    if existing_bet_doc.exists:
        existing_bet = existing_bet_doc.to_dict()
        previous_amount = existing_bet.get("amount", 0)
        update_balance(user_id, previous_amount)  # Refund the previous bet
    # Now check if user has enough balance for the new amount
    if get_balance(user_id) < amount:
        return False
    # Deduct the new amount
    update_balance(user_id, -amount)
    # Save or update the bet
    entry_ref.set({
        "nickname": nickname,
        "user_id": user_id,
        "team": team,
        "amount": amount,
        "timestamp": firestore.SERVER_TIMESTAMP
    })
    return True

def resolve_bets(match_key, winning_team):
    entries = list(db.collection("bets").document(match_key).collection("entries").stream())
    print(f"[RESOLVE_BETS] Checking {len(entries)} bet entries for match_key: {match_key}")
    for doc in entries:
        data = doc.to_dict()
        print(f"[DEBUG] Fetched data: {data}")
        user_id = data.get("user_id")
        nickname = data.get("nickname", "Unknown")
        team = data.get("team")
        if not user_id or not team:
            print(f"[WARN] Missing data for doc {doc.id} -> user_id: {user_id}, team: {team}")
            continue
        if team == winning_team:
            update_balance(user_id, data["amount"] * 2)
            print(f"[RESOLVE_BETS] {nickname} ({user_id}) won {data['amount'] * 2} coins on {winning_team}")
        else:
            print(f"[RESOLVE_BETS] ❌ {nickname} ({user_id}) lost {data['amount']} coins on {team}")

def clear_guild_bets(ctx):
    def sanitize_name(name):
        return re.sub(r'\W+', '_', name.lower())
    match_key = f"{sanitize_name(ctx.guild.name)}_{ctx.guild.id}"
    # Delete all entries
    entries_ref = db.collection("bets").document(match_key).collection("entries").stream()
    for entry in entries_ref:
        db.collection("bets").document(match_key).collection("entries").document(entry.id).delete()
    # Delete match document if empty
    remaining = list(db.collection("bets").document(match_key).collection("entries").stream())
    if not remaining:
        db.collection("bets").document(match_key).delete()
        print(f"[CLEAR] ✅ Deleted match document for guild: {match_key}")
    else:
        print(f"[CLEAR] ❌ Some entries still exist in {match_key}")

def clear_all_bets(bot):
    def sanitize_name(name):
        return re.sub(r'\W+', '_', name.lower())
    for guild in bot.guilds:
        match_key = f"{sanitize_name(guild.name)}_{guild.id}"
        print(f"[DEBUG] 🔍 Looking for bets under: {match_key}")
        entries_ref = db.collection("bets").document(match_key).collection("entries").stream()
        entries = list(entries_ref)
        print(f"[DEBUG] 🧾 Found {len(entries)} entries in: {match_key}")
        # Delete each entry
        for entry in entries:
            db.collection("bets").document(match_key).collection("entries").document(entry.id).delete()
            print(f"[CLEAR] 🗑️ Deleted entry: {entry.id} from match: {match_key}")
    print("[INIT] 🧹 Cleared ALL bets from Firestore on startup.")