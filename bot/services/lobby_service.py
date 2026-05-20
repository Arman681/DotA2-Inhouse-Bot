import itertools
import random
from concurrent.futures import ThreadPoolExecutor

import discord

from bot.services.guild_config_service import (
    get_separated_pairs,
    load_preferred_roles_setting,
    save_lobby_players,
)
from bot.services.immortal_draft import Candidate, ImmortalDraftSession
from bot.state.runtime_state import (
    MMR_ROLE_OVERRULE_THRESHOLD,
    ROLE_FIT_WEIGHT,
    captain_draft_state,
    immortal_draft_running,
    inhouse_mode,
    lobby_channel_ids,
    lobby_players,
    match_wait_tasks,
    original_teams,
    rocket_lock,
    roll_count,
    team_rolls,
    valid_team_combos,
)


db = None
update_lobby_embed = None


def configure_lobby_service(*, db_client, update_lobby_embed_fn):
    global db, update_lobby_embed
    db = db_client
    update_lobby_embed = update_lobby_embed_fn


def assign_roles_with_preferences(team, preference_map=None, mmr_map=None):
    assigned = {}
    unassigned_players = list(team)
    if preference_map is None or mmr_map is None:
        preference_map = {}
        mmr_map = {}
        for player in team:
            uid = str(player[0])
            mmr_map[uid] = player[2]
            doc = db.collection("players").document(uid).get()
            data = doc.to_dict() if doc.exists else None
            preference_map[uid] = data.get("preferred_roles", [1, 2, 3, 4, 5]) if (data and isinstance(data.get("preferred_roles"), list)) else [1, 2, 3, 4, 5]
    for role in range(1, 6):
        best_candidate = None
        best_rank = 999
        best_mmr = -1
        for player in unassigned_players:
            uid = str(player[0])
            prefs = preference_map.get(uid, [])
            mmr = mmr_map.get(uid, 0)
            if role in prefs:
                rank = prefs.index(role)
                if (rank < best_rank) or (rank == best_rank and mmr > best_mmr):
                    best_candidate = player
                    best_rank = rank
                    best_mmr = mmr
        for player in unassigned_players:
            uid = str(player[0])
            mmr = mmr_map[uid]
            prefs = preference_map.get(uid, [])
            if role in prefs and best_candidate:
                best_uid = str(best_candidate[0])
                if uid != best_uid and mmr - mmr_map[best_uid] >= MMR_ROLE_OVERRULE_THRESHOLD and prefs.index(role) <= 2:
                    best_candidate = player
                    break
        if best_candidate:
            assigned[role] = best_candidate
            unassigned_players.remove(best_candidate)
    return assigned


def get_all_captain_pairs(players):
    captain_eligible = [p for p in players if not is_placeholder_player(p[0])]
    sorted_players = sorted(players, key=lambda p: p[2])
    pairs = []
    for i in range(len(captain_eligible)):
        for j in range(i + 1, len(captain_eligible)):
            p1 = captain_eligible[i]
            p2 = captain_eligible[j]
            diff = abs(p1[2] - p2[2])
            pool = [p for p in sorted_players if p not in (p1, p2)]
            pairs.append(((p1, p2), pool, diff))
    pairs.sort(key=lambda x: x[2])
    return pairs


def choose_captain_pair_index(players, all_pairs, policy="min_diff", threshold=150):
    if not all_pairs:
        return 0
    if policy == "min_diff":
        return 0
    if policy == "top2_if_close":
        sorted_players = sorted(players, key=lambda x: x[2])
        top2 = (sorted_players[-2], sorted_players[-1])
        top2_ids = {top2[0][0], top2[1][0]}
        top2_diff = abs(top2[1][2] - top2[0][2])
        if top2_diff <= threshold:
            for i, (caps, _pool, _d) in enumerate(all_pairs):
                if {caps[0][0], caps[1][0]} == top2_ids:
                    return i
        return 0
    if policy == "simulate":
        def simulate_score(caps, pool):
            cap1, cap2 = caps
            totals = {"cap1": cap1[2], "cap2": cap2[2]}
            remaining = sorted(pool, key=lambda x: x[2])
            pick_order = [("cap1", 1), ("cap2", 2), ("cap1", 2), ("cap2", 2), ("cap1", 1)]
            for who, cnt in pick_order:
                for _ in range(cnt):
                    if not remaining:
                        break
                    pick = remaining.pop()
                    totals[who] += pick[2]
            return abs(totals["cap1"] - totals["cap2"])

        best_i, best_score = 0, None
        for i, (caps, pool, _d) in enumerate(all_pairs):
            score = simulate_score(caps, pool)
            if best_score is None or score < best_score:
                best_i, best_score = i, score
        return best_i
    return 0


