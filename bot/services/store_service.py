from datetime import datetime, timedelta, timezone

import discord

from bot.services.betting_manager import DD_TOKEN_COST
from bot.services.guild_config_service import log_store_purchase as persist_store_purchase
from bot.state.runtime_state import (
    CUSTOM_STORE_ROLE_COLOR,
    STORE_ITEM_ALIASES,
    STORE_ITEM_CUSTOM_ROLE,
    STORE_ITEM_DD_TOKENS,
    STORE_ITEM_VIP_FEEDER,
    STORE_ROLE_DURATION_DAYS,
    VIP_FEEDER_ROLE_COLOR,
    VIP_FEEDER_ROLE_NAME,
)


db = None
firestore = None
bot = None


def configure_store_service(*, db_client, firestore_module, bot_instance):
    global db, firestore, bot
    db = db_client
    firestore = firestore_module
    bot = bot_instance


def get_store_catalog():
    return {
        STORE_ITEM_DD_TOKENS: {
            "key": STORE_ITEM_DD_TOKENS,
            "display_name": "Double-Down Tokens",
            "default_cost": DD_TOKEN_COST,
            "duration_days": None,
            "role_name": None,
        },
        STORE_ITEM_VIP_FEEDER: {
            "key": STORE_ITEM_VIP_FEEDER,
            "display_name": "VIP Feeder",
            "default_cost": 50000,
            "duration_days": STORE_ROLE_DURATION_DAYS,
            "role_name": VIP_FEEDER_ROLE_NAME,
        },
        STORE_ITEM_CUSTOM_ROLE: {
            "key": STORE_ITEM_CUSTOM_ROLE,
            "display_name": "Custom Role",
            "default_cost": 150000,
            "duration_days": STORE_ROLE_DURATION_DAYS,
            "role_name": None,
        },
    }


async def ensure_vip_feeder_role(guild):
    existing = discord.utils.get(guild.roles, name=VIP_FEEDER_ROLE_NAME)
    if existing:
        if existing.color != VIP_FEEDER_ROLE_COLOR or not existing.hoist:
            try:
                await existing.edit(
                    color=VIP_FEEDER_ROLE_COLOR,
                    hoist=True,
                    reason="Sync VIP Feeder store role color.",
                )
            except discord.Forbidden:
                print(f"[store] Missing permissions to update VIP Feeder role in guild {guild.id}")
            except Exception as e:
                print(f"[store] Failed to update VIP Feeder role in guild {guild.id}: {e}")
        await promote_store_role_display(guild, existing)
        return existing
    try:
        role = await guild.create_role(
            name=VIP_FEEDER_ROLE_NAME,
            color=VIP_FEEDER_ROLE_COLOR,
            hoist=True,
            reason="Create default VIP Feeder store role.",
        )
        await promote_store_role_display(guild, role)
        return role
    except discord.Forbidden:
        print(f"[store] Missing permissions to create VIP Feeder role in guild {guild.id}")
        return None
    except Exception as e:
        print(f"[store] Failed to create VIP Feeder role in guild {guild.id}: {e}")
        return None


async def reset_vip_feeder_role(guild):
    existing = discord.utils.get(guild.roles, name=VIP_FEEDER_ROLE_NAME)
    deleted_role_id = existing.id if existing else None
    if existing is not None:
        try:
            await existing.delete(reason="Global admin requested VIP Feeder role reset.")
        except discord.Forbidden:
            return False, "I couldn't delete the existing `VIP Feeder` role. Please make sure I have `Manage Roles`.", None, 0
        except Exception as e:
            return False, f"I couldn't delete the existing `VIP Feeder` role: `{e}`", None, 0

    role = await ensure_vip_feeder_role(guild)
    if role is None:
        return False, "I couldn't recreate the `VIP Feeder` role. Please make sure I have `Manage Roles`.", None, 0

    reassigned_count = 0
    docs = db.collection("store_role_entitlements").document(str(guild.id)).collection("entries").stream()
    for doc in docs:
        data = doc.to_dict() or {}
        if not data.get("active", False):
            continue
        if data.get("item_key") != STORE_ITEM_VIP_FEEDER:
            continue
        user_id = str(data.get("user_id"))
        member = guild.get_member(int(user_id)) if user_id.isdigit() else None
        if member is None:
            continue
        if role not in member.roles:
            try:
                await member.add_roles(role, reason="Restored VIP Feeder role after global admin reset.")
                reassigned_count += 1
            except discord.Forbidden:
                print(f"[store] Missing permissions to restore VIP Feeder role to {user_id} in guild {guild.id}")
            except Exception as e:
                print(f"[store] Failed to restore VIP Feeder role to {user_id} in guild {guild.id}: {e}")
        db.collection("store_role_entitlements").document(str(guild.id)).collection("entries").document(doc.id).set(
            {
                "role_id": role.id,
                "role_name": role.name,
                "status": "active",
            },
            merge=True,
        )

    if deleted_role_id is not None:
        print(f"[store] Reset VIP Feeder role in guild {guild.id}: {deleted_role_id} -> {role.id}")
    return True, None, role, reassigned_count


