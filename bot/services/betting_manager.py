import bot.storage.firebase_setup  # ensures Firebase is initialized before anything else
from firebase_admin import firestore

db = firestore.client()

DD_TOKEN_COST = 1000


# ====================================
# Wallet Functions
# ====================================

def get_balance(guild_id, user_id, nickname=None):
    ref = db.collection("wallets").document(str(guild_id)) \
            .collection("users").document(str(user_id))
    snap = ref.get()
    if snap.exists:
        return snap.to_dict().get("balance", 1000)
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
        current = 1000
    new_balance = int(current) + int(delta)
    payload = {"balance": new_balance}
    if nickname:
        payload["nickname"] = nickname
    ref.set(payload, merge=True)

# ====================================
# Double Down Token Functions
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
    print(f"[CLEAR] Deleted all active double downs for guild {guild_id}")

# ====================================
# Betting Functions
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
    logs = []
    results = []
    for doc in entries:
        data = doc.to_dict()
        user_id = data.get("user_id")
        nickname = data.get("nickname", "Unknown")
        team = data.get("team")
        amount = int(data.get("amount", 0) or 0)
        if not user_id or not team:
            print(f"[WARN] Missing data in doc {doc.id}")
            batch.delete(doc.reference)
            continue
        if team == winning_team:
            wallet_ref = db.collection("wallets").document(str(guild_id)).collection("users").document(str(user_id))
            batch.set(wallet_ref, {"balance": firestore.Increment(amount * 2), "nickname": nickname}, merge=True)
            logs.append(("win", user_id, amount, team, nickname))
            results.append({
                "user_id": str(user_id),
                "nickname": nickname,
                "team": team,
                "amount": amount,
                "won": True,
                "net_delta": amount,
                "gross_payout": amount * 2,
            })
        else:
            logs.append(("lose", user_id, amount, team, nickname))
            results.append({
                "user_id": str(user_id),
                "nickname": nickname,
                "team": team,
                "amount": amount,
                "won": False,
                "net_delta": -amount,
                "gross_payout": 0,
            })
        batch.delete(doc.reference)
    batch.commit()
    for outcome, user_id, amount, team, nickname in logs:
        if outcome == "win":
            print(f"[RESOLVE_BETS] WIN {nickname} ({user_id}) won {amount} on {team}")
        else:
            print(f"[RESOLVE_BETS] LOSE {nickname} ({user_id}) lost {amount} on {team}")
    return results

# ====================================
# Cleanup Functions
# ====================================

def clear_guild_bets(guild_id):
    entries_ref = db.collection("bets").document(str(guild_id)).collection("entries").stream()
    batch = db.batch()
    for entry in entries_ref:
        batch.delete(entry.reference)
    batch.commit()
    print(f"[CLEAR] Deleted all bets for guild {guild_id}")

def clear_all_bets(bot):
    for guild in bot.guilds:
        guild_id = str(guild.id)
        entries = db.collection("bets").document(guild_id).collection("entries").stream()
        for entry in entries:
            db.collection("bets").document(guild_id).collection("entries").document(entry.id).delete()
            print(f"[CLEAR] Deleted entry {entry.id} from guild {guild_id}")
    print("[INIT] Cleared ALL bets from Firestore on startup.")