def find_lobby_tuple(gid: int, user_id: int):
    for tup in lobby_players.get(gid, []):
        if tup[0] == user_id:
            return tup
    return None


def clear_manual_if_lobby_changed(gid: int) -> bool:
    state = captain_draft_state.get(gid)
    if not state or not state.get("manual"):
        return False
    try:
        captains, pool, _ = state["pairs"][state["index"]]
    except Exception:
        captain_draft_state.pop(gid, None)
        return True
    original_ids = {p[0] for p in captains} | {p[0] for p in pool}
    current_ids = {uid for uid, _, _ in lobby_players.get(gid, [])}
    if original_ids != current_ids:
        captain_draft_state.pop(gid, None)
        return True
    return False


def get_preferred_roles(player_id):
    doc = db.collection("players").document(str(player_id)).get()
    if doc.exists:
        data = doc.to_dict()
        if "preferred_roles" in data and isinstance(data["preferred_roles"], list):
            return data["preferred_roles"]
    return None


async def refresh_lobby_member_mmr(guild: discord.Guild, member: discord.Member, new_mmr=None):
    gid = guild.id
    if gid not in lobby_players:
        return
    for idx, (uid, name, old_mmr) in enumerate(list(lobby_players[gid])):
        if uid == member.id:
            mmr_val = new_mmr
            if mmr_val is None:
                snap = db.collection("players").document(str(member.id)).get()
                data = snap.to_dict() if snap.exists else {}
                mmr_val = data.get("mmr", old_mmr)
            lobby_players[gid][idx] = (uid, name, mmr_val)
            save_lobby_players(gid, lobby_players[gid])
            await update_lobby_embed(guild)
            break


async def start_immortal_draft(bot, guild: discord.Guild, channel: discord.TextChannel):
    gid = guild.id
    mode = inhouse_mode.get(gid)
    if mode != "immortal":
        await channel.send("This command only works after starting an **Immortal** lobby.")
        return
    players = lobby_players.get(gid, [])
    if len(players) != 10:
        await channel.send("Immortal Draft requires exactly **10** players in the lobby.")
        return
    state = captain_draft_state.get(gid)
    if not state or "pairs" not in state or "index" not in state:
        await channel.send("No captains found. Press 🚀 in the Immortal lobby first.")
        return
    try:
        captains, pool, _diff = state["pairs"][state["index"]]
    except Exception:
        await channel.send("Could not read captain pair. Try pressing 🚀 again.")
        return

    captains = list(captains)
    random.shuffle(captains)
    c1_id, _c1_name, _c1_mmr = captains[0]
    c2_id, _c2_name, _c2_mmr = captains[1]
    cap1 = guild.get_member(int(c1_id))
    cap2 = guild.get_member(int(c2_id))
    if not cap1 or not cap2:
        await channel.send("One or both captains are no longer in the server.")
        return
    await channel.send(f"Randomized player draft first pick: **{cap1.mention}** gets first pick!")

    candidates = []
    for uid, name, mmr in pool:
        if is_placeholder_player(uid):
            candidates.append(Candidate(player_id=str(uid), mmr=int(mmr), member=None, name=name))
        else:
            member = guild.get_member(int(uid))
            if member and not member.bot:
                candidates.append(Candidate(player_id=str(member.id), mmr=int(mmr), member=member, name=member.display_name))
    if len(candidates) != 8:
        await channel.send("Need **8 valid non-captain** players available for the draft.")
        return

    header = discord.Embed(
        title="Starting Immortal Draft",
        description=(
            f"Captains: {cap1.mention} vs {cap2.mention}\n"
            f"Draft order: **1-2-2-2-1**\n"
            f"Pick clock: **5s per pick** + **60s personal reserve** (cumulative)\n"
            f"Players are shown low->high by **actual MMR**."
        ),
        color=discord.Color.gold(),
    )
    header_message = await channel.send(embed=header)
    session = ImmortalDraftSession(
        bot=bot,
        guild=guild,
        channel=channel,
        cap1=cap1,
        cap2=cap2,
        cap1_mmr=int(_c1_mmr),
        cap2_mmr=int(_c2_mmr),
        candidates=candidates,
        header_message=header_message,
    )
    await session.start()
    if session.timer_task:
        session.timer_task.add_done_callback(lambda _t, guild_id=guild.id: immortal_draft_running.__setitem__(guild_id, False))
    return session


def cancel_match_wait(guild_id: int):
    task = match_wait_tasks.pop(guild_id, None)
    if task and not task.done():
        task.cancel()