async def reset_custom_store_roles(guild):
    await cleanup_expired_store_roles_for_guild(guild)
    docs = db.collection("store_role_entitlements").document(str(guild.id)).collection("entries").stream()
    grouped_roles = {}
    for doc in docs:
        data = doc.to_dict() or {}
        if not data.get("active", False):
            continue
        if not bool(data.get("is_custom_role", False)):
            continue
        role_name = (data.get("custom_role_name") or data.get("role_name") or "").strip()
        if not role_name:
            continue
        role_group = grouped_roles.setdefault(
            role_name,
            {"entries": [], "role_ids": set(), "member_ids": set()},
        )
        role_group["entries"].append((doc.id, data))
        role_id = data.get("role_id")
        if role_id:
            role_group["role_ids"].add(int(role_id))
        user_id = str(data.get("user_id"))
        if user_id.isdigit():
            role_group["member_ids"].add(int(user_id))

    if not grouped_roles:
        return True, None, {"roles_refreshed": 0, "members_restored": 0, "entitlements_updated": 0}

    roles_refreshed = 0
    members_restored = 0
    entitlements_updated = 0

    for role_name, role_group in grouped_roles.items():
        existing_roles = {}
        for role_id in role_group["role_ids"]:
            role = guild.get_role(role_id)
            if role is not None:
                existing_roles[role.id] = role

        if not existing_roles:
            fallback_role = discord.utils.get(guild.roles, name=role_name)
            if fallback_role is not None and fallback_role.color == CUSTOM_STORE_ROLE_COLOR:
                existing_roles[fallback_role.id] = fallback_role

        target_member_ids = set(role_group["member_ids"])
        for existing_role in existing_roles.values():
            for member in existing_role.members:
                target_member_ids.add(member.id)

        for existing_role in existing_roles.values():
            try:
                await existing_role.delete(reason="Global admin requested custom role refresh.")
            except discord.Forbidden:
                return False, f"I couldn't delete the existing custom role `{role_name}`. Please make sure I have `Manage Roles`.", None
            except Exception as e:
                return False, f"I couldn't delete the existing custom role `{role_name}`: `{e}`", None

        try:
            new_role = await guild.create_role(
                name=role_name,
                color=CUSTOM_STORE_ROLE_COLOR,
                hoist=True,
                reason="Global admin requested custom role refresh.",
            )
        except discord.Forbidden:
            return False, f"I couldn't recreate the custom role `{role_name}`. Please make sure I have `Manage Roles`.", None
        except Exception as e:
            return False, f"I couldn't recreate the custom role `{role_name}`: `{e}`", None

        await promote_store_role_display(guild, new_role)

        for member_id in target_member_ids:
            member = guild.get_member(member_id)
            if member is None or new_role in member.roles:
                continue
            try:
                await member.add_roles(new_role, reason="Restored custom role after global admin refresh.")
                members_restored += 1
            except discord.Forbidden:
                print(f"[store] Missing permissions to restore custom role {new_role.id} to {member_id} in guild {guild.id}")
            except Exception as e:
                print(f"[store] Failed to restore custom role {new_role.id} to {member_id} in guild {guild.id}: {e}")

        for doc_id, _data in role_group["entries"]:
            db.collection("store_role_entitlements").document(str(guild.id)).collection("entries").document(doc_id).set(
                {
                    "role_id": new_role.id,
                    "role_name": role_name,
                    "custom_role_name": role_name,
                    "status": "active",
                },
                merge=True,
            )
            entitlements_updated += 1

        roles_refreshed += 1
        print(f"[store] Reset custom role `{role_name}` in guild {guild.id} -> {new_role.id}")

    return True, None, {
        "roles_refreshed": roles_refreshed,
        "members_restored": members_restored,
        "entitlements_updated": entitlements_updated,
    }


