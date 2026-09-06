import discord

from bot.services.betting_manager import BETTING_MODE_POOL, get_betting_summary
from bot.services.guild_config_service import (
    get_captain_policy,
    load_inhouse_mode_for_guild,
    load_lobby_password_for_guild,
    load_mmr_spread_setting,
    load_preferred_roles_setting,
)
from bot.services.lobby_service import calculate_team_mmr_std_dev
from bot.state.runtime_state import (
    IMMORTAL_MAX_ROLLS,
    MAX_ROLLS,
    inhouse_mode,
    lobby_message,
    lobby_players,
    roll_count,
    valid_team_combos,
)


bot = None
hero_id_map = {}
get_display_name = None
assign_roles_with_preferences = None


def _format_amount(amount):
    try:
        return f"{int(amount):,}"
    except (TypeError, ValueError):
        return "0"


def _format_multiplier(value):
    if value is None:
        return "--x"
    try:
        return f"{float(value):.2f}x"
    except (TypeError, ValueError):
        return "--x"


def _scoreboard_team_score(scoreboard, team):
    team_data = scoreboard.get(team) or {}
    score = team_data.get("score")
    if score is not None:
        try:
            return int(score)
        except (TypeError, ValueError):
            pass
    return sum(int(player.get("kills", 0) or 0) for player in team_data.get("players", []) or [])


def _format_live_betting_columns(guild_id, match_id):
    summary = get_betting_summary(guild_id, match_id)
    if not summary:
        return None, None
    markets = summary.get("markets") or []
    main_market = next((m for m in markets if m.get("id") == "match"), None)
    open_count = sum(1 for m in markets if m.get("status") == "open")
    locked_count = sum(1 for m in markets if m.get("status") == "locked")
    left_lines = [
        f"Mode: `{summary.get('mode_label', 'Classic')}`",
        f"Markets: `{open_count}` open, `{locked_count}` locked",
    ]
    right_lines = []
    if main_market:
        pools = main_market.get("pools") or {}
        if summary.get("mode") == BETTING_MODE_POOL:
            left_lines.append(f"Prize Pool: `{_format_amount(main_market.get('total_pool'))}`")
            right_lines.extend([
                f"Seeded: `{_format_amount(main_market.get('seed'))}`",
                (
                    f"Radiant: `{_format_amount(pools.get('radiant'))}` "
                    f"| `{_format_multiplier(main_market.get('radiant_multiplier'))}`"
                ),
                (
                    f"Dire: `{_format_amount(pools.get('dire'))}` "
                    f"| `{_format_multiplier(main_market.get('dire_multiplier'))}`"
                ),
            ])
        else:
            left_lines.append("Match Winner: `2.00x` classic payout")
            right_lines.extend([
                f"Radiant: `{_format_amount(pools.get('radiant'))}`",
                f"Dire: `{_format_amount(pools.get('dire'))}`",
            ])
    return "\n".join(left_lines), "\n".join(right_lines) if right_lines else "\u200b"


def configure_embed_service(*, bot_instance, hero_id_map_cache, get_display_name_fn, assign_roles_with_preferences_fn):
    global bot, hero_id_map, get_display_name, assign_roles_with_preferences
    bot = bot_instance
    hero_id_map = hero_id_map_cache
    get_display_name = get_display_name_fn
    assign_roles_with_preferences = assign_roles_with_preferences_fn


def build_lobby_embed(guild, mode=None):
    guild_id = guild.id
    if mode is None:
        mode = inhouse_mode.get(guild_id)
        if mode is None:
            mode = load_inhouse_mode_for_guild(guild.id)
            inhouse_mode[guild_id] = mode
    embed = discord.Embed(title="DotA2 Inhouse Lobby", color=discord.Color.purple())
    embed.add_field(name=f"**Mode** `{mode.capitalize()}`", value=f"\n({len(lobby_players[guild.id])}/10)", inline=True)
    if mode != "immortal":
        spread_enabled = load_mmr_spread_setting(guild.id)
        embed.add_field(name="MMR Spread", value="✅ Enabled" if spread_enabled else "❌ Disabled", inline=True)
        roles_enabled = load_preferred_roles_setting(guild.id)
        embed.add_field(name="Preferred Roles", value="✅ Enabled" if roles_enabled else "❌ Disabled", inline=True)
    else:
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)
    for _, name, mmr in lobby_players.get(guild_id, []):
        embed.add_field(name=name, value=str(mmr), inline=True)
    password = load_lobby_password_for_guild(guild.id)
    embed.add_field(name="**Password**", value=password, inline=False)
    return embed