def reset_team_state_for_guild(guild_id: int):
    team_rolls.pop(guild_id, None)
    original_teams.pop(guild_id, None)
    roll_count.pop(guild_id, None)
    valid_team_combos.pop(guild_id, None)
    captain_draft_state.pop(guild_id, None)


async def full_post_rocket_reset(guild_id, message=None):
    cancel_match_wait(guild_id)
    reset_team_state_for_guild(guild_id)
    rocket_lock[guild_id] = False
    if message:
        for reaction in message.reactions:
            if str(reaction.emoji) in ["🚀", "♻️", "⚔️", "🎯"]:
                try:
                    await message.clear_reaction(reaction.emoji)
                except Exception:
                    pass


def is_placeholder_player(uid) -> bool:
    return str(uid).startswith("placeholder:")


def format_lobby_player_mention(uid, name: str) -> str:
    return name if is_placeholder_player(uid) else f"<@{uid}>"


def get_lobby_channel_for_guild(guild):
    guild_id = guild.id
    channel_id = lobby_channel_ids.get(guild_id)
    if channel_id:
        channel = guild.get_channel(channel_id)
        if channel:
            return channel
    return None


def calculate_balanced_teams(players, guild_id, max_mmr_diff=100):
    preference_map = {}
    mmr_map = {}
    for uid, _, mmr in players:
        uid_str = str(uid)
        mmr_map[uid_str] = mmr
        doc = db.collection("players").document(uid_str).get()
        data = doc.to_dict() if doc.exists else None
        preferred = data.get("preferred_roles", [1, 2, 3, 4, 5]) if (data and isinstance(data.get("preferred_roles"), list)) else [1, 2, 3, 4, 5]
        preference_map[uid_str] = preferred
    use_roles = load_preferred_roles_setting(guild_id)
    active_player_ids = {str(uid) for uid, _, _ in players}
    separated_pairs = [
        tuple(pair["user_ids"])
        for pair in get_separated_pairs(guild_id)
        if all(str(uid) in active_player_ids for uid in pair.get("user_ids", []))
    ]
    combos_to_score = []
    for team1 in itertools.combinations(players, 5):
        team2 = tuple(p for p in players if p not in set(team1))
        team1_ids = {str(p[0]) for p in team1}
        team2_ids = {str(p[0]) for p in team2}
        if any(pair[0] in team1_ids and pair[1] in team1_ids for pair in separated_pairs):
            continue
        if any(pair[0] in team2_ids and pair[1] in team2_ids for pair in separated_pairs):
            continue
        mmr1 = sum(p[2] for p in team1) / 5
        mmr2 = sum(p[2] for p in team2) / 5
        mmr_diff = abs(mmr1 - mmr2)
        if mmr_diff > max_mmr_diff:
            continue
        combos_to_score.append((team1, team2, mmr_diff))
    print(
        f"[calculate_balanced_teams] Found {len(combos_to_score)} valid team combinations "
        f"(MMR diff <= {max_mmr_diff}, active separated pairs={len(separated_pairs)})"
    )
    if not combos_to_score:
        team_rolls[guild_id] = []
        return [], 0

    def score_combo(combo):
        team1, team2, mmr_diff = combo
        if use_roles:
            score1, roles1 = calculate_role_fit_score(team1, preference_map, mmr_map)
            score2, roles2 = calculate_role_fit_score(team2, preference_map, mmr_map)
        else:
            score1 = score2 = 0
            roles1 = roles2 = None
        total_score = (mmr_diff / 5) - ROLE_FIT_WEIGHT * (score1 + score2)
        return (total_score, team1, team2, score1, score2, roles1, roles2)

    with ThreadPoolExecutor() as executor:
        results = list(executor.map(score_combo, combos_to_score))

    results.sort(key=lambda x: x[0])
    top_teams = [(r[1], r[2], r[3], r[4], r[5], r[6]) for r in results[:5]]
    team_rolls[guild_id] = top_teams
    valid_team_combos[guild_id] = len(results)
    return top_teams, len(results)


def calculate_role_fit_score(team, preference_map=None, mmr_map=None):
    assignments = assign_roles_with_preferences(team, preference_map, mmr_map)
    total_score = 0
    for role, player in assignments.items():
        uid = str(player[0])
        preferences = preference_map.get(uid) if preference_map else get_preferred_roles(uid)
        if not preferences:
            continue
        try:
            preference_rank = preferences.index(role) + 1
        except ValueError:
            preference_rank = 6
        total_score += preference_rank
    return total_score, assignments