def get_store_item_info(item_key):
    return get_store_catalog().get(item_key)


def normalize_store_item_name(raw_name):
    if not raw_name:
        return None
    normalized = " ".join(str(raw_name).strip().lower().split())
    for item_key, aliases in STORE_ITEM_ALIASES.items():
        if normalized in aliases:
            return item_key
        item_info = get_store_item_info(item_key)
        if item_info and normalized == item_info["display_name"].lower():
            return item_key
    return None


def get_store_cost(guild_id, item_key):
    item_info = get_store_item_info(item_key)
    if not item_info:
        return None
    doc = db.collection("guild_specific_info").document(str(guild_id)).get()
    if doc.exists:
        overrides = doc.to_dict().get("store_cost_overrides", {})
        if isinstance(overrides, dict):
            override = overrides.get(item_key, {})
            if isinstance(override, dict):
                cost = override.get("cost")
                if isinstance(cost, int):
                    return cost
    return item_info["default_cost"]


def save_store_cost_override(guild_id, item_key, cost, server_name=None, set_by=None):
    item_info = get_store_item_info(item_key)
    if not item_info:
        raise ValueError(f"Unknown store item: {item_key}")
    data = {
        item_key: {
            "cost": int(cost),
            "display_name": item_info["display_name"],
            "default_cost": item_info["default_cost"],
            "server_name": server_name,
            "set_by": str(set_by) if set_by is not None else None,
            "timestamp": firestore.SERVER_TIMESTAMP,
        }
    }
    doc_ref = db.collection("guild_specific_info").document(str(guild_id))
    doc_ref.set({"store_cost_overrides": data}, merge=True)


def get_active_store_role_entry(guild_id, user_id, item_key):
    ref = db.collection("store_role_entitlements").document(str(guild_id)).collection("entries").document(f"{user_id}_{item_key}")
    snap = ref.get()
    if not snap.exists:
        return None
    data = snap.to_dict() or {}
    data["_doc_id"] = snap.id
    return data


def log_store_purchase(guild_id, user_id, item_key, cost, details=None):
    item_info = get_store_item_info(item_key) or {}
    persist_store_purchase(
        guild_id,
        user_id,
        item_key,
        cost,
        item_info.get("display_name", item_key),
        details=details,
    )


async def promote_store_role_display(guild, role):
    if role is None:
        return
    try:
        if not role.hoist:
            await role.edit(hoist=True, reason="Hoist store role in the member list.")
    except discord.Forbidden:
        print(f"[store] Missing permissions to hoist role {role.id} in guild {guild.id}")
        return
    except Exception as e:
        print(f"[store] Failed to hoist role {role.id} in guild {guild.id}: {e}")
        return
    bot_member = guild.me or guild.get_member(bot.user.id if bot and bot.user else 0)
    if bot_member is None:
        return
    target_position = max(1, bot_member.top_role.position - 1)
    if role.position >= target_position:
        return
    try:
        await role.edit(position=target_position, reason="Move store role near the top of the member list.")
    except discord.Forbidden:
        print(f"[store] Missing permissions to move role {role.id} in guild {guild.id}")
    except Exception as e:
        print(f"[store] Failed to move role {role.id} in guild {guild.id}: {e}")


