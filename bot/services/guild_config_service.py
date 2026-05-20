from datetime import datetime, timezone

from bot.state.runtime_state import (
    ALLOWED_CAPTAIN_POLICIES,
    captain_policy_by_guild,
    captain_policy_threshold_by_guild,
    prefix_cache,
)

db = None
firestore = None


def configure_guild_config(*, db_client, firestore_module):
    global db, firestore
    db = db_client
    firestore = firestore_module


def save_player_config(user_id, data):
    doc_ref = db.collection("players").document(str(user_id))
    doc_ref.set(data)


def load_player_config(user_id):
    doc = db.collection("players").document(str(user_id)).get()
    return doc.to_dict() if doc.exists else None


def save_guild_prefix(guild_id, prefix, server_name=None, set_by=None):
    data = {
        "prefix": prefix,
        "prefix_set_by": set_by,
        "prefix_timestamp": firestore.SERVER_TIMESTAMP,
    }
    doc_ref = db.collection("guild_specific_info").document(str(guild_id))
    doc_ref.set({"prefix": data}, merge=True)
    prefix_cache[guild_id] = prefix


def load_guild_prefix(guild_id):
    if guild_id in prefix_cache:
        return prefix_cache[guild_id]
    doc = db.collection("guild_specific_info").document(str(guild_id)).get()
    if doc.exists:
        data = doc.to_dict()
        prefix = data.get("prefix", {}).get("prefix", "!")
        prefix_cache[guild_id] = prefix
        return prefix
    return "!"


def save_lobby_password_for_guild(guild_id, password, server_name=None, set_by=None):
    data = {
        "password": password,
        "password_set_by": set_by,
        "password_timestamp": firestore.SERVER_TIMESTAMP,
        "server_name": server_name,
    }
    doc_ref = db.collection("guild_specific_info").document(str(guild_id))
    doc_ref.set({"password": data}, merge=True)


def load_lobby_password_for_guild(guild_id):
    doc = db.collection("guild_specific_info").document(str(guild_id)).get()
    if doc.exists:
        data = doc.to_dict()
        return data.get("password", {}).get("password", "penguin")
    return "penguin"


def save_inhouse_mode_for_guild(guild_id, mode, server_name=None, set_by=None):
    data = {
        "mode": mode,
        "mode_set_by": str(set_by),
        "mode_timestamp": firestore.SERVER_TIMESTAMP,
        "server_name": server_name,
    }
    doc_ref = db.collection("guild_specific_info").document(str(guild_id))
    doc_ref.set({"inhouse_mode": data}, merge=True)


def load_inhouse_mode_for_guild(guild_id):
    doc = db.collection("guild_specific_info").document(str(guild_id)).get()
    if doc.exists:
        return doc.to_dict().get("inhouse_mode", {}).get("mode", "regular")
    return "regular"


def save_league_guild_mapping(guild_id: int, league_id: int, server_name=None, bound_by=None):
    data = {
        "bound_league_id": str(league_id),
        "league_id_bound_by": str(bound_by),
        "league_bind_timestamp": firestore.SERVER_TIMESTAMP,
        "server_name": server_name,
    }
    doc_ref = db.collection("guild_specific_info").document(str(guild_id))
    doc_ref.set({"league_id": data}, merge=True)


def save_lobby_message_id(guild_id, message_id):
    data = {
        "lobby_message_id": message_id
    }
    doc_ref = db.collection("guild_specific_info").document(str(guild_id))
    doc_ref.set({"lobby_message_id": data}, merge=True)


def load_lobby_message_id(guild_id):
    doc = db.collection("guild_specific_info").document(str(guild_id)).get()
    if doc.exists:
        return doc.to_dict().get("lobby_message_id", {}).get("lobby_message_id", 0)
    return None


def save_lobby_players(guild_id, players):
    formatted = [{"id": uid, "name": name, "mmr": mmr} for uid, name, mmr in players]
    doc_ref = db.collection("guild_specific_info").document(str(guild_id))
    doc_ref.set({"lobby_players": formatted}, merge=True)


def load_lobby_players(guild_id):
    doc = db.collection("guild_specific_info").document(str(guild_id)).get()
    if doc.exists:
        raw = doc.to_dict().get("lobby_players", [])
        return [(p["id"], p["name"], p["mmr"]) for p in raw if "id" in p and "name" in p and "mmr" in p]
    return []


def save_preferred_roles_setting(guild_id, enabled, set_by=None):
    data = {
        "preferred_roles_enabled": enabled,
        "preferred_roles_set_by": str(set_by),
        "preferred_roles_timestamp": firestore.SERVER_TIMESTAMP
    }
    doc_ref = db.collection("guild_specific_info").document(str(guild_id))
    doc_ref.set({"preferred_roles_setting": data}, merge=True)


def load_preferred_roles_setting(guild_id):
    doc = db.collection("guild_specific_info").document(str(guild_id)).get()
    if doc.exists:
        return doc.to_dict().get("preferred_roles_setting", {}).get("preferred_roles_enabled", True)
    return True


