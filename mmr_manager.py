import discord
import firebase_setup  # ensures Firebase is initialized before anything else
from firebase_admin import firestore

db = firestore.client()

def get_inhouse_mmr(guild_id, user_id):
    doc_ref = db.collection("inhouse_mmr").document(str(guild_id)).collection("users").document(str(user_id))
    doc = doc_ref.get()

    if doc.exists:
        return doc.to_dict().get("mmr", 2500)
    else:
        # Initialize new user with 2500 MMR
        doc_ref.set({"mmr": 2500, "nickname": "Unknown"})
        return 2500

def set_inhouse_mmr(guild_id, user_id, nickname, mmr):
    data = {
        "nickname": nickname,
        "mmr": mmr,
        }
    doc = db.collection("inhouse_mmr").document(str(guild_id)).collection("users").document(str(user_id))
    doc.set(data, merge=True)    

async def adjust_mmr(bot, winner_ids, loser_ids, guild_id, gain=50, loss=50):
    guild = bot.get_guild(int(guild_id))

    for uid in winner_ids:
        current = get_inhouse_mmr(guild_id, uid)
        nickname = "Unknown"

        try:
            member = await guild.fetch_member(int(uid))
            nickname = member.display_name
        except discord.NotFound:
            print(f"[WARN] No Discord user found for Steam ID {uid} in guild {guild_id}")
        except Exception as e:
            print(f"[ERROR] Unexpected error fetching member {uid}: {e}")

        new_mmr = current + gain
        set_inhouse_mmr(guild_id, uid, nickname, new_mmr)
        print(f"[MMR+] ✅ {nickname} ({uid}) gained {gain} MMR (now {new_mmr})")

    for uid in loser_ids:
        current = get_inhouse_mmr(guild_id, uid)
        nickname = "Unknown"

        try:
            member = await guild.fetch_member(int(uid))
            nickname = member.display_name
        except discord.NotFound:
            print(f"[WARN] No Discord user found for Steam ID {uid} in guild {guild_id}")
        except Exception as e:
            print(f"[ERROR] Unexpected error fetching member {uid}: {e}")

        new_mmr = current - loss
        set_inhouse_mmr(guild_id, uid, nickname, new_mmr)
        print(f"[MMR–] ❌ {nickname} ({uid}) lost {loss} MMR (now {new_mmr})")

def get_top_players(guild_id, limit=10):
    docs = db.collection("inhouse_mmr").document(str(guild_id)) \
             .collection("users").order_by("mmr", direction=firestore.Query.DESCENDING).limit(limit).stream()
    return [(doc.id, doc.to_dict().get("mmr", 1000)) for doc in docs]