async def expire_store_role_entry(guild, entry_id, data):
    user_id = str(data.get("user_id"))
    role_id = data.get("role_id")
    item_key = data.get("item_key")
    custom_role = bool(data.get("is_custom_role", False))
    ref = db.collection("store_role_entitlements").document(str(guild.id)).collection("entries").document(entry_id)
    member = guild.get_member(int(user_id)) if user_id.isdigit() else None
    role = guild.get_role(int(role_id)) if role_id else None
    if member and role:
        try:
            await member.remove_roles(role, reason="Store role expired after 7 days.")
        except discord.Forbidden:
            print(f"[store] Missing permissions to remove expired role {role_id} from {user_id} in guild {guild.id}")
        except Exception as e:
            print(f"[store] Failed to remove expired role {role_id} from {user_id} in guild {guild.id}: {e}")
    if custom_role and role:
        try:
            await role.delete(reason="Custom store role expired after 7 days.")
        except discord.Forbidden:
            print(f"[store] Missing permissions to delete expired custom role {role_id} in guild {guild.id}")
        except Exception as e:
            print(f"[store] Failed to delete expired custom role {role_id} in guild {guild.id}: {e}")
    ref.set(
        {
            "active": False,
            "status": "expired",
            "expired_at": firestore.SERVER_TIMESTAMP,
            "expired_at_utc": datetime.now(timezone.utc),
            "item_key": item_key,
        },
        merge=True,
    )


async def cleanup_expired_store_roles_for_guild(guild, *, only_user_id=None):
    now = datetime.now(timezone.utc)
    docs = db.collection("store_role_entitlements").document(str(guild.id)).collection("entries").stream()
    expired_count = 0
    for doc in docs:
        data = doc.to_dict() or {}
        if not data.get("active", False):
            continue
        if only_user_id is not None and str(data.get("user_id")) != str(only_user_id):
            continue
        expires_at = data.get("expires_at")
        if expires_at is None:
            purchased_at = data.get("purchased_at_utc") or data.get("timestamp_utc")
            if purchased_at is None:
                continue
            expires_at = purchased_at + timedelta(days=STORE_ROLE_DURATION_DAYS)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if now >= expires_at:
            await expire_store_role_entry(guild, doc.id, data)
            expired_count += 1
    return expired_count


async def cleanup_expired_store_roles():
    total_expired = 0
    for guild in bot.guilds:
        total_expired += await cleanup_expired_store_roles_for_guild(guild)
    if total_expired:
        print(f"[store] Expired {total_expired} timed store role(s).")
    return total_expired