def _separated_doc_ref(guild_id):
    return db.collection("separated").document(str(guild_id))


def _separated_pair_key(user_id_a, user_id_b):
    ids = sorted([str(user_id_a), str(user_id_b)])
    return f"{ids[0]}_{ids[1]}"


def save_separated_pair(guild_id, user_id_a, user_id_b, *, guild_name=None, set_by=None, names=None):
    key = _separated_pair_key(user_id_a, user_id_b)
    doc_ref = _separated_doc_ref(guild_id)
    doc = doc_ref.get()
    existing_pairs = {}
    if doc.exists:
        existing_pairs = (doc.to_dict() or {}).get("pairs", {}) or {}
    if key in existing_pairs:
        return False, existing_pairs[key]

    ids = sorted([str(user_id_a), str(user_id_b)])
    entry = {
        "user_ids": ids,
        "names": {str(uid): str(name) for uid, name in (names or {}).items()},
        "created_by": str(set_by) if set_by is not None else "Unknown",
        "created_at": firestore.SERVER_TIMESTAMP,
    }
    updated_pairs = dict(existing_pairs)
    updated_pairs[key] = entry
    payload = {
        "guild_id": str(guild_id),
        "guild_name": guild_name,
        "updated_at": firestore.SERVER_TIMESTAMP,
        "pairs": updated_pairs,
    }
    doc_ref.set(payload, merge=True)
    return True, entry


def delete_separated_pair(guild_id, user_id_a, user_id_b):
    key = _separated_pair_key(user_id_a, user_id_b)
    doc_ref = _separated_doc_ref(guild_id)
    doc = doc_ref.get()
    if not doc.exists:
        return False, None
    data = doc.to_dict() or {}
    existing_pairs = data.get("pairs", {}) or {}
    if key not in existing_pairs:
        return False, None

    removed = existing_pairs.pop(key)
    doc_ref.set(
        {
            "updated_at": firestore.SERVER_TIMESTAMP,
            "pairs": existing_pairs,
        },
        merge=True,
    )
    return True, removed


def get_separated_pairs(guild_id):
    doc = _separated_doc_ref(guild_id).get()
    if not doc.exists:
        return []
    pairs = (doc.to_dict() or {}).get("pairs", {}) or {}
    results = []
    for key, entry in pairs.items():
        if not isinstance(entry, dict):
            continue
        user_ids = [str(uid) for uid in entry.get("user_ids", []) if uid is not None]
        if len(user_ids) != 2:
            continue
        results.append({
            "key": key,
            "user_ids": sorted(user_ids),
            "names": entry.get("names", {}) or {},
            "created_at": entry.get("created_at"),
            "created_by": entry.get("created_by"),
        })
    return results


def get_captain_policy(guild_id: int) -> tuple[str, int | None]:
    if guild_id in captain_policy_by_guild:
        return captain_policy_by_guild[guild_id], captain_policy_threshold_by_guild.get(guild_id)
    doc = db.collection("guild_specific_info").document(str(guild_id)).get()
    if doc.exists:
        data = doc.to_dict() or {}
        nested = data.get("captain_policy", {})
        pol = nested.get("captain_policy", "min_diff")
        thr = nested.get("captain_policy_threshold")
    else:
        pol, thr = "min_diff", None
    captain_policy_by_guild[guild_id] = pol
    if thr is not None:
        captain_policy_threshold_by_guild[guild_id] = int(thr)
    else:
        captain_policy_threshold_by_guild.pop(guild_id, None)
    return pol, thr


def set_captain_policy(guild_id: int, policy: str, threshold: int | None = None, set_by: str | None = None) -> None:
    if policy not in ALLOWED_CAPTAIN_POLICIES:
        raise ValueError(f"Invalid policy: {policy}")
    captain_policy_by_guild[guild_id] = policy
    if threshold is not None:
        captain_policy_threshold_by_guild[guild_id] = int(threshold)
    elif policy != "top2_if_close":
        captain_policy_threshold_by_guild.pop(guild_id, None)
    data = {
        "captain_policy": policy,
        "captain_policy_set_by": set_by,
        "captain_policy_timestamp": firestore.SERVER_TIMESTAMP,
    }
    if policy == "top2_if_close":
        data["captain_policy_threshold"] = int(threshold or 150)
    doc_ref = db.collection("guild_specific_info").document(str(guild_id))
    doc_ref.set({"captain_policy": data}, merge=True)


def log_store_purchase(guild_id, user_id, item_key, cost, item_name, details=None):
    payload = {
        "guild_id": str(guild_id),
        "user_id": str(user_id),
        "item_key": item_key,
        "item_name": item_name,
        "cost": int(cost),
        "timestamp": firestore.SERVER_TIMESTAMP,
        "timestamp_utc": datetime.now(timezone.utc),
    }
    if details:
        payload.update(details)
    db.collection("store_purchases").document(str(guild_id)).collection("entries").add(payload)