async def update_lobby_embed(guild):
    guild_id = guild.id
    if guild_id not in lobby_players or guild_id not in lobby_message:
        return
    embed = build_lobby_embed(guild)
    message = lobby_message[guild_id]
    try:
        await message.edit(embed=embed)
    except discord.NotFound:
        print(f"[update_lobby_embed] Lobby message was deleted before it could be edited for guild {guild_id}")
        return
    except Exception as e:
        print(f"[update_lobby_embed] Failed to edit lobby message for guild {guild_id}: {e}")
        return
    if len(lobby_players[guild_id]) == 10 and not any(str(r.emoji) == "🚀" for r in message.reactions):
        try:
            await message.add_reaction("🚀")
        except discord.NotFound:
            print(f"[update_lobby_embed] Lobby message was deleted before 🚀 could be added for guild {guild_id}")
        except Exception as e:
            print(f"[update_lobby_embed] Failed to add 🚀 reaction for guild {guild_id}: {e}")


async def update_all_lobbies():
    for guild in bot.guilds:
        await update_lobby_embed(guild)


def build_team_embed(team1, team2, score1, score2, roles1=None, roles2=None, guild=None, preference_map=None, mmr_map=None):
    roles_enabled = load_preferred_roles_setting(guild.id)
    spread_enabled = load_mmr_spread_setting(guild.id)

    def matchmaking_mmr(player):
        if mmr_map is None:
            return player[2]
        return mmr_map.get(str(player[0]), player[2])

    avg1 = sum(matchmaking_mmr(player) for player in team1) / 5
    avg2 = sum(matchmaking_mmr(player) for player in team2) / 5
    description = f"(10/10): T1: {int(avg1)}, T2: {int(avg2)}, Roll #{roll_count.get(guild.id, 1)}/{MAX_ROLLS}"
    if spread_enabled:
        std_dev1 = calculate_team_mmr_std_dev(team1, mmr_map)
        std_dev2 = calculate_team_mmr_std_dev(team2, mmr_map)
        description += (
            f"\n📊 MMR standard deviation — T1: {std_dev1:.1f}, "
            f"T2: {std_dev2:.1f}, Difference: {abs(std_dev1 - std_dev2):.1f}"
        )
    if guild.id in valid_team_combos:
        description += f"\n🧮 Valid team combinations found: {valid_team_combos[guild.id]}"
    embed = discord.Embed(title="DotA2 Inhouse Lobby", description=description, color=discord.Color.gold())
    team1_sorted = sorted(team1, key=lambda x: x[2], reverse=True)
    team2_sorted = sorted(team2, key=lambda x: x[2], reverse=True)
    if roles_enabled:
        if roles1 is None:
            print("[BUILD_TEAM_EMBED] Assigning roles for team 1")
            roles1 = assign_roles_with_preferences(team1_sorted, preference_map, mmr_map)
        else:
            print("[BUILD_TEAM_EMBED] Using passed roles for team 1")
        if roles2 is None:
            print("[BUILD_TEAM_EMBED] Assigning roles for team 2")
            roles2 = assign_roles_with_preferences(team2_sorted, preference_map, mmr_map)
        else:
            print("[BUILD_TEAM_EMBED] Using passed roles for team 2")

        def format_player_list(assignments):
            return ", ".join(f"{p[1]} ({p[2]}) [Pos {role}]" for role, p in sorted(assignments.items()))

        team1_desc = format_player_list(roles1) + f"\nRole Fit Score: {score1}"
        team2_desc = format_player_list(roles2) + f"\nRole Fit Score: {score2}"
    else:
        def format_player_list(team):
            return ", ".join(f"{p[1]} ({p[2]})" for p in team)

        team1_desc = format_player_list(team1_sorted)
        team2_desc = format_player_list(team2_sorted)
    password = load_lobby_password_for_guild(guild.id)
    embed.add_field(name="Team One", value=team1_desc, inline=False)
    embed.add_field(name="Team Two", value=team2_desc, inline=False)
    embed.add_field(name="**Password**", value=password, inline=False)
    return embed


