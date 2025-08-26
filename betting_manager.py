import firebase_setup  # ensures Firebase is initialized before anything else
from firebase_admin import firestore
from datetime import datetime
import re

db = firestore.client()

# ====================================
# 🔹 WALLET FUNCTIONS
# ====================================

def get_balance(guild_id, user_id):
    doc = db.collection("wallets").document(str(guild_id)) \
            .collection("users").document(str(user_id)).get()
    return doc.to_dict().get("balance", 1000) if doc.exists else 1000

def update_balance(guild_id, user_id, delta, nickname=None):
    data = {
        "balance": firestore.Increment(int(delta))
    }
    if nickname:
        data["nickname"] = nickname
    db.collection("wallets").document(str(guild_id)) \
      .collection("users").document(str(user_id)).set(data, merge=True)

# ====================================
# 🔹 BETTING FUNCTIONS
# ====================================

def place_bet(user_id, team, amount, delta, guild_id, nickname):
    entry_ref = db.collection("bets").document(str(guild_id)).collection("entries").document(str(user_id))
    update_balance(guild_id, user_id, -delta, nickname)
    entry_ref.set({
        "nickname": nickname,
        "user_id": str(user_id),
        "team": team,
        "amount": amount,
        "timestamp": firestore.SERVER_TIMESTAMP
    })
    return True

def resolve_bets(guild_id, winning_team):
    entries = db.collection("bets").document(str(guild_id)).collection("entries").stream()
    print(f"[RESOLVE_BETS] Resolving {sum(1 for _ in entries)} bets for guild: {guild_id}")
    entries = db.collection("bets").document(str(guild_id)).collection("entries").stream()
    for doc in entries:
        data = doc.to_dict()
        user_id = data.get("user_id")
        nickname = data.get("nickname", "Unknown")
        team = data.get("team")
        amount = data.get("amount", 0)
        if not user_id or not team:
            print(f"[WARN] Missing data in doc {doc.id}")
            continue
        if team == winning_team:
            update_balance(guild_id, user_id, amount * 2, nickname)
            print(f"[RESOLVE_BETS] ✅ {nickname} ({user_id}) won {amount * 2} on {winning_team}")
        else:
            print(f"[RESOLVE_BETS] ❌ {nickname} ({user_id}) lost {amount} on {team}")

# ====================================
# 🔹 CLEANUP FUNCTIONS
# ====================================

def clear_guild_bets(guild_id):
    entries_ref = db.collection("bets").document(str(guild_id)).collection("entries").stream()
    for entry in entries_ref:
        db.collection("bets").document(str(guild_id)).collection("entries").document(entry.id).delete()
    # Clean up document if empty
    remaining = list(db.collection("bets").document(str(guild_id)).collection("entries").stream())
    if not remaining:
        db.collection("bets").document(str(guild_id)).delete()
        print(f"[CLEAR] ✅ Deleted all bets for guild {guild_id}")
    else:
        print(f"[CLEAR] ❌ Some entries remain in guild {guild_id}")

def clear_all_bets(bot):
    for guild in bot.guilds:
        guild_id = str(guild.id)
        entries = db.collection("bets").document(guild_id).collection("entries").stream()
        for entry in entries:
            db.collection("bets").document(guild_id).collection("entries").document(entry.id).delete()
            print(f"[CLEAR] Deleted entry {entry.id} from guild {guild_id}")
    print("[INIT] 🧹 Cleared ALL bets from Firestore on startup.")