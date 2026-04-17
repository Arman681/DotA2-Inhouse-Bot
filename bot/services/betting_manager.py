import bot.storage.firebase_setup  # ensures Firebase is initialized before anything else
from firebase_admin import firestore
from datetime import datetime
import re

db = firestore.client()

DD_TOKEN_COST = 1000

# ====================================
# 🔹 WALLET FUNCTIONS
# ====================================

def get_balance(guild_id, user_id, nickname=None):
    ref = db.collection("wallets").document(str(guild_id)) \
            .collection("users").document(str(user_id))
    snap = ref.get()
    if snap.exists:
        return snap.to_dict().get("balance", 1000)
    # Auto-seed
    payload = {"balance": 1000}
    if nickname:
        payload["nickname"] = nickname
    ref.set(payload, merge=True)
    return 1000

def update_balance(guild_id, user_id, delta, nickname=None):
    ref = db.collection("wallets").document(str(guild_id)) \
           .collection("users").document(str(user_id))
    snap = ref.get()
    if snap.exists and isinstance(snap.to_dict(), dict):
        current = snap.to_dict().get("balance", 1000)
    else:
        # Seed brand-new wallets at 1000 to match get_balance()
        current = 1000
    new_balance = int(current) + int(delta)
    payload = {"balance": new_balance}
    if nickname:
        payload["nickname"] = nickname
    ref.set(payload, merge=True)

# ====================================
# 🔹 DOUBLE DOWN TOKEN FUNCTIONS
# ====================================

def get_dd_token_balance(guild_id, user_id, nickname=None):
    ref = db.collection("dd_tokens").document(str(guild_id)) \
            .collection("users").document(str(user_id))
    snap = ref.get()
    if snap.exists:
        return snap.to_dict().get("count", 0)
    payload = {"count": 0}
    if nickname:
        payload["nickname"] = nickname
    ref.set(payload, merge=True)
    return 0

def update_dd_token_balance(guild_id, user_id, delta, nickname=None):
    ref = db.collection("dd_tokens").document(str(guild_id)) \
            .collection("users").document(str(user_id))
    snap = ref.get()
    if snap.exists and isinstance(snap.to_dict(), dict):
        current = snap.to_dict().get("count", 0)
    else:
        current = 0
    new_count = current + int(delta)
    if new_count < 0:
        new_count = 0
    payload = {"count": new_count}
    if nickname:
        payload["nickname"] = nickname
    ref.set(payload, merge=True)

def has_active_double_down(guild_id, user_id):
    ref = db.collection("double_downs").document(str(guild_id)) \
            .collection("users").document(str(user_id))
    return ref.get().exists

def activate_double_down(guild_id, user_id, nickname=None):
    ref = db.collection("double_downs").document(str(guild_id)) \
            .collection("users").document(str(user_id))
    payload = {
        "user_id": str(user_id),
        "timestamp": firestore.SERVER_TIMESTAMP
    }
    if nickname:
        payload["nickname"] = nickname
    ref.set(payload, merge=True)

def get_active_double_down_users(guild_id):
    docs = db.collection("double_downs").document(str(guild_id)) \
             .collection("users").stream()
    return [doc.id for doc in docs]

def clear_active_double_downs(guild_id):
    docs = db.collection("double_downs").document(str(guild_id)) \
             .collection("users").stream()
    batch = db.batch()
    for doc in docs:
        batch.delete(doc.reference)
    batch.commit()
    print(f"[CLEAR] ✅ Deleted all active double downs for guild {guild_id}")

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
    entries = list(db.collection("bets").document(str(guild_id)).collection("entries").stream())
    print(f"[RESOLVE_BETS] Resolving {len(entries)} bets for guild: {guild_id}")
    batch = db.batch()
    logs = [] # collect messages to send after commit: (outcome, user_id, amount, team)
    for doc in entries:
        data = doc.to_dict()
        user_id = data.get("user_id")
        nickname = data.get("nickname", "Unknown")
        team = data.get("team")
        amount = data.get("amount", 0)
        if not user_id or not team:
            print(f"[WARN] Missing data in doc {doc.id}")
            batch.delete(doc.reference)
            continue
        else:
            if team == winning_team:
                wallet_ref = db.collection("wallets").document(str(guild_id)).collection("users").document(str(user_id))
                # stake + winnings in one atomic add, no read
                batch.set(wallet_ref, {"balance": firestore.Increment(amount * 2), "nickname": nickname}, merge=True)
                logs.append(("win", user_id, amount, team, nickname))
            else:
                logs.append(("lose", user_id, amount, team, nickname))
            # clear the bet row either way
            batch.delete(doc.reference)
    # Commit all changes at once
    batch.commit()
    # Send logs to a designated channel or log them
    for outcome, user_id, amount, team, nickname in logs:
        if outcome == "win":
            print(f"[RESOLVE_BETS] ✅ {nickname} ({user_id}) won {amount} on {team}")
        else:
            print(f"[RESOLVE_BETS] ❌ {nickname} ({user_id}) lost {amount} on {team}")

# ====================================
# 🔹 CLEANUP FUNCTIONS
# ====================================

def clear_guild_bets(guild_id):
    entries_ref = db.collection("bets").document(str(guild_id)).collection("entries").stream()
    batch = db.batch()
    for entry in entries_ref:
        batch.delete(entry.reference)
    batch.commit()
    print(f"[CLEAR] ✅ Deleted all bets for guild {guild_id}")

def clear_all_bets(bot):
    for guild in bot.guilds:
        guild_id = str(guild.id)
        entries = db.collection("bets").document(guild_id).collection("entries").stream()
        for entry in entries:
            db.collection("bets").document(guild_id).collection("entries").document(entry.id).delete()
            print(f"[CLEAR] Deleted entry {entry.id} from guild {guild_id}")
    print("[INIT] 🧹 Cleared ALL bets from Firestore on startup.")