def build_immortal_embed(captains, pool, guild, reroll_count):
    c1, c2 = captains
    policy, threshold = get_captain_policy(guild.id)
    policy_display = policy
    if policy == "top2_if_close":
        policy_display = f"{policy} (threshold: {threshold or 200})"
    embed = discord.Embed(
        title="🛡️ Immortal Draft Inhouse Lobby",
        description=(
            f"Captains: {c1[1]} ({c1[2]}) vs {c2[1]} ({c2[2]})\n"
            f"Roll #{reroll_count}/{IMMORTAL_MAX_ROLLS}\n"
            f"Captain Pair Policy: **{policy_display}**"
        ),
        color=discord.Color.orange(),
    )
    embed.add_field(name="Captain 1", value=f"{c1[1]} ({c1[2]})", inline=True)
    embed.add_field(name="Captain 2", value=f"{c2[1]} ({c2[2]})", inline=True)
    embed.add_field(
        name="🧩 Draft Pool",
        value=", ".join(f"{p[1]} ({p[2]})" for p in sorted(pool, key=lambda x: x[2])),
        inline=False,
    )
    password = load_lobby_password_for_guild(guild.id)
    embed.add_field(name="**Password**", value=password, inline=False)
    return embed


async def format_live_match_embed(match, guild):
    scoreboard = match.get("scoreboard")
    if scoreboard:
        try:
            duration = int(scoreboard.get("duration", 0) or 0)
        except (TypeError, ValueError):
            duration = 0
        minutes = duration // 60
        seconds = duration % 60
        timer = f"{minutes}:{seconds:02d}"
        radiant_kills = _scoreboard_team_score(scoreboard, "radiant")
        dire_kills = _scoreboard_team_score(scoreboard, "dire")
    else:
        timer = "-"
        radiant_kills = 0
        dire_kills = 0
    league_id = match.get("league_id", "N/A")
    match_id = match.get("match_id", "N/A")
    if radiant_kills > dire_kills:
        color = discord.Color.green()
    elif dire_kills > radiant_kills:
        color = discord.Color.red()
    else:
        color = discord.Color.blurple()
    description = (
        f"⏱️ **{timer}** - **Radiant {radiant_kills} : {dire_kills} Dire**"
        if scoreboard
        else "Draft phase (scoreboard not available yet)"
    )
    embed = discord.Embed(title="🏆 Live League Match", description=description, color=color)
    radiant_players, dire_players = [], []
    for player in match.get("players", []) or []:
        team = player.get("team", 0)
        hero_id = player.get("hero_id", 0) or 0
        hero_name = hero_id_map.get(str(hero_id), "-" if hero_id == 0 else f"Hero {hero_id}")
        steam32 = player.get("account_id")
        name = await get_display_name(steam32, guild) if steam32 else "Unknown"
        entry = f"{name} ({hero_name})"
        if team == 0 and len(radiant_players) < 5:
            radiant_players.append(entry)
        elif team == 1 and len(dire_players) < 5:
            dire_players.append(entry)
    if scoreboard and (len(radiant_players) != 5 or len(dire_players) != 5):
        print(f"[format_live_match_embed] Expected 5 players per team. Got Radiant={len(radiant_players)}, Dire={len(dire_players)}")
    while len(radiant_players) < 5:
        radiant_players.append("-")
    while len(dire_players) < 5:
        dire_players.append("-")
    radiant_value = "\n".join(radiant_players)
    dire_value = "\n".join(dire_players)
    betting_left, betting_right = _format_live_betting_columns(guild.id, match_id)
    if betting_left:
        radiant_value += f"\n\n**Betting**\n{betting_left}"
        dire_value += f"\n\n\u200b\n{betting_right}"
    radiant_value += f"\n\n**Match Info**\nLeague ID: `{league_id}`\nMatch ID: `{match_id}`"
    embed.add_field(name="**Radiant**", value=radiant_value, inline=True)
    embed.add_field(name="**Dire**", value=dire_value, inline=True)
    return embed
