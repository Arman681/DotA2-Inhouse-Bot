import discord
import firebase_setup  # ensures Firebase is initialized before anything else
from firebase_admin import firestore

db = firestore.client()

async def get_inhouse_mmr(bot, guild_id, user_id):
    guild = bot.get_guild(int(guild_id))
    # Default values
    mmr = 2500
    nickname = "Unknown"
    # If we can't see the guild, just return defaults / stored value
    if not guild:
        doc_ref = db.collection("inhouse_mmr").document(str(guild_id))\
                    .collection("users").document(str(user_id))
        doc = doc_ref.get()
        if doc.exists:
            return doc.to_dict().get("mmr", 2500)
        # Initialize doc for completeness
        doc_ref.set({"mmr": mmr, "nickname": nickname})
        return mmr
    doc_ref = db.collection("inhouse_mmr").document(str(guild_id))\
                .collection("users").document(str(user_id))
    doc = doc_ref.get()
    if doc.exists:
        data = doc.to_dict()
        mmr = data.get("mmr", 2500)
        nickname = data.get("nickname", "Unknown")
    else:
        # Initialize new user
        doc_ref.set({"mmr": mmr, "nickname": nickname})
    # Try updating nickname from Discord (cache first, then fetch)
    try:
        member = guild.get_member(int(user_id)) or await guild.fetch_member(int(user_id))
        if member:
            new_nickname = member.display_name
            # Only write if changed
            if new_nickname != nickname:
                doc_ref.set({"nickname": new_nickname}, merge=True)
    except discord.NotFound:
        print(f"[WARN] No Discord user found for ID {user_id} in guild {guild_id}")
    except Exception as e:
        print(f"[ERROR] Unexpected error fetching member: {e}")
    return mmr

def set_inhouse_mmr(guild_id, user_id, nickname, mmr):
    data = {
        "nickname": nickname,
        "mmr": mmr,
        }
    doc = db.collection("inhouse_mmr").document(str(guild_id)).collection("users").document(str(user_id))
    doc.set(data, merge=True)    

async def adjust_mmr(bot, winner_ids, loser_ids, guild_id, gain=50, loss=50, doubled_user_ids=None):
    guild = bot.get_guild(int(guild_id))
    batch = db.batch()
    doubled_user_ids = {str(uid) for uid in (doubled_user_ids or [])}
    async def stage(uid, base_delta: int):
        current = await get_inhouse_mmr(bot, guild_id, uid)
        nickname = "Unknown"
        member = guild.get_member(int(uid)) if guild else None
        if member:
            nickname = member.display_name
        else:
            try:
                if guild:
                    member = await guild.fetch_member(int(uid))
                    nickname = member.display_name
            except discord.NotFound:
                print(f"[WARN] No Discord user found for Steam ID {uid} in guild {guild_id}")
            except Exception as e:
                print(f"[ERROR] Unexpected error fetching member {uid}: {e}")
        actual_delta = base_delta * 2 if str(uid) in doubled_user_ids else base_delta
        new_mmr = current + actual_delta
        ref = db.collection("inhouse_mmr").document(str(guild_id)).collection("users").document(str(uid))
        batch.set(ref, {"nickname": nickname, "mmr": new_mmr}, merge=True)
        if actual_delta > 0:
            print(f"[MMR+] ✅ {nickname} ({uid}) gained {actual_delta} MMR (now {new_mmr})")
        elif actual_delta < 0:
            print(f"[MMR–] ❌ {nickname} ({uid}) lost {abs(actual_delta)} MMR (now {new_mmr})")
    for uid in winner_ids:
        await stage(uid, gain)
    for uid in loser_ids:
        await stage(uid, -loss)
    batch.commit()

def get_top_players(guild_id, limit=None):
    query = db.collection("inhouse_mmr").document(str(guild_id)) \
             .collection("users").order_by("mmr", direction=firestore.Query.DESCENDING)
    if isinstance(limit, int) and limit > 0:
        query = query.limit(limit)
    docs = query.stream()
    results = []
    for doc in docs:
        data = doc.to_dict() or {}
        uid = doc.id
        nickname = data.get("nickname", f"User {uid}")
        mmr = data.get("mmr", 1000)
        results.append((uid, nickname, mmr))
    return results