async def purchase_store_role(member, item_key, custom_role_name=None, quantity=1):
    guild = member.guild
    quantity = int(quantity)
    if quantity <= 0:
        return False, "Amount must be greater than 0."
    duration_days = STORE_ROLE_DURATION_DAYS * quantity
    await cleanup_expired_store_roles_for_guild(guild, only_user_id=member.id)
    existing_entry = get_active_store_role_entry(guild.id, member.id, item_key)
    role = None
    role_name = None
    is_custom_role = False
    if existing_entry and existing_entry.get("active", False):
        expires_at = existing_entry.get("expires_at")
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        base_time = expires_at if expires_at and expires_at > now else now
        new_expires_at = base_time + timedelta(days=duration_days)
        role_id = existing_entry.get("role_id")
        role = guild.get_role(int(role_id)) if role_id else None
        is_custom_role = bool(existing_entry.get("is_custom_role", False))
        role_name = existing_entry.get("role_name")
        if role is None and item_key == STORE_ITEM_VIP_FEEDER:
            role = await ensure_vip_feeder_role(guild)
            role_name = role.name if role else VIP_FEEDER_ROLE_NAME
        if role is None and item_key == STORE_ITEM_CUSTOM_ROLE:
            role_name = (existing_entry.get("custom_role_name") or custom_role_name or role_name or "").strip()
            if not role_name:
                return False, "I couldn't find your existing custom role name to extend this purchase."
            try:
                role = await guild.create_role(
                    name=role_name,
                    color=CUSTOM_STORE_ROLE_COLOR,
                    reason=f"Recreated custom store role for {member}.",
                )
            except discord.Forbidden:
                return False, "I can't recreate your custom role because I'm missing the `Manage Roles` permission."
            except Exception as e:
                return False, f"I couldn't recreate your custom role: `{e}`"
            await promote_store_role_display(guild, role)
        if role is None:
            return False, "I couldn't locate the role tied to your active purchase."
        await promote_store_role_display(guild, role)
        if role not in member.roles:
            try:
                await member.add_roles(role, reason=f"Extended store role: {role_name}")
            except discord.Forbidden:
                return False, "I couldn't re-assign your role while extending it. Please make sure my highest role is above the store roles."
            except Exception as e:
                return False, f"I couldn't re-assign your role while extending it: `{e}`"
        db.collection("store_role_entitlements").document(str(guild.id)).collection("entries").document(f"{member.id}_{item_key}").set(
            {
                "role_id": role.id,
                "role_name": role_name,
                "custom_role_name": role_name if is_custom_role else None,
                "expires_at": new_expires_at,
                "last_extended_at": firestore.SERVER_TIMESTAMP,
                "last_extended_at_utc": datetime.now(timezone.utc),
                "last_extension_quantity": quantity,
                "duration_days": duration_days,
                "active": True,
                "status": "active",
            },
            merge=True,
        )
        return True, {
            "role": role,
            "role_name": role_name,
            "expires_at": new_expires_at,
            "is_custom_role": is_custom_role,
            "extended": True,
            "quantity": quantity,
            "duration_days": duration_days,
        }

    if item_key == STORE_ITEM_VIP_FEEDER:
        role = await ensure_vip_feeder_role(guild)
        if role is None:
            return False, "I couldn't create or locate the `VIP Feeder` role. Please make sure I have `Manage Roles`."
        role_name = role.name
        is_custom_role = False
    elif item_key == STORE_ITEM_CUSTOM_ROLE:
        custom_role_name = (custom_role_name or "").strip()
        if not custom_role_name:
            return False, "Please include a name for your custom role."
        if len(custom_role_name) > 100:
            return False, "Custom role names must be 100 characters or fewer."
        try:
            role = await guild.create_role(
                name=custom_role_name,
                color=CUSTOM_STORE_ROLE_COLOR,
                hoist=True,
                reason=f"Purchased custom store role for {member}.",
            )
        except discord.Forbidden:
            return False, "I can't create your custom role because I'm missing the `Manage Roles` permission."
        except Exception as e:
            return False, f"I couldn't create your custom role: `{e}`"
        role_name = custom_role_name
        is_custom_role = True
    else:
        return False, "That store item is not a timed role."

    await promote_store_role_display(guild, role)
    try:
        await member.add_roles(role, reason=f"Purchased store role: {role_name}")
    except discord.Forbidden:
        if is_custom_role:
            try:
                await role.delete(reason="Cleanup after failed assignment.")
            except Exception:
                pass
        return False, "I couldn't assign the role. Please make sure my highest role is above the store roles."
    except Exception as e:
        if is_custom_role:
            try:
                await role.delete(reason="Cleanup after failed assignment.")
            except Exception:
                pass
        return False, f"I couldn't assign the role: `{e}`"

    purchased_at = datetime.now(timezone.utc)
    expires_at = purchased_at + timedelta(days=duration_days)
    entry_payload = {
        "guild_id": str(guild.id),
        "user_id": str(member.id),
        "item_key": item_key,
        "item_name": get_store_item_info(item_key)["display_name"],
        "role_id": role.id,
        "role_name": role_name,
        "is_custom_role": is_custom_role,
        "custom_role_name": role_name if is_custom_role else None,
        "active": True,
        "status": "active",
        "purchased_at": firestore.SERVER_TIMESTAMP,
        "purchased_at_utc": purchased_at,
        "expires_at": expires_at,
        "purchase_quantity": quantity,
        "duration_days": duration_days,
    }
    db.collection("store_role_entitlements").document(str(guild.id)).collection("entries").document(f"{member.id}_{item_key}").set(
        entry_payload,
        merge=True,
    )
    return True, {
        "role": role,
        "role_name": role_name,
        "expires_at": expires_at,
        "is_custom_role": is_custom_role,
        "extended": False,
        "quantity": quantity,
        "duration_days": duration_days,
    }
