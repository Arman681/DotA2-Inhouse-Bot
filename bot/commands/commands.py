# commands.py
import asyncio
import math
import time
from datetime import datetime, timezone
import discord
import re
import shlex
from discord.ext import commands

def attach_commands(bot, deps):
    # ---- pull in all helpers/state exposed by the main bot entrypoint ----
    # Checks / auth
    user_is_admin_or_has_role = deps["user_is_admin_or_has_role"]   # async fn(author) -> bool
    is_admin_or_has_role      = deps["is_admin_or_has_role"]        # decorator factory @is_admin_or_has_role()
    is_global_admin           = deps["is_global_admin"]

    # Firestore + db
    db         = deps["db"]
    firestore  = deps["firestore"]

    # Player + MMR helpers
    convert_to_steam32   = deps["convert_to_steam32"]
    fetch_mmr            = deps["fetch_mmr"]
    save_player_config   = deps["save_player_config"]
    get_mmr              = deps["get_mmr"]
    get_inhouse_mmr      = deps["get_inhouse_mmr"]
    get_top_players      = deps["get_top_players"]

    # Feederbucks / betting
    get_balance                     = deps["get_balance"]
    place_bet                       = deps["place_bet"]
    place_market_bet                = deps["place_market_bet"]
    update_balance                  = deps["update_balance"]
    resolve_bets                    = deps["resolve_bets"]
    clear_guild_bets                = deps["clear_guild_bets"]
    BETTING_MODE_CLASSIC            = deps["BETTING_MODE_CLASSIC"]
    BETTING_MODE_POOL               = deps["BETTING_MODE_POOL"]
    MARKET_MATCH_WINNER             = deps["MARKET_MATCH_WINNER"]
    MARKET_FIRST_BLOOD              = deps["MARKET_FIRST_BLOOD"]
    MARKET_FIRST_TO_10              = deps["MARKET_FIRST_TO_10"]
    MARKET_FIRST_TOWER              = deps["MARKET_FIRST_TOWER"]
    MARKET_DURATION_35              = deps["MARKET_DURATION_35"]
    MARKET_TOTAL_KILLS_50           = deps["MARKET_TOTAL_KILLS_50"]
    MARKET_ORDER                    = deps["MARKET_ORDER"]
    get_betting_settings            = deps["get_betting_settings"]
    get_betting_summary             = deps["get_betting_summary"]
    get_existing_market_bet         = deps["get_existing_market_bet"]
    get_public_market_snapshots     = deps["get_public_market_snapshots"]
    is_market_open_for_betting      = deps["is_market_open_for_betting"]
    normalize_market_id             = deps["normalize_market_id"]
    save_betting_mode_for_guild     = deps["save_betting_mode_for_guild"]
    save_prop_markets_setting       = deps["save_prop_markets_setting"]
    void_market                     = deps["void_market"]
    void_markets                    = deps["void_markets"]
    DD_TOKEN_COST                   = deps["DD_TOKEN_COST"]
    get_dd_token_balance            = deps["get_dd_token_balance"]
    update_dd_token_balance         = deps["update_dd_token_balance"]
    has_active_double_down          = deps["has_active_double_down"]
    activate_double_down            = deps["activate_double_down"]
    get_active_double_down_users    = deps["get_active_double_down_users"]
    clear_active_double_downs       = deps["clear_active_double_downs"]
    get_store_item_info             = deps["get_store_item_info"]
    normalize_store_item_name       = deps["normalize_store_item_name"]
    get_store_cost                  = deps["get_store_cost"]
    save_store_cost_override        = deps["save_store_cost_override"]
    purchase_store_role             = deps["purchase_store_role"]
    log_store_purchase              = deps["log_store_purchase"]
    reset_vip_feeder_role           = deps["reset_vip_feeder_role"]
    reset_custom_store_roles        = deps["reset_custom_store_roles"]

    # Match / live tracking
    fetch_live_match_for_guild   = deps["fetch_live_match_for_guild"]
    poll_live_match              = deps["poll_live_match"]
    format_live_match_embed      = deps["format_live_match_embed"]
    map_steam_ids_to_discord_ids = deps["map_steam_ids_to_discord_ids"]
    fetch_match_result           = deps["fetch_match_result"]
    get_bound_league_id          = deps["get_bound_league_id"]
    get_processed_match          = deps["get_processed_match"]
    is_match_processed           = deps["is_match_processed"]
    log_processed_match          = deps["log_processed_match"]
    log_match_ledger            = deps["log_match_ledger"]
    get_all_match_ledgers       = deps["get_all_match_ledgers"]
    get_recent_match_ledgers    = deps["get_recent_match_ledgers"]
    schedule_match_imp_enrichment = deps["schedule_match_imp_enrichment"]
    get_top_avg_imp_players       = deps["get_top_avg_imp_players"]

    # In-memory state dicts (same ones you already maintain)
    active_match_ids            = deps["active_match_ids"]
    polling_tasks               = deps["polling_tasks"]
    random_polling_flags        = deps["random_polling_flags"]
    match_tracking_start_times  = deps["match_tracking_start_times"]
    live_embed_messages         = deps["live_embed_messages"]
    bets_embed_messages         = deps["bets_embed_messages"]
    bets_refresh_tasks          = deps["bets_refresh_tasks"]
    match_wait_tasks            = deps["match_wait_tasks"]
    original_teams              = deps["original_teams"]

    # Lobby state + helpers
    lobby_players                = deps["lobby_players"]
    lobby_message                = deps["lobby_message"]
    rocket_lock                  = deps["rocket_lock"]
    update_lobby_embed           = deps["update_lobby_embed"]
    build_lobby_embed            = deps["build_lobby_embed"]
    save_lobby_players           = deps["save_lobby_players"]
    save_lobby_message_id        = deps["save_lobby_message_id"]
    save_lobby_password_for_guild= deps["save_lobby_password_for_guild"]
    load_inhouse_mode_for_guild  = deps["load_inhouse_mode_for_guild"]
    save_inhouse_mode_for_guild  = deps["save_inhouse_mode_for_guild"]
    save_preferred_roles_setting = deps["save_preferred_roles_setting"]
    refresh_lobby_member_mmr     = deps["refresh_lobby_member_mmr"]
    start_immortal_draft         = deps["start_immortal_draft"]
    get_captain_policy           = deps["get_captain_policy"]
    set_captain_policy           = deps["set_captain_policy"]
    choose_captain_pair_index    = deps["choose_captain_pair_index"]
    cancel_match_wait            = deps["cancel_match_wait"]
    reset_team_state_for_guild   = deps["reset_team_state_for_guild"]
    full_post_rocket_reset       = deps["full_post_rocket_reset"]
    is_placeholder_player        = deps["is_placeholder_player"]
    format_lobby_player_mention  = deps["format_lobby_player_mention"]

    # Guild settings
    save_guild_prefix            = deps["save_guild_prefix"]
    load_guild_prefix            = deps["load_guild_prefix"]
    save_league_guild_mapping    = deps["save_league_guild_mapping"]
    live_channel_ids             = deps["live_channel_ids"]
    lobby_channel_ids            = deps["lobby_channel_ids"]
    get_lobby_channel_for_guild  = deps["get_lobby_channel_for_guild"]
    inhouse_mode                 = deps["inhouse_mode"]
    captain_draft_state          = deps["captain_draft_state"]
    get_all_captain_pairs        = deps["get_all_captain_pairs"]
    build_immortal_embed         = deps["build_immortal_embed"]

    # Misc helpers
    get_discord_id_from_steam_id = deps["get_discord_id_from_steam_id"]
    adjust_mmr                   = deps["adjust_mmr"]

    # ============================== General Commands ==============================

    def parse_store_tokens(raw: str):
        if raw is None:
            return [], ""
        text = raw.strip()
        if not text:
            return [], ""
        try:
            tokens = shlex.split(text)
        except ValueError:
            tokens = text.split()
        return tokens, text

    def parse_store_item_and_remainder(raw: str):
        tokens, _ = parse_store_tokens(raw)
        if not tokens:
            return None, ""
        for end in range(len(tokens), 0, -1):
            candidate = " ".join(tokens[:end])
            item_key = normalize_store_item_name(candidate)
            if item_key:
                return item_key, " ".join(tokens[end:]).strip()
        return None, ""

    STORE_ITEM_ORDER = [
        "dd_tokens",
        "role_vip_feeder",
        "role_custom_role",
    ]

    def get_store_item_key_by_index(item_index: int):
        if 1 <= item_index <= len(STORE_ITEM_ORDER):
            return STORE_ITEM_ORDER[item_index - 1]
        return None

    def format_fb(amount):
        try:
            return f"{int(amount):,}"
        except (TypeError, ValueError):
            return "0"

    def format_odds(value):
        if value is None:
            return "--x"
        try:
            return f"{float(value):.2f}x"
        except (TypeError, ValueError):
            return "--x"

    def format_option_label(option):
        labels = {
            "radiant": "Radiant",
            "dire": "Dire",
            "over": "Over",
            "under": "Under",
        }
        return labels.get(str(option or "").lower(), str(option or "Unknown").title())

    def parse_bool_toggle(value: str | None):
        lowered = str(value or "").lower().strip()
        if lowered in ("on", "enable", "enabled", "true", "yes"):
            return True
        if lowered in ("off", "disable", "disabled", "false", "no"):
            return False
        return None

    def normalize_betting_mode_arg(value: str | None):
        lowered = str(value or "").lower().strip().replace("-", "_").replace(" ", "_")
        if lowered in ("classic", "old", "standard"):
            return BETTING_MODE_CLASSIC
        if lowered in ("pool", "prizepool", "prize_pool", "twitch"):
            return BETTING_MODE_POOL
        return None

    def parse_bet_args(args):
        tokens = list(args or [])
        market_id = MARKET_MATCH_WINNER
        if tokens:
            first = str(tokens[0]).lower()
            maybe_market = normalize_market_id(first)
            if maybe_market and (not first.isdigit() or len(tokens) >= 3):
                market_id = maybe_market
                tokens = tokens[1:]
        amount = None
        amount_is_all = False
        team = None
        for arg in tokens:
            lower = str(arg).lower()
            if lower in ("radiant", "dire", "over", "under"):
                if team is not None and team != lower:
                    raise ValueError("Please specify only one team: `radiant` or `dire`.")
                team = lower
            else:
                if amount is not None:
                    raise ValueError("Too many amount values.")
                if lower == "all":
                    amount = "all"
                    amount_is_all = True
                else:
                    try:
                        amount = int(arg)
                    except ValueError:
                        raise ValueError("Invalid argument.")
        return market_id, amount, amount_is_all, team

    def market_status_label(status):
        labels = {
            "open": "Open",
            "locked": "Locked",
            "resolved": "Resolved",
            "paid": "Paid",
            "voided": "Voided",
            "cancelled": "Cancelled",
        }
        return labels.get(str(status or "").lower(), str(status or "Unknown").title())

    def build_bets_embed(guild, match_id):
        summary = get_betting_summary(guild.id, match_id)
        if not summary:
            embed = discord.Embed(
                title="Active Betting Markets",
                description="No betting markets are active for this match.",
                color=discord.Color.red(),
            )
            return embed
        mode = summary.get("mode", BETTING_MODE_CLASSIC)
        embed = discord.Embed(
            title="Active Betting Markets",
            description=(
                f"Match ID: `{match_id}`\n"
                f"Mode: `{summary.get('mode_label', 'Classic')}`"
            ),
            color=discord.Color.gold(),
        )
        for market in summary.get("markets") or []:
            pools = market.get("pools") or {}
            options = market.get("options") or ["radiant", "dire"]
            multipliers = market.get("option_multipliers") or {}
            status = market_status_label(market.get("status"))
            winner = market.get("winner")
            lines = [f"Status: `{status}`"]
            if winner:
                lines.append(f"Result: `{format_option_label(winner)}`")
            if mode == BETTING_MODE_POOL:
                lines.extend([
                    f"Prize Pool: `{format_fb(market.get('total_pool'))}`",
                    f"Seeded: `{format_fb(market.get('seed'))}`",
                ])
                for option in options:
                    lines.append(
                        f"{format_option_label(option)}: `{format_fb(pools.get(option))}` "
                        f"| `{format_odds(multipliers.get(option))}`"
                    )
            else:
                lines.extend([
                    "Odds: `2.00x` classic payout",
                ])
                for option in options:
                    lines.append(f"{format_option_label(option)} Bets: `{format_fb(pools.get(option))}`")
            embed.add_field(
                name=f"[{market.get('index')}] {market.get('label')}",
                value="\n".join(lines),
                inline=False,
            )
        embed.set_footer(text="Bet with !bet <market_number> <option> <amount>")
        return embed

    def betting_markets_have_open_status(guild_id, match_id):
        summary = get_betting_summary(guild_id, match_id)
        if not summary:
            return False
        return any(
            str(market.get("status", "")).lower() == "open"
            for market in summary.get("markets") or []
        )

    async def refresh_bets_embed_until_locked(guild, match_id, message):
        guild_id = guild.id
        try:
            while True:
                await asyncio.sleep(15)
                if active_match_ids.get(guild_id) != match_id:
                    break
                try:
                    await message.edit(embed=build_bets_embed(guild, match_id))
                except discord.NotFound:
                    break
                except Exception as e:
                    print(f"[bets_refresh] Failed to refresh bets embed for guild={guild_id} match={match_id}: {e}")
                    break
                if not betting_markets_have_open_status(guild_id, match_id):
                    break
        except asyncio.CancelledError:
            return
        finally:
            current_task = asyncio.current_task()
            if bets_refresh_tasks.get(guild_id) is current_task:
                bets_refresh_tasks.pop(guild_id, None)

    LEADERBOARD_PAGE_SIZE = 10
    MEDAL_EMOJIS = ["\U0001F947", "\U0001F948", "\U0001F949"]
    PAGE_PREV_LABEL = "\u25C0"
    PAGE_NEXT_LABEL = "\u25B6"
    FOOTER_SEPARATOR = " \u2022 "

    def build_leaderboard_embed(guild, players, page_index: int, requester_name: str):
        total_pages = max(1, math.ceil(len(players) / LEADERBOARD_PAGE_SIZE))
        start = page_index * LEADERBOARD_PAGE_SIZE
        end = start + LEADERBOARD_PAGE_SIZE
        page_players = players[start:end]
        embed = discord.Embed(
            title="Inhouse Leaderboard",
            description=f"Leaderboard for **{guild.name}**",
            color=discord.Color.gold()
        )
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for rank, (user_id, stored_nickname, mmr) in enumerate(page_players, start=start + 1):
            member = guild.get_member(int(user_id))
            if member:
                name = member.display_name
            else:
                name = stored_nickname or "Unknown"
                if name.lower() == "unknown":
                    player_doc = db.collection("players").document(str(user_id)).get()
                    if player_doc.exists:
                        pdata = player_doc.to_dict() or {}
                        name = pdata.get("discord_nickname") or pdata.get("steam_name") or name
            prefix = medals[rank - 1] if rank <= 3 else f"**#{rank}**"
            lines.append(f"{prefix} - **{name}**: `{mmr}` MMR")
        embed.add_field(name="Rankings", value="\n".join(lines), inline=False)
        embed.set_footer(text=f"Page {page_index + 1}/{total_pages} • Requested by {requester_name}")
        return embed

    class LeaderboardView(discord.ui.View):
        def __init__(self, author_id: int, guild, players, requester_name: str):
            super().__init__(timeout=600)
            self.author_id = author_id
            self.guild = guild
            self.players = players
            self.requester_name = requester_name
            self.page_index = 0
            self.message = None
            self.sync_buttons()

        def sync_buttons(self):
            total_pages = max(1, math.ceil(len(self.players) / LEADERBOARD_PAGE_SIZE))
            self.prev_page.disabled = self.page_index <= 0
            self.next_page.disabled = self.page_index >= total_pages - 1

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.author_id:
                await interaction.response.send_message(
                    "Only the user who opened this leaderboard can change pages.",
                    ephemeral=True
                )
                return False
            return True

        async def refresh(self, interaction: discord.Interaction):
            self.sync_buttons()
            await interaction.response.edit_message(
                embed=build_leaderboard_embed(self.guild, self.players, self.page_index, self.requester_name),
                view=self
            )

        async def on_timeout(self):
            for child in self.children:
                child.disabled = True
            if self.message:
                try:
                    await self.message.edit(view=self)
                except Exception:
                    pass

        @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
        async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.page_index > 0:
                self.page_index -= 1
            await self.refresh(interaction)

        @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
        async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
            total_pages = max(1, math.ceil(len(self.players) / LEADERBOARD_PAGE_SIZE))
            if self.page_index < total_pages - 1:
                self.page_index += 1
            await self.refresh(interaction)

    def build_avgimp_embed(guild, players, page_index: int, requester_name: str):
        total_pages = max(1, math.ceil(len(players) / LEADERBOARD_PAGE_SIZE))
        start = page_index * LEADERBOARD_PAGE_SIZE
        end = start + LEADERBOARD_PAGE_SIZE
        page_players = players[start:end]
        embed = discord.Embed(
            title="Average IMP Leaderboard",
            description=(
                f"Leaderboard for **{guild.name}**\n\n"
                "**IMP:** Individual Match Performance by STRATZ"
            ),
            color=discord.Color.teal(),
        )
        lines = []
        for rank, (user_id, stored_nickname, avg_imp, match_count) in enumerate(page_players, start=start + 1):
            member = guild.get_member(int(user_id))
            name = member.display_name if member else (stored_nickname or f"User {user_id}")
            prefix = MEDAL_EMOJIS[rank - 1] if rank <= 3 else f"**#{rank}**"
            lines.append(f"{prefix} - **{name}**: `{avg_imp:.2f}` AVG IMP (`{match_count}` matches)")
        embed.add_field(name="Rankings", value="\n".join(lines), inline=False)
        embed.set_footer(text=f"Page {page_index + 1}/{total_pages}{FOOTER_SEPARATOR}Requested by {requester_name}")
        return embed

    class AvgImpView(discord.ui.View):
        def __init__(self, author_id: int, guild, players, requester_name: str):
            super().__init__(timeout=600)
            self.author_id = author_id
            self.guild = guild
            self.players = players
            self.requester_name = requester_name
            self.page_index = 0
            self.message = None
            self.sync_buttons()

        def sync_buttons(self):
            total_pages = max(1, math.ceil(len(self.players) / LEADERBOARD_PAGE_SIZE))
            self.prev_page.disabled = self.page_index <= 0
            self.next_page.disabled = self.page_index >= total_pages - 1

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.author_id:
                await interaction.response.send_message(
                    "Only the user who opened this leaderboard can change pages.",
                    ephemeral=True,
                )
                return False
            return True

        async def refresh(self, interaction: discord.Interaction):
            self.sync_buttons()
            await interaction.response.edit_message(
                embed=build_avgimp_embed(self.guild, self.players, self.page_index, self.requester_name),
                view=self,
            )

        async def on_timeout(self):
            for child in self.children:
                child.disabled = True
            if self.message:
                try:
                    await self.message.edit(view=self)
                except Exception:
                    pass

        @discord.ui.button(label=PAGE_PREV_LABEL, style=discord.ButtonStyle.secondary)
        async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.page_index > 0:
                self.page_index -= 1
            await self.refresh(interaction)

        @discord.ui.button(label=PAGE_NEXT_LABEL, style=discord.ButtonStyle.secondary)
        async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
            total_pages = max(1, math.ceil(len(self.players) / LEADERBOARD_PAGE_SIZE))
            if self.page_index < total_pages - 1:
                self.page_index += 1
            await self.refresh(interaction)

    def build_shared_ranked_embed(title: str, guild, rows, page_index: int, requester_name: str, value_formatter, description: str | None = None, color=discord.Color.blurple()):
        total_pages = max(1, math.ceil(len(rows) / LEADERBOARD_PAGE_SIZE))
        start = page_index * LEADERBOARD_PAGE_SIZE
        end = start + LEADERBOARD_PAGE_SIZE
        page_rows = rows[start:end]
        embed = discord.Embed(
            title=title,
            description=description or f"Leaderboard for **{guild.name}**",
            color=color,
        )
        lines = []
        for rank, row in enumerate(page_rows, start=start + 1):
            user_id = str(row[0])
            stored_nickname = row[1]
            member = guild.get_member(int(user_id)) if user_id.isdigit() else None
            name = member.display_name if member else (stored_nickname or f"User {user_id}")
            prefix = MEDAL_EMOJIS[rank - 1] if rank <= 3 else f"**#{rank}**"
            lines.append(f"{prefix} - **{name}**: {value_formatter(row)}")
        embed.add_field(name="Rankings", value="\n".join(lines), inline=False)
        embed.set_footer(text=f"Page {page_index + 1}/{total_pages}{FOOTER_SEPARATOR}Requested by {requester_name}")
        return embed

    class SharedRankedView(discord.ui.View):
        def __init__(self, *, author_id: int, rows, requester_name: str, embed_builder):
            super().__init__(timeout=600)
            self.author_id = author_id
            self.rows = rows
            self.requester_name = requester_name
            self.embed_builder = embed_builder
            self.page_index = 0
            self.message = None
            self.sync_buttons()

        def sync_buttons(self):
            total_pages = max(1, math.ceil(len(self.rows) / LEADERBOARD_PAGE_SIZE))
            self.prev_page.disabled = self.page_index <= 0
            self.next_page.disabled = self.page_index >= total_pages - 1

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.author_id:
                await interaction.response.send_message(
                    "Only the user who opened this leaderboard can change pages.",
                    ephemeral=True,
                )
                return False
            return True

        async def refresh(self, interaction: discord.Interaction):
            self.sync_buttons()
            await interaction.response.edit_message(
                embed=self.embed_builder(self.rows, self.page_index, self.requester_name),
                view=self,
            )

        async def on_timeout(self):
            for child in self.children:
                child.disabled = True
            if self.message:
                try:
                    await self.message.edit(view=self)
                except Exception:
                    pass

        @discord.ui.button(label=PAGE_PREV_LABEL, style=discord.ButtonStyle.secondary)
        async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.page_index > 0:
                self.page_index -= 1
            await self.refresh(interaction)

        @discord.ui.button(label=PAGE_NEXT_LABEL, style=discord.ButtonStyle.secondary)
        async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
            total_pages = max(1, math.ceil(len(self.rows) / LEADERBOARD_PAGE_SIZE))
            if self.page_index < total_pages - 1:
                self.page_index += 1
            await self.refresh(interaction)

    LEDGER_PAGE_SIZE = 1
    LEDGER_MAX_PAGES = 5

    def format_ledger_timestamp(value):
        if hasattr(value, "to_datetime"):
            value = value.to_datetime()
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return discord.utils.format_dt(value, style="f")
        return "Unknown"

    def truncate_embed_field(text: str, limit: int = 1024):
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    def format_compact_number(value):
        value = int(value or 0)
        if abs(value) >= 1000:
            return f"{value / 1000:.1f}k"
        return str(value)

    def build_feederbucks_award(award_id, user_id, nickname, amount, reason, balance_after=None):
        payload = {
            "award_id": str(award_id),
            "user_id": str(user_id),
            "nickname": nickname,
            "amount": int(amount or 0),
            "reason": reason,
        }
        if balance_after is not None:
            payload["balance_after"] = int(balance_after)
            payload["balance_before"] = int(balance_after) - int(amount or 0)
        return payload

    def is_mvp_feederbucks_award(award):
        if not isinstance(award, dict):
            return False
        award_id = str(award.get("award_id") or "").strip().lower()
        reason = str(award.get("reason") or "").strip().lower()
        return award_id == "mvp_highest_imp" or reason == "mvp bonus"

    def build_ledger_player_stats(guild, raw_player_stats):
        results = []
        for stat in raw_player_stats or []:
            steam_id = str(stat.get("steam_id", ""))
            discord_id = stat.get("user_id") or (get_discord_id_from_steam_id(steam_id) if steam_id else None)
            member = guild.get_member(int(discord_id)) if discord_id and str(discord_id).isdigit() else None
            nickname = member.display_name if member else (stat.get("nickname") or (str(discord_id) if discord_id else steam_id))
            enriched = dict(stat)
            enriched["steam_id"] = steam_id
            enriched["user_id"] = str(discord_id) if discord_id else None
            enriched["nickname"] = nickname
            results.append(enriched)
        return results

    def compute_kda(stat):
        kills = int(stat.get("kills", 0) or 0)
        assists = int(stat.get("assists", 0) or 0)
        deaths = int(stat.get("deaths", 0) or 0)
        return round((kills + assists) / max(1, deaths), 2)

    def get_best_stat_entries(guild, entries):
        categories = {
            "Best KDA": {
                "value_fn": compute_kda,
                "format_fn": lambda stat: f"{compute_kda(stat):.2f}",
            },
            "Highest GPM": {
                "value_fn": lambda stat: int(stat.get("gpm", 0) or 0),
                "format_fn": lambda stat: f"{int(stat.get('gpm', 0) or 0)}",
            },
            "Highest XPM": {
                "value_fn": lambda stat: int(stat.get("xpm", 0) or 0),
                "format_fn": lambda stat: f"{int(stat.get('xpm', 0) or 0)}",
            },
            "Highest Avg APM": {
                "value_fn": lambda stat: int(stat.get("avg_apm", 0) or 0),
                "format_fn": lambda stat: f"{int(stat.get('avg_apm', 0) or 0)}",
            },
            "Highest Hero Damage": {
                "value_fn": lambda stat: int(stat.get("hero_damage", 0) or 0),
                "format_fn": lambda stat: format_compact_number(stat.get("hero_damage", 0)),
            },
            "Highest Building Damage": {
                "value_fn": lambda stat: int(stat.get("building_damage", 0) or 0),
                "format_fn": lambda stat: format_compact_number(stat.get("building_damage", 0)),
            },
        }
        best = {}
        for entry in entries:
            processed_at = entry.get("processed_at")
            match_id = entry.get("match_id", "Unknown")
            for stat in build_ledger_player_stats(guild, entry.get("player_stats") or []):
                user_id = str(stat.get("user_id") or "")
                member = guild.get_member(int(user_id)) if user_id.isdigit() else None
                name = member.display_name if member else stat.get("nickname") or stat.get("steam_id") or "Unknown"
                for label, cfg in categories.items():
                    value = cfg["value_fn"](stat)
                    current = best.get(label)
                    if current is None or value > current["sort_value"]:
                        best[label] = {
                            "label": label,
                            "player_name": name,
                            "display_value": cfg["format_fn"](stat),
                            "sort_value": value,
                            "match_id": match_id,
                            "processed_at": processed_at,
                        }
        return best

    def build_topstats_embed(guild, best_entries, requester_name: str):
        embed = discord.Embed(
            title="Top Match Stats",
            description=f"Best recorded player performances for **{guild.name}**",
            color=discord.Color.green(),
        )
        ordered_labels = [
            "Best KDA",
            "Highest GPM",
            "Highest XPM",
            "Highest Avg APM",
            "Highest Hero Damage",
            "Highest Building Damage",
        ]
        for label in ordered_labels:
            item = best_entries.get(label)
            if not item:
                embed.add_field(name=label, value="No data recorded yet.", inline=False)
                continue
            timestamp_text = format_ledger_timestamp(item.get("processed_at"))
            embed.add_field(
                name=label,
                value=(
                    f"**{item['player_name']}** — `{item['display_value']}`\n"
                    f"Match `{item['match_id']}` • {timestamp_text}"
                ),
                inline=False,
            )
        embed.set_footer(text=f"Requested by {requester_name}")
        return embed

    def build_ledger_embed(guild, entries, page_index: int, requester_name: str):
        total_pages = max(1, min(len(entries), LEDGER_MAX_PAGES))
        entry = entries[page_index]
        match_id = entry.get("match_id", "Unknown")
        winning_team = (entry.get("winning_team") or "unknown").title()
        source = entry.get("source", "unknown")
        league_id = entry.get("league_id") or "N/A"
        timestamp_text = format_ledger_timestamp(entry.get("processed_at"))
        mmr_changes = entry.get("mmr_changes") or []
        bet_results = entry.get("bet_results") or []
        feederbucks_awards = entry.get("feederbucks_awards") or []
        embed = discord.Embed(
            title="Match Ledger",
            description=(
                f"Ledger for **{guild.name}**\n"
                f"**Match ID:** `{match_id}`\n"
                f"**Date/Time:** {timestamp_text}\n"
                f"**Winning Team:** `{winning_team}`\n"
                f"**League ID:** `{league_id}`\n"
                f"**Source:** `{source}`"
            ),
            color=discord.Color.blurple(),
        )
        if mmr_changes:
            mmr_lines = []
            for change in mmr_changes:
                user_id = str(change.get("user_id", ""))
                member = guild.get_member(int(user_id)) if user_id.isdigit() else None
                name = member.display_name if member else change.get("nickname") or f"User {user_id}"
                delta = int(change.get("delta", 0))
                old_mmr = int(change.get("old_mmr", 0))
                new_mmr = int(change.get("new_mmr", 0))
                doubled = " x2" if change.get("doubled") else ""
                sign = "+" if delta > 0 else ""
                mmr_lines.append(
                    f"**{name}**: `{sign}{delta}` MMR{doubled} (`{old_mmr}` -> `{new_mmr}`)"
                )
            embed.add_field(
                name="Inhouse MMR",
                value=truncate_embed_field("\n".join(mmr_lines)),
                inline=False,
            )
        else:
            embed.add_field(
                name="Inhouse MMR",
                value="No inhouse MMR adjustments were recorded for this match.",
                inline=False,
            )
        if bet_results:
            bet_lines = []
            for bet in bet_results:
                user_id = str(bet.get("user_id", ""))
                member = guild.get_member(int(user_id)) if user_id.isdigit() else None
                name = member.display_name if member else bet.get("nickname") or f"User {user_id}"
                amount = int(bet.get("amount", 0))
                net_delta = int(bet.get("net_delta", 0))
                sign = "+" if net_delta > 0 else ""
                team = str(bet.get("team", "unknown")).title()
                market_label = bet.get("market_label") or "Match Winner"
                balance_before = bet.get("balance_before")
                balance_after = bet.get("balance_after")
                if balance_before is not None and balance_after is not None:
                    bet_lines.append(
                        f"**{name}**: `{market_label}` bet `{amount}` on `{team}`, result `{sign}{net_delta}` (`{int(balance_before)}` -> `{int(balance_after)}`)"
                    )
                else:
                    bet_lines.append(
                        f"**{name}**: `{market_label}` bet `{amount}` on `{team}`, result `{sign}{net_delta}`"
                    )
            embed.add_field(
                name="Bets",
                value=truncate_embed_field("\n".join(bet_lines)),
                inline=False,
            )
        else:
            embed.add_field(
                name="Bets",
                value="No bets were recorded for this match.",
                inline=False,
            )
        display_feederbucks_awards = [
            award for award in feederbucks_awards
            if is_mvp_feederbucks_award(award)
        ]
        if display_feederbucks_awards:
            award_lines = []
            for award in display_feederbucks_awards:
                user_id = str(award.get("user_id", ""))
                member = guild.get_member(int(user_id)) if user_id.isdigit() else None
                name = member.display_name if member else award.get("nickname") or f"User {user_id}"
                amount = int(award.get("amount", 0) or 0)
                sign = "+" if amount > 0 else ""
                reason = award.get("reason") or "Award"
                balance_before = award.get("balance_before")
                balance_after = award.get("balance_after")
                if balance_before is not None and balance_after is not None:
                    award_lines.append(
                        f"**{name}**: `{reason}` `{sign}{amount}` Feederbucks (`{int(balance_before)}` -> `{int(balance_after)}`)"
                    )
                else:
                    award_lines.append(f"**{name}**: `{reason}` `{sign}{amount}` Feederbucks")
            embed.add_field(
                name="Feederbucks",
                value=truncate_embed_field("\n".join(award_lines)),
                inline=False,
            )
        embed.set_footer(text=f"Page {page_index + 1}/{total_pages} • Requested by {requester_name}")
        return embed

    class LedgerView(discord.ui.View):
        def __init__(self, author_id: int, guild, entries, requester_name: str):
            super().__init__(timeout=600)
            self.author_id = author_id
            self.guild = guild
            self.entries = entries[:LEDGER_MAX_PAGES]
            self.requester_name = requester_name
            self.page_index = 0
            self.message = None
            self.sync_buttons()
        def sync_buttons(self):
            total_pages = max(1, len(self.entries))
            self.prev_page.disabled = self.page_index <= 0
            self.next_page.disabled = self.page_index >= total_pages - 1
        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.author_id:
                await interaction.response.send_message(
                    "Only the user who opened this ledger can change pages.",
                    ephemeral=True
                )
                return False
            return True
        async def refresh(self, interaction: discord.Interaction):
            self.sync_buttons()
            await interaction.response.edit_message(
                embed=build_ledger_embed(self.guild, self.entries, self.page_index, self.requester_name),
                view=self
            )
        async def on_timeout(self):
            for child in self.children:
                child.disabled = True
            if self.message:
                try:
                    await self.message.edit(view=self)
                except Exception:
                    pass
        @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
        async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.page_index > 0:
                self.page_index -= 1
            await self.refresh(interaction)

        @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
        async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
            total_pages = max(1, len(self.entries))
            if self.page_index < total_pages - 1:
                self.page_index += 1
            await self.refresh(interaction)

    @bot.command(name="cfg")
    async def cfg_cmd(ctx, steam_id: str, member: discord.Member = None, *, force: str = None):
        MMR_CAP_FOR_TOP_RANKS = 5650
        if steam_id is None:
            await ctx.reply("Please provide a valid numeric Steam friend code or Steam ID.")
            return
        force_flag = (force is not None and force.strip().lower() == "--force")
        target = member or ctx.author
        # figure out what they're trying to do
        targeting_other = (target != ctx.author)
        using_force = force_flag
        # if they try either action, require admin/inhouse role
        if targeting_other or using_force:
            is_authorized = await user_is_admin_or_has_role(ctx.author)
            if not is_authorized:
                if targeting_other and using_force:
                    await ctx.reply("You tried to configure another user **with `--force`**. Only admins or Inhouse Admins can do that.")
                elif targeting_other:
                    await ctx.reply("Only admins or Inhouse Admins can configure **other users**.")
                else:  # using_force only
                    await ctx.reply("`--force` can only be used by admins or Inhouse Admins.")
                return
        steam_id_32 = convert_to_steam32(steam_id)
        if steam_id_32 is None:
            await ctx.reply("Invalid Steam ID provided.")
            return
        player_ref = db.collection("players").document(str(target.id))
        snap = player_ref.get()
        existing = snap.to_dict() if snap.exists else {}
        existing_steam_id = existing.get("steam_id")
        existing_mmr = existing.get("mmr")
        if force_flag:
            mmr, season_rank, source = await fetch_mmr(steam_id_32)
            if mmr is None and season_rank is not None and season_rank >= 80:
                mmr = MMR_CAP_FOR_TOP_RANKS
                await ctx.reply(
                    "STRATZ does not provide season rank values beyond 80.\n"
                    f"Your estimated MMR has been capped at **{MMR_CAP_FOR_TOP_RANKS}** "
                    f"based on your season rank ({season_rank})."
                )
            config_data = {
                "steam_id": steam_id_32,
                "steam_name": target.name,
                "discord_username": str(target),
                "discord_nickname": target.nick if target.nick else target.display_name,
                "mmr": mmr,
                "seasonRank": season_rank,
                "mmrSource": source,
                "mmrUpdatedAt": firestore.SERVER_TIMESTAMP,
                "steamLinkedAt": firestore.SERVER_TIMESTAMP,
            }
            save_player_config(str(target.id), config_data)
            await ctx.reply(
                f"{target.mention}, your Steam ID `{steam_id}` has been force-updated "
                f"with an estimated MMR of **{mmr if mmr is not None else 'N/A'}**."
            )
            await refresh_lobby_member_mmr(ctx.guild, target, mmr)
            return
        # Case A
        if existing_steam_id is not None and isinstance(existing_mmr, (int, float)):
            await ctx.reply(
                f"{ctx.author.mention}, **{target.display_name}** is already configured "
                f"(Steam ID linked and MMR set)."
            )
            return
        # Case B
        if existing_steam_id is None and isinstance(existing_mmr, (int, float)):
            player_ref.set({
                "steam_id": steam_id_32,
                "steamLinkedAt": firestore.SERVER_TIMESTAMP
            }, merge=True)
            await ctx.reply(
                f"{target.mention}, your Steam ID `{steam_id}` has been linked. "
                f"(Existing MMR {int(existing_mmr)} preserved.)"
            )
            return
        # Case C
        if existing_steam_id is not None and not isinstance(existing_mmr, (int, float)):
            mmr, season_rank, source = await fetch_mmr(existing_steam_id)
            if mmr is None and season_rank is not None and season_rank >= 80:
                mmr = MMR_CAP_FOR_TOP_RANKS
                await ctx.reply(
                    "STRATZ does not provide season rank values beyond 80.\n"
                    f"Your estimated MMR has been capped at **{MMR_CAP_FOR_TOP_RANKS}** "
                    f"based on your season rank ({season_rank})."
                )
            player_ref.set({
                "mmr": mmr,
                "seasonRank": season_rank,
                "mmrSource": source,
                "mmrUpdatedAt": firestore.SERVER_TIMESTAMP
            }, merge=True)
            if mmr is not None:
                await ctx.reply(f"{target.mention}, your MMR has been set to **{mmr}**.")
                await refresh_lobby_member_mmr(ctx.guild, target, mmr)
            else:
                await ctx.reply(f"{target.mention}, Steam ID was linked earlier, but I still couldn’t determine your MMR.")
            return
        # Case D
        mmr, season_rank, source = await fetch_mmr(steam_id_32)
        if mmr is None and season_rank is not None and season_rank >= 80:
            mmr = MMR_CAP_FOR_TOP_RANKS
            await ctx.reply(
                "STRATZ does not provide season rank values beyond 80.\n"
                f"Your estimated MMR has been capped at **{MMR_CAP_FOR_TOP_RANKS}** "
                f"based on your season rank ({season_rank})."
            )
        config_data = {
            "steam_id": steam_id_32,
            "steam_name": target.name,
            "discord_username": str(target),
            "discord_nickname": target.nick if target.nick else target.display_name,
            "mmr": mmr,
            "seasonRank": season_rank,
            "mmrSource": source,
            "mmrUpdatedAt": firestore.SERVER_TIMESTAMP,
            "steamLinkedAt": firestore.SERVER_TIMESTAMP,
        }
        save_player_config(str(target.id), config_data)
        if mmr is not None:
            await ctx.reply(
                f"{target.mention}, your Steam ID `{steam_id}` has been linked "
                f"with an estimated MMR of **{mmr}**."
            )
            await refresh_lobby_member_mmr(ctx.guild, target, mmr)
        else:
            await ctx.reply(
                f"{target.mention}, Steam ID linked, but MMR could not be determined."
            )
        guild_id = str(ctx.guild.id)
        user_id  = str(target.id)
        nickname = target.nick or target.display_name
        update_balance(guild_id, user_id, 0, nickname=nickname)  # delta 0 seeds to 1000
    @cfg_cmd.error
    async def cfg_cmd_error(ctx, error):
        if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument, commands.UserInputError)):
            await ctx.reply("Usage: !cfg `<steam_id>` (optional (admin-only): `[@user]` `[--force]`)")

    @bot.command(name="mmr")
    async def mmr_lookup(ctx, member: discord.Member = None):
        user = member or ctx.author
        mmr = get_mmr(user)
        await ctx.reply(f"{user.display_name}'s MMR is **{mmr}**.")

    @bot.command(name="inhouse_mmr")
    async def inhouse_mmr(ctx, member: discord.Member = None):
        member = member or ctx.author
        mmr = await get_inhouse_mmr(bot, ctx.guild.id, str(member.id))
        await ctx.reply(f"{member.display_name}'s inhouse MMR is **{mmr}**.")

    @bot.command(name="leaderboard")
    async def leaderboard(ctx):
        top_players = get_top_players(ctx.guild.id, limit=None)
        if not top_players:
            await ctx.reply("No leaderboard data found for this server.")
            return
        embed_builder = lambda rows, page_index, requester_name: build_shared_ranked_embed(
            "Inhouse MMR Leaderboard",
            ctx.guild,
            rows,
            page_index,
            requester_name,
            value_formatter=lambda row: f"`{row[2]}` MMR",
            color=discord.Color.gold(),
        )
        view = SharedRankedView(
            author_id=ctx.author.id,
            rows=top_players,
            requester_name=ctx.author.display_name,
            embed_builder=embed_builder,
        )
        message = await ctx.reply(
            embed=embed_builder(top_players, 0, ctx.author.display_name),
            view=view
        )
        view.message = message

    @bot.command(name="avgimp")
    async def avgimp(ctx):
        top_players = get_top_avg_imp_players(ctx.guild.id)
        if not top_players:
            await ctx.reply("No average IMP data found for this server yet. Players need at least 4 matches with IMP data.")
            return
        embed_builder = lambda rows, page_index, requester_name: build_shared_ranked_embed(
            "Average IMP Leaderboard",
            ctx.guild,
            rows,
            page_index,
            requester_name,
            value_formatter=lambda row: f"`{row[2]:.2f}` (`{row[3]}` matches)",
            description=(
                f"Leaderboard for **{ctx.guild.name}**\n\n"
                "**IMP:** Individual Match Performance by STRATZ"
            ),
            color=discord.Color.teal(),
        )
        view = SharedRankedView(
            author_id=ctx.author.id,
            rows=top_players,
            requester_name=ctx.author.display_name,
            embed_builder=embed_builder,
        )
        message = await ctx.reply(
            embed=embed_builder(top_players, 0, ctx.author.display_name),
            view=view,
        )
        view.message = message

    @bot.command(name="ledger")
    async def ledger(ctx):
        entries = get_recent_match_ledgers(ctx.guild.id, limit=LEDGER_MAX_PAGES)
        if not entries:
            await ctx.reply("No ledger data found for this server yet.")
            return
        view = LedgerView(
            author_id=ctx.author.id,
            guild=ctx.guild,
            entries=entries,
            requester_name=ctx.author.display_name,
        )
        message = await ctx.reply(
            embed=build_ledger_embed(ctx.guild, entries, 0, ctx.author.display_name),
            view=view
        )
        view.message = message

    @bot.command(name="topstats")
    async def topstats(ctx):
        entries = get_all_match_ledgers(ctx.guild.id)
        if not entries:
            await ctx.reply("No match data found for this server yet.")
            return
        best_entries = get_best_stat_entries(ctx.guild, entries)
        if not best_entries:
            await ctx.reply("No player stat data has been recorded yet.")
            return
        await ctx.reply(
            embed=build_topstats_embed(ctx.guild, best_entries, ctx.author.display_name)
        )

    @commands.cooldown(1, 5, commands.BucketType.user)  # 1 use / 5s per user
    @bot.command(name="bet")
    async def bet(ctx, *args):
        DEFAULT_BET = 100
        try:
            market_id, amount, amount_is_all, team = parse_bet_args(args)
        except ValueError as exc:
            await ctx.reply(
                f"{exc}\n"
                "Usage: `!bet [market] [amount|all] [radiant|dire]` or `!bet <market_number> <radiant|dire> <amount>`."
            )
            return
        if amount is None:
            amount = DEFAULT_BET
        if not amount_is_all and amount <= 0:
            await ctx.reply("Bet amount must be greater than 0.")
            return
        user_id = str(ctx.author.id)
        nickname = ctx.author.nick if ctx.author.nick else ctx.author.display_name
        if ctx.guild.id not in active_match_ids:
            await ctx.reply("There is no active match in progress to bet on.")
            return
        is_random = random_polling_flags.get(ctx.guild.id, False)
        auto_detected = False # whether we auto-detected the bettor's team
        match = await fetch_live_match_for_guild(ctx.guild.id, random_mode=is_random)
        if not match:
            await ctx.reply("Could not retrieve live match info. Betting may be closed.")
            return
        match_id = match.get("match_id")
        market_snapshot = next(
            (m for m in get_public_market_snapshots(ctx.guild.id, match_id) if m.get("id") == market_id),
            None,
        )
        market_label = (market_snapshot or {}).get("label", "Match Winner")
        market_options = (market_snapshot or {}).get("options") or ["radiant", "dire"]
        if not is_market_open_for_betting(ctx.guild.id, match_id, market_id):
            await ctx.reply(f"Bets are closed for **{market_label}**.")
            return
        # --- Auto-detect bettor's team if they're playing ---
        player_team = None  # 0 = Radiant, 1 = Dire
        for player in match.get("players", []):
            steam_id = player.get("account_id")
            discord_id = get_discord_id_from_steam_id(str(steam_id))
            if discord_id == str(ctx.author.id):
                player_team = player.get("team")  # 0 = Radiant, 1 = Dire
                break
        if market_id != MARKET_MATCH_WINNER and player_team is not None:
            await ctx.reply(
                "Players in the active match cannot bet on side markets like "
                f"**{market_label}**."
            )
            return
        # If team not provided, fill it from player's team (if playing), else prompt
        if team is None:
            if market_id == MARKET_MATCH_WINNER and player_team is not None:
                team = "radiant" if player_team == 0 else "dire"
                auto_detected = True # they didn't specify, but we found it
            else:
                await ctx.reply(
                    "You’re not in the current match. Please specify a team:\n"
                    "Example: `!bet radiant`, `!bet 2 dire 250`, or `!bet 5 over 250`."
                )
                return
        # Normalize & validate team now that it’s known
        team = str(team).lower().strip()
        if team not in market_options:
            await ctx.reply(
                "Invalid option. Choose one of: "
                + ", ".join(f"`{option}`" for option in market_options)
                + "."
            )
            return
        if market_id == MARKET_MATCH_WINNER and player_team is not None:
            if (player_team == 0 and team == "dire") or (player_team == 1 and team == "radiant"):
                await ctx.reply(
                    f"You are currently playing on the **{'Radiant' if player_team == 0 else 'Dire'}** team.\n"
                    f"You cannot place a bet on the **opposing team** during a match you are in."
                )
                return
        existing_bet = get_existing_market_bet(ctx.guild.id, match_id, market_id, ctx.author.id)
        previous_bet = 0
        delta = amount
        is_update = False
        if existing_bet:
            try:
                previous_bet = int(existing_bet.get("amount", 0) or 0)
            except (TypeError, ValueError):
                await ctx.reply("Your existing bet amount is invalid in storage. Please contact an admin.")
                return
            previous_team = existing_bet.get("team", "")
            if team != previous_team:
                await ctx.reply(
                    f"You already bet on **{format_option_label(previous_team)}**. "
                    f"You cannot change options once your bet is placed."
                )
                return
            if not amount_is_all and amount <= previous_bet:
                await ctx.reply(
                    f"You already bet `{previous_bet}`. You can only **increase** your bet amount."
                )
                return
            if not amount_is_all:
                delta = amount - previous_bet
            is_update = True
        try:
            current_balance = int(get_balance(ctx.guild.id, ctx.author.id, nickname=nickname) or 0)
        except (TypeError, ValueError):
            await ctx.reply("Your wallet balance is invalid in storage. Please contact an admin.")
            return
        if amount_is_all:
            amount = current_balance + previous_bet
            if amount <= previous_bet:
                await ctx.reply("You do not have any available Feederbucks left to add to this bet.")
                return
            delta = amount - previous_bet
        if (current_balance + previous_bet) < amount:
            await ctx.reply("You don’t have enough balance.")
        else:
            try:
                place_market_bet(user_id, team, amount, delta, ctx.guild.id, match_id, market_id, nickname)
            except ValueError as exc:
                await ctx.reply(str(exc))
                return
            new_balance = current_balance - delta
            prefix = ""
            if auto_detected:
                prefix = (
                    f"Detected you’re playing on **{team.capitalize()}**. "
                    f"Placing your bet on **{team.capitalize()}** by default.\n"
                )
            if is_update:
                await ctx.reply(
                    prefix +
                    f"You updated your **{market_label}** bet from `{previous_bet}` to `{amount}` on **{format_option_label(team)}**. "
                    f"Your balance went from {current_balance} to {new_balance}."
                )
            else:
                await ctx.reply(
                    prefix +
                    f"You bet `{amount}` on **{format_option_label(team)}** for **{market_label}**. "
                    f"Your balance went from {current_balance} to {new_balance}."
                )
    @bet.error
    async def bet_error(ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(f"You're betting too fast. Try again in `{error.retry_after:.1f}` seconds.")
            return
        original = getattr(error, "original", error)
        print(f"[bet_error] guild={getattr(ctx.guild, 'id', 'dm')} user={ctx.author.id} error={type(original).__name__}: {original}")
        await ctx.reply("An unexpected error occurred while placing your bet. Usage: `!bet [market] [amount] [radiant|dire]`.")

    @commands.cooldown(1, 30, commands.BucketType.guild)
    @bot.command(name="bets")
    async def bets(ctx):
        if ctx.guild.id not in active_match_ids:
            await ctx.reply("There is no active match in progress.")
            return
        guild_id = ctx.guild.id
        match_id = active_match_ids.get(guild_id)
        is_random = random_polling_flags.get(guild_id, False)
        match = await fetch_live_match_for_guild(guild_id, random_mode=is_random)
        if match:
            match_id = match.get("match_id", match_id)
        old_task = bets_refresh_tasks.pop(guild_id, None)
        if old_task and not old_task.done():
            old_task.cancel()
        previous_message = bets_embed_messages.pop(guild_id, None)
        if previous_message:
            try:
                await previous_message.delete()
            except Exception:
                pass
        message = await ctx.reply(embed=build_bets_embed(ctx.guild, match_id))
        bets_embed_messages[guild_id] = message
        if betting_markets_have_open_status(guild_id, match_id):
            bets_refresh_tasks[guild_id] = asyncio.create_task(
                refresh_bets_embed_until_locked(ctx.guild, match_id, message)
            )

    @bets.error
    async def bets_error(ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            wait_time = math.ceil(error.retry_after)
            await ctx.reply(f"You must wait {wait_time} seconds before using `!bets` again.")
            return
        await ctx.reply("An error occurred while showing active betting markets.")

    @bot.command(name="bettingrules")
    async def bettingrules(ctx):
        embed = discord.Embed(
            title="Betting Rules",
            description=f"Rules for **{ctx.guild.name}**",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Markets",
            value=(
                "`Match Winner`, `First Blood`, and `First to 10 Kills` are always available when a match is active.\n"
                "When prop markets are enabled, `First Tower`, `Game Duration O/U 35:00`, and `Total Kills O/U 50` are also available.\n"
                "Players in the active match may only bet on Match Winner, and only on their own team."
            ),
            inline=False,
        )
        embed.add_field(
            name="Integrity",
            value=(
                "Collusion, bribery, or intentional gameplay manipulation for betting outcomes is prohibited.\n"
                "Admins/Inhouse Admins may void suspicious markets and refund affected bets.\n"
                "First Tower is voided/refunded if both teams lose a tower between Steam API polls."
            ),
            inline=False,
        )
        embed.add_field(
            name="Payouts",
            value=(
                "Classic mode pays `2.00x` gross payout on winning bets.\n"
                "Prize Pool mode splits each market's prize pool proportionally among winning bettors.\n"
                "If nobody bets on the Match Winner outcome, that prize pool carries over as jackpot.\n"
                "Side/prop markets use smaller seeds and do not carry over.\n"
                "`Over` means at least 35:00 for duration or at least 50 total kills."
            ),
            inline=False,
        )
        embed.add_field(
            name="Locks",
            value=(
                "Markets lock when the game starts, when scoring begins, or 60 seconds after all heroes are fetched."
            ),
            inline=False,
        )
        embed.set_footer(text="Use !bets to view active markets and !betmode to view the current mode.")
        await ctx.reply(embed=embed)

    @bot.command(name="betmode")
    @is_admin_or_has_role()
    async def betmode(ctx, mode: str = None):
        settings = get_betting_settings(ctx.guild.id)
        if mode is None:
            await ctx.reply(
                f"Current betting mode: `{settings['mode']}` "
                f"({ 'Prize Pool' if settings['mode'] == BETTING_MODE_POOL else 'Classic' }).\n"
                "Use `!betmode classic` or `!betmode pool` to change future matches."
            )
            return
        normalized = normalize_betting_mode_arg(mode)
        if normalized is None:
            await ctx.reply("Usage: `!betmode <classic|pool>`")
            return
        save_betting_mode_for_guild(ctx.guild.id, normalized, server_name=ctx.guild.name, set_by=ctx.author)
        label = "Prize Pool" if normalized == BETTING_MODE_POOL else "Classic"
        live_note = ""
        if ctx.guild.id in active_match_ids:
            live_note = "\nCurrent live matches keep the betting mode they started with."
        await ctx.reply(f"Betting mode changed to **{label}** for future matches.{live_note}")

    @betmode.error
    async def betmode_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to change betting mode. You must be a server admin or have the 'Inhouse Admin' role.")
        else:
            await ctx.reply("An unexpected error occurred while changing betting mode.")

    @bot.command(name="propmarkets", aliases=["propmarket"])
    @is_admin_or_has_role()
    async def propmarkets(ctx, mode: str = None):
        settings = get_betting_settings(ctx.guild.id)
        if mode is None:
            status = "enabled" if settings["prop_markets_enabled"] else "disabled"
            await ctx.reply(f"Prop markets are currently **{status}** for this server.")
            return
        enabled = parse_bool_toggle(mode)
        if enabled is None:
            await ctx.reply("Usage: `!propmarkets <on|off>`")
            return
        save_prop_markets_setting(ctx.guild.id, enabled, server_name=ctx.guild.name, set_by=ctx.author)
        await ctx.reply(f"Prop markets are now **{'enabled' if enabled else 'disabled'}** for future markets.")

    @propmarkets.error
    async def propmarkets_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to change prop markets. You must be a server admin or have the 'Inhouse Admin' role.")
        else:
            await ctx.reply("An unexpected error occurred while changing prop market settings.")

    @bot.command(name="voidmarket")
    @is_admin_or_has_role()
    async def voidmarket(ctx, market: str = None, *, reason: str = ""):
        if market is None:
            await ctx.reply("Usage: `!voidmarket <market_number|match|firstblood|first10|side|all> [reason]`")
            return
        if ctx.guild.id not in active_match_ids:
            await ctx.reply("There is no active match with betting markets to void.")
            return
        match_id = active_match_ids.get(ctx.guild.id)
        selector = str(market).lower().strip()
        existing_market_ids = [
            snapshot.get("id")
            for snapshot in get_public_market_snapshots(ctx.guild.id, match_id)
            if snapshot.get("id")
        ]
        if selector in ("all", "everything"):
            market_ids = existing_market_ids
        elif selector in ("side", "sides"):
            market_ids = [market_id for market_id in existing_market_ids if market_id != MARKET_MATCH_WINNER]
        elif selector in ("prop", "props"):
            prop_ids = {MARKET_FIRST_TOWER, MARKET_DURATION_35, MARKET_TOTAL_KILLS_50}
            market_ids = [market_id for market_id in existing_market_ids if market_id in prop_ids]
        else:
            market_id = normalize_market_id(selector)
            if not market_id:
                await ctx.reply("Unknown market. Use `!bets` to see market numbers.")
                return
            market_ids = [market_id]
        if not market_ids:
            await ctx.reply("No matching markets are active for this match.")
            return
        try:
            results = void_markets(
                ctx.guild.id,
                match_id,
                market_ids,
                reason=reason or "No reason provided",
                voided_by=ctx.author,
            )
        except ValueError as exc:
            await ctx.reply(str(exc))
            return
        lines = []
        for result in results:
            label = result.get("market_label", result.get("market_id", "Market"))
            already = " already voided" if result.get("already_voided") else ""
            lines.append(
                f"**{label}**{already}: refunded `{format_fb(result.get('refunded_total'))}` "
                f"across `{result.get('bet_count', 0)}` bets."
            )
        await ctx.reply("\n".join(lines))

    @voidmarket.error
    async def voidmarket_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to void markets. You must be a server admin or have the 'Inhouse Admin' role.")
        else:
            await ctx.reply("An unexpected error occurred while voiding the market.")

    @bot.command(name="balance", aliases=["money", "feederbucks"])
    async def balance(ctx, member: discord.Member = None):
        member = member or ctx.author
        user_id = str(member.id)
        guild_id = str(ctx.guild.id)
        feederbucks = get_balance(guild_id, user_id, nickname=member.display_name)
        await ctx.reply(f"{member.display_name}'s balance: `{feederbucks}` Feederbucks.")

    @bot.command(name="store", aliases=["shop"])
    async def store(ctx):
        lines = []
        for index, item_key in enumerate(STORE_ITEM_ORDER, start=1):
            item_info = get_store_item_info(item_key)
            item_cost = get_store_cost(ctx.guild.id, item_key)
            description = f"`{item_cost}` Feederbucks"
            if item_key == "dd_tokens":
                description += " for one Double-Down Token"
            elif item_key == "role_vip_feeder":
                description += " for the VIP Feeder role that expires in 7 days"
            elif item_key == "role_custom_role":
                description += " for a custom role that expires in 7 days"
            lines.append(f"`{index}`. **{item_info['display_name']}** - {description}")
        embed = discord.Embed(
            title="Store",
            description=f"Shop for **{ctx.guild.name}**",
            color=discord.Color.green()
        )
        embed.add_field(name="Items", value="\n".join(lines), inline=False)
        embed.add_field(
            name="How To Buy",
            value=(
                "`!buy <item_number> <amount> [any additional optional parameters]`\n"
                "Example: `!buy 1 1`\n"
                "Example: `!buy 2 1`\n"
                "Example: `!buy 3 1 My Custom Role`"
            ),
            inline=False
        )
        await ctx.reply(embed=embed)

    @bot.command(name="buy")
    async def buy(ctx, *, raw_args: str = None):
        if raw_args is None:
            await ctx.reply(
                "Usage: `!buy <item_number> <amount> [any additional optional parameters]`\n"
                "Example: `!buy 1 1`\n"
                "Example: `!buy 2 1`\n"
                "Example: `!buy 3 1 My Custom Role`"
            )
            return
        tokens, _ = parse_store_tokens(raw_args)
        if not tokens:
            await ctx.reply("Usage: `!buy <item_number> <amount> [any additional optional parameters]`")
            return
        try:
            item_index = int(tokens[0])
        except ValueError:
            await ctx.reply("The first argument to `!buy` must be the store item index from `!store`.")
            return
        item_key = get_store_item_key_by_index(item_index)
        if item_key is None:
            await ctx.reply(f"Invalid store item index. Please choose a number from `1` to `{len(STORE_ITEM_ORDER)}`.")
            return
        guild_id = str(ctx.guild.id)
        user_id = str(ctx.author.id)
        nickname = ctx.author.display_name
        item_info = get_store_item_info(item_key)
        item_cost = get_store_cost(guild_id, item_key)
        current_balance = get_balance(guild_id, user_id, nickname=nickname)

        if item_key == "dd_tokens":
            amount = 1
            if len(tokens) >= 2:
                try:
                    amount = int(tokens[1])
                except ValueError:
                    await ctx.reply("Usage: `!buy 1 <amount>`")
                    return
            if amount <= 0:
                await ctx.reply("Amount must be greater than 0.")
                return
            total_cost = item_cost * amount
            if current_balance < total_cost:
                await ctx.reply(
                    f"You do not have enough Feederbucks.\n"
                    f"Cost: `{total_cost}` Feederbucks\n"
                    f"Your balance: `{current_balance}` Feederbucks"
                )
                return
            update_balance(guild_id, user_id, -total_cost, nickname=nickname)
            update_dd_token_balance(guild_id, user_id, amount, nickname=nickname)
            log_store_purchase(
                guild_id,
                user_id,
                item_key,
                total_cost,
                details={"amount": amount, "nickname": nickname}
            )
            new_balance = get_balance(guild_id, user_id, nickname=nickname)
            new_tokens = get_dd_token_balance(guild_id, user_id, nickname=nickname)
            await ctx.reply(
                f"You bought `{amount}` dd_tokens for `{total_cost}` Feederbucks.\n"
                f"New balance: `{new_balance}` Feederbucks\n"
                f"Your dd_tokens: `{new_tokens}`"
            )
            return

        amount = 1
        if len(tokens) >= 2:
            try:
                amount = int(tokens[1])
            except ValueError:
                await ctx.reply("Usage: `!buy <item_number> <amount> [any additional optional parameters]`")
                return
        if amount <= 0:
            await ctx.reply("Amount must be greater than 0.")
            return
        total_cost = item_cost * amount
        if current_balance < total_cost:
            await ctx.reply(
                f"You do not have enough Feederbucks.\n"
                f"Cost: `{total_cost}` Feederbucks\n"
                f"Your balance: `{current_balance}` Feederbucks"
            )
            return
        custom_role_name = " ".join(tokens[2:]).strip() if item_key == "role_custom_role" else None
        success, result = await purchase_store_role(
            ctx.author,
            item_key,
            custom_role_name=custom_role_name,
            quantity=amount,
        )
        if not success:
            await ctx.reply(result)
            return
        update_balance(guild_id, user_id, -total_cost, nickname=nickname)
        log_store_purchase(
            guild_id,
            user_id,
            item_key,
            total_cost,
            details={
                "nickname": nickname,
                "role_name": result["role_name"],
                "expires_at": result["expires_at"],
                "is_custom_role": result["is_custom_role"],
                "amount": amount,
                "duration_days": result["duration_days"],
                "extended": result["extended"],
            }
        )
        new_balance = get_balance(guild_id, user_id, nickname=nickname)
        await ctx.reply(
            f"You bought `{item_info['display_name']}` x`{amount}` for `{total_cost}` Feederbucks.\n"
            f"Assigned role: `{result['role_name']}`\n"
            f"Expires: `{result['expires_at'].strftime('%Y-%m-%d %H:%M UTC')}`\n"
            f"Duration added: `{result['duration_days']}` day(s)\n"
            f"New balance: `{new_balance}` Feederbucks"
        )

    @buy.error
    async def buy_error(ctx, error):
        if isinstance(error, commands.BadArgument):
            await ctx.reply(
                "Usage: `!buy <item_number> <amount> [any additional optional parameters]`"
            )
        else:
            await ctx.reply("An unexpected error occurred while buying from the store.")

    @bot.command(name="modifycost")
    @is_admin_or_has_role()
    async def modifycost(ctx, *, raw_args: str = None):
        if not raw_args:
            await ctx.reply("Usage: `!modifycost <item_index|item_name> <cost>`")
            return
        tokens, _ = parse_store_tokens(raw_args)
        if len(tokens) < 2:
            await ctx.reply("Usage: `!modifycost <item_index|item_name> <cost>`")
            return
        try:
            new_cost = int(tokens[-1])
        except ValueError:
            await ctx.reply("The last argument must be a whole-number Feederbucks cost.")
            return
        if new_cost < 0:
            await ctx.reply("Cost cannot be negative.")
            return
        item_name = " ".join(tokens[:-1]).strip()
        item_key = None
        if len(tokens[:-1]) == 1:
            try:
                item_index = int(tokens[0])
            except ValueError:
                item_index = None
            if item_index is not None:
                item_key = get_store_item_key_by_index(item_index)
                if item_key is None:
                    await ctx.reply(f"Invalid store item index. Please choose a number from `1` to `{len(STORE_ITEM_ORDER)}`.")
                    return
        if item_key is None:
            item_key = normalize_store_item_name(item_name)
        if item_key is None:
            await ctx.reply(
                "Unknown store item. Try a store number from `!store`, `Double-Down Tokens`, `VIP Feeder`, or `Custom Role`."
            )
            return
        save_store_cost_override(
            ctx.guild.id,
            item_key,
            new_cost,
            server_name=ctx.guild.name,
            set_by=ctx.author.id,
        )
        item_info = get_store_item_info(item_key)
        await ctx.reply(f"Updated `{item_info['display_name']}` to `{new_cost}` Feederbucks for this server.")

    @bot.command(name="dd_tokens")
    async def dd_tokens(ctx, member: discord.Member = None):
        member = member or ctx.author
        guild_id = str(ctx.guild.id)
        user_id = str(member.id)
        count = get_dd_token_balance(guild_id, user_id, nickname=member.display_name)
        await ctx.reply(f"{member.display_name} has `{count}` dd_tokens.")

    @bot.command(name="dd")
    async def double_down(ctx):
        guild_id = ctx.guild.id
        user_id = str(ctx.author.id)
        nickname = ctx.author.display_name
        if guild_id not in active_match_ids:
            await ctx.reply("There is no active inhouse match right now. You can only use `!dd` during an active match.")
            return
        if random_polling_flags.get(guild_id, False):
            await ctx.reply("`!dd` cannot be used during random public match polling because inhouse MMR is not being adjusted.")
            return
        match = await fetch_live_match_for_guild(guild_id, random_mode=False)
        if not match:
            await ctx.reply("Could not verify a live inhouse match right now.")
            return
        duration = match.get("scoreboard", {}).get("duration", 0)
        if duration >= 120:
            await ctx.reply("Double Down is closed. The match has passed the 2:00 mark.")
            return
        if has_active_double_down(guild_id, user_id):
            await ctx.reply("You already activated your double down for this match.")
            return
        token_count = get_dd_token_balance(guild_id, user_id, nickname=nickname)
        if token_count <= 0:
            await ctx.reply("You do not have any dd_tokens. Buy one first with `!buy 1 1`.")
            return
        update_dd_token_balance(guild_id, user_id, -1, nickname=nickname)
        activate_double_down(guild_id, user_id, nickname=nickname)
        remaining = get_dd_token_balance(guild_id, user_id, nickname=nickname)
        await ctx.reply(
            f"{ctx.author.display_name} activated **Double Down** for this match.\n"
            f"Your inhouse MMR gain/loss will be doubled for this match.\n"
            f"Remaining dd_tokens: `{remaining}`"
        )

    @bot.command(name="send")
    async def send_feederbucks(ctx, first: str = None, second: str = None):
        if first is None or second is None:
            await ctx.reply("Usage: !send `<amount>` `<@user>` or `!send <@user> <amount>`")
            return
        converter = commands.MemberConverter()
        amount = None
        member = None
        try:
            amount = int(first)
            member = await converter.convert(ctx, second)
        except (ValueError, commands.BadArgument):
            try:
                amount = int(second)
                member = await converter.convert(ctx, first)
            except (ValueError, commands.BadArgument):
                await ctx.reply("Invalid argument. Usage: !send `<amount>` `<@user>` or `!send <@user> <amount>`.")
                return
        if amount <= 0:
            await ctx.reply("Amount must be greater than 0.")
            return
        sender_id = str(ctx.author.id)
        receiver_id = str(member.id)
        guild_id = str(ctx.guild.id)
        sender_balance = get_balance(guild_id, sender_id, nickname=ctx.author.display_name)
        if sender_id == receiver_id:
            await ctx.reply("You cannot send Feederbucks to yourself.")
            return
        if sender_balance < amount:
            await ctx.reply(f"You do not have enough Feederbucks. Your balance is `{sender_balance}`.")
            return
        update_balance(guild_id, sender_id, -amount)
        update_balance(guild_id, receiver_id, amount)
        await ctx.reply(f"{ctx.author.display_name} sent `{amount}` Feederbucks to {member.display_name}.\nYour new balance: `{get_balance(guild_id, sender_id)}`.")
    @send_feederbucks.error
    async def send_feederbucks_error(ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply("Usage: !send `<amount>` `<@user>` or `!send <@user> <amount>`")
        elif isinstance(error, commands.BadArgument):
            await ctx.reply("Invalid argument. Usage: !send `<amount>` `<@user>` or `!send <@user> <amount>`.")
        else:
            await ctx.reply("An unexpected error occurred while sending Feederbucks.")

    @bot.command(name="setpreferredroles")
    async def set_preferred_roles(ctx, r1: int, r2: int, r3: int, r4: int, r5: int, member: discord.Member = None):
        roles = [r1, r2, r3, r4, r5]
        if sorted(roles) != [1, 2, 3, 4, 5]:
            await ctx.reply("Usage: !setpreferredroles '1 2 3 4 5' (enter each role starting from most preferred going to least preferred).")
            return
        target = member or ctx.author
        if member and member != ctx.author:
            if not await user_is_admin_or_has_role(ctx.author):
                await ctx.reply("You do not have permission to set roles for other users. Only admins or Inhouse Admins can do that.")
                return
        user_id = str(target.id)
        doc_ref = db.collection("players").document(user_id)
        doc_ref.set({"preferred_roles": roles}, merge=True)
        if target == ctx.author:
            await ctx.reply(f"{ctx.author.mention}, your preferred roles have been saved: {roles}")
        else:
            await ctx.reply(f"{ctx.author.mention} set preferred roles for {target.mention}: {roles}")
    @set_preferred_roles.error
    async def set_preferred_roles_error(ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply("You must provide 5 role numbers in order of most preferred to least preferred, **spaced out**.\nExample: `!setpreferredroles 3 2 4 5 1`")
        elif isinstance(error, commands.BadArgument):
            await ctx.reply("Invalid input. Make sure the first five values are numbers (1–5), followed optionally by a valid @user mention.")
        elif isinstance(error, commands.UserInputError):
            await ctx.reply("Incorrect usage. Try: `!setpreferredroles 1 2 3 4 5` or `!setpreferredroles 1 2 3 4 5 @user`")
        else:
            await ctx.reply("An unexpected error occurred while processing your preferred roles.")
            raise error

    @bot.command(name="viewpreferredroles")
    async def view_preferred_roles(ctx, member: discord.Member = None):
        target = member or ctx.author
        user_id = str(target.id)
        doc_ref = db.collection("players").document(user_id)
        doc = doc_ref.get()
        if not doc.exists or "preferred_roles" not in doc.to_dict():
            await ctx.reply(f"{target.display_name} has not set their preferred roles.")
            return
        preferred_roles = doc.to_dict()["preferred_roles"]
        formatted = ", ".join(f"Pos {r}" for r in preferred_roles)
        await ctx.reply(f"{target.mention}'s preferred roles (most to least preferred): {formatted}")
    @view_preferred_roles.error
    async def view_preferred_roles_error(ctx, error):
        if isinstance(error, commands.BadArgument):
            await ctx.reply("Invalid user. Make sure to mention a valid user in the server.")
        else:
            await ctx.reply("An unexpected error occurred while fetching preferred roles.")
            raise error

    # ========================== Lobby Management Commands =========================

    @bot.command(name="add")
    @is_admin_or_has_role()
    async def add_to_lobby(ctx, *args):
        if not args:
            await ctx.reply("Usage: !add `@player1` `[@player2 ...]` OR `!add <placeholder_name> <mmr>`")
            return
        guild_id = ctx.guild.id
        if guild_id not in lobby_players:
            lobby_players[guild_id] = []
        if len(lobby_players[guild_id]) >= 10:
            await ctx.reply("Lobby is already full. Cannot add more players.")
            return
        added = []
        channel = ctx.channel
        message = None
        if guild_id in lobby_message:
            try:
                message = await channel.fetch_message(lobby_message[guild_id].id)
            except:
                message = None
        # -------------------------------
        # Placeholder mode: !add name mmr
        # -------------------------------
        if len(args) == 2 and not ctx.message.mentions:
            placeholder_name = args[0].strip()
            mmr_raw = args[1].strip()
            if not placeholder_name:
                await ctx.reply("Placeholder name cannot be empty.")
                return
            try:
                placeholder_mmr = int(mmr_raw)
            except ValueError:
                await ctx.reply("Placeholder MMR must be a number.")
                return
            placeholder_id = f"placeholder:{placeholder_name.lower()}"
            if any(str(uid) == placeholder_id for uid, _, _ in lobby_players[guild_id]):
                await ctx.reply("That placeholder is already in the lobby.")
                return
            if any(existing_name.lower() == placeholder_name.lower() for _, existing_name, _ in lobby_players[guild_id]):
                await ctx.reply("That name is already in the lobby.")
                return
            lobby_players[guild_id].append((placeholder_id, placeholder_name, placeholder_mmr))
            added.append(f"{placeholder_name} ({placeholder_mmr})")
        # -------------------------------
        # Normal mention mode: !add @user
        # -------------------------------
        else:
            members = ctx.message.mentions
            if not members:
                await ctx.reply("Usage: !add `@player1` `[@player2 ...]` OR `!add <placeholder_name> <mmr>`")
                return
            for member in members:
                if any(str(uid) == str(member.id) for uid, _, _ in lobby_players[guild_id]):
                    continue
                mmr = get_mmr(member)
                display_name = member.display_name
                lobby_players[guild_id].append((member.id, display_name, mmr))
                added.append(f"{display_name} ({mmr})")

                if len(lobby_players[guild_id]) >= 10:
                    break
        if added:
            await full_post_rocket_reset(guild_id, message)
            save_lobby_players(guild_id, lobby_players[guild_id])
            await update_lobby_embed(ctx.guild)
        else:
            await ctx.reply("No new members were added.")
    @add_to_lobby.error
    async def add_to_lobby_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")
        else:
            await ctx.reply("An unexpected error occurred while adding players to the lobby.")

    @bot.command(name="remove")
    @is_admin_or_has_role()
    async def remove_from_lobby(ctx, *args):
        if not args:
            await ctx.reply("Usage: !remove `@player1` `[@player2 ...]` OR `!remove <placeholder_name>`")
            return
        guild_id = ctx.guild.id
        removed = []
        if guild_id not in lobby_players:
            await ctx.reply("There is no lobby for this server yet.")
            return
        channel = ctx.channel
        message = None
        if guild_id in lobby_message:
            try:
                message = await channel.fetch_message(lobby_message[guild_id].id)
            except:
                message = None
        # -------------------------------
        # Placeholder mode: !remove name
        # -------------------------------
        if len(args) == 1 and not ctx.message.mentions:
            target_name = args[0].strip().lower()

            for i, (uid, name, _) in enumerate(lobby_players[guild_id]):
                if is_placeholder_player(uid) and name.lower() == target_name:
                    del lobby_players[guild_id][i]
                    removed.append(name)
                    break
        # -------------------------------
        # Normal mention mode: !remove @user
        # -------------------------------
        else:
            members = ctx.message.mentions
            if not members:
                await ctx.reply("Usage: !remove `@player1` `[@player2 ...]` OR `!remove <placeholder_name>`")
                return
            for member in members:
                for i, (uid, _, _) in enumerate(lobby_players[guild_id]):
                    if str(uid) == str(member.id):
                        del lobby_players[guild_id][i]
                        removed.append(member.display_name)
                        break
        if removed:
            await full_post_rocket_reset(guild_id, message)
            save_lobby_players(guild_id, lobby_players[guild_id])
            await update_lobby_embed(ctx.guild)
        else:
            await ctx.reply("None of the specified players were in the lobby.")
        @remove_from_lobby.error
        async def remove_from_lobby_error(ctx, error):
            if isinstance(error, commands.CheckFailure):
                await ctx.reply("You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")
            else:
                await ctx.reply("An unexpected error occurred while removing players from the lobby.")

    @bot.command(name="replace")
    @is_admin_or_has_role()
    async def replace_in_lobby(ctx, *args):
        if not args:
            await ctx.reply(
                "Usage:\n"
                "`!replace @olduser @newuser`\n"
                "`!replace @olduser <placeholder_name> <mmr>`\n"
                "`!replace <placeholder_name> @newuser`\n"
                "`!replace <old_placeholder> <new_placeholder> <mmr>`"
            )
            return
        guild_id = ctx.guild.id
        if guild_id not in lobby_players:
            await ctx.reply("There is no lobby for this server yet.")
            return
        channel = ctx.channel
        message = None
        if guild_id in lobby_message:
            try:
                message = await channel.fetch_message(lobby_message[guild_id].id)
            except Exception:
                message = None
        current_players = lobby_players[guild_id]
        def parse_member_token(token: str):
            match = re.fullmatch(r"<@!?(\d+)>", token.strip())
            if not match:
                return None
            member_id = int(match.group(1))
            return ctx.guild.get_member(member_id)
        # -------------------------------
        # Parse OLD target from args[0]
        # -------------------------------
        old_token = args[0].strip()
        old_member = parse_member_token(old_token)
        old_target_uid = None
        old_target_name = None
        old_index = None
        if old_member:
            for i, (uid, name, mmr) in enumerate(current_players):
                if str(uid) == str(old_member.id):
                    old_target_uid = uid
                    old_target_name = name
                    old_index = i
                    break
        else:
            old_placeholder_name = old_token.lower()
            for i, (uid, name, mmr) in enumerate(current_players):
                if is_placeholder_player(uid) and name.lower() == old_placeholder_name:
                    old_target_uid = uid
                    old_target_name = name
                    old_index = i
                    break
        if old_index is None:
            await ctx.reply("The player or placeholder you want to replace is not in the lobby.")
            return
        # -------------------------------
        # Parse NEW target from remaining args
        # -------------------------------
        if len(args) < 2:
            await ctx.reply(
                "Usage:\n"
                "`!replace @olduser @newuser`\n"
                "`!replace @olduser <placeholder_name> <mmr>`\n"
                "`!replace <placeholder_name> @newuser`\n"
                "`!replace <old_placeholder> <new_placeholder> <mmr>`"
            )
            return
        new_token = args[1].strip()
        new_member = parse_member_token(new_token)
        # Case 1: replacing with a mentioned user
        if new_member:
            if len(args) != 2:
                await ctx.reply("Usage: `!replace @olduser @newuser` or `!replace <placeholder_name> @newuser`")
                return
            if any(str(uid) == str(new_member.id) for uid, _, _ in current_players if str(uid) != str(old_target_uid)):
                await ctx.reply("That replacement user is already in the lobby.")
                return
            new_tuple = (new_member.id, new_member.display_name, get_mmr(new_member))
        # Case 2: replacing with a placeholder
        else:
            if len(args) != 3:
                await ctx.reply(
                    "Usage for placeholder replacement:\n"
                    "`!replace @olduser <placeholder_name> <mmr>`\n"
                    "`!replace <old_placeholder> <new_placeholder> <mmr>`"
                )
                return
            placeholder_name = new_token
            mmr_raw = args[2].strip()
            if not placeholder_name:
                await ctx.reply("Placeholder name cannot be empty.")
                return
            try:
                placeholder_mmr = int(mmr_raw)
            except ValueError:
                await ctx.reply("Placeholder MMR must be a number.")
                return
            placeholder_id = f"placeholder:{placeholder_name.lower()}"
            if any(str(uid) == placeholder_id for uid, _, _ in current_players if str(uid) != str(old_target_uid)):
                await ctx.reply("That replacement placeholder is already in the lobby.")
                return
            if any(name.lower() == placeholder_name.lower() for uid, name, _ in current_players if str(uid) != str(old_target_uid)):
                await ctx.reply("That name is already in the lobby.")
                return
            new_tuple = (placeholder_id, placeholder_name, placeholder_mmr)
        # -------------------------------
        # Perform replacement
        # -------------------------------
        current_players[old_index] = new_tuple
        await full_post_rocket_reset(guild_id, message)
        save_lobby_players(guild_id, current_players)
        await update_lobby_embed(ctx.guild)
        await ctx.reply(
            f"Replaced **{old_target_name}** with **{new_tuple[1]}** in the lobby.",
            delete_after=8,
        )
    @replace_in_lobby.error
    async def replace_in_lobby_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")
        else:
            await ctx.reply("An unexpected error occurred while replacing a player in the lobby.")

    @bot.command(name="lobby")
    @is_admin_or_has_role()
    async def lobby_cmd(ctx, mode: str = None):
        guild_id = ctx.guild.id
        existing_players = lobby_players.get(guild_id, [])
        if mode:
            selected_mode = mode.lower() if mode.lower() in ["regular", "immortal"] else "regular"
            save_inhouse_mode_for_guild(guild_id, selected_mode, server_name=ctx.guild.name, set_by=str(ctx.author))
        else:
            selected_mode = load_inhouse_mode_for_guild(guild_id)
        inhouse_mode[guild_id] = selected_mode
        if guild_id not in lobby_players:
            lobby_players[guild_id] = existing_players
        """try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass"""
        await full_post_rocket_reset(guild_id)
        if guild_id in lobby_message:
            try:
                await lobby_message[guild_id].delete()
            except discord.NotFound:
                pass
        embed = build_lobby_embed(ctx.guild, selected_mode)
        target_channel = get_lobby_channel_for_guild(ctx.guild) or ctx.channel
        message = await target_channel.send(embed=embed)
        lobby_message[guild_id] = message
        save_lobby_message_id(guild_id, message.id)
        save_lobby_players(guild_id, lobby_players[guild_id])
        await message.add_reaction("👍")
        await message.add_reaction("👎")
        if len(lobby_players[guild_id]) == 10:
            await message.add_reaction("🚀")
    @lobby_cmd.error
    async def lobby_cmd_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")
        else:
            await ctx.reply("An unexpected error occurred while creating or refreshing the lobby.")

    @bot.command(name="reset")
    @is_admin_or_has_role()
    async def reset(ctx, *args):
        if args:
            await ctx.reply("Usage: !reset (no extra arguments allowed)")
            return
        guild_id = ctx.guild.id
        lobby_players[guild_id] = []
        await full_post_rocket_reset(guild_id)
        try:
            if guild_id in lobby_message:
                await lobby_message[guild_id].delete()
        except discord.NotFound:
            pass
        embed = build_lobby_embed(ctx.guild)
        target_channel = get_lobby_channel_for_guild(ctx.guild) or ctx.channel
        message = await target_channel.send(embed=embed)
        lobby_message[guild_id] = message
        save_lobby_message_id(guild_id, message.id)
        save_lobby_players(guild_id, lobby_players[guild_id])
        await message.add_reaction("👍")
        await message.add_reaction("👎")
    @reset.error
    async def reset_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")
        else:
            await ctx.reply("An unexpected error occurred while resetting the lobby.")
    
    @commands.cooldown(1, 30, commands.BucketType.guild)
    @bot.command(name="livematch")
    async def livematch_cmd(ctx):
        guild_id = ctx.guild.id
        match_id = active_match_ids.get(guild_id)
        if not match_id:
            await ctx.reply("There is no active match to display.")
            return
        is_random = random_polling_flags.get(guild_id, False)
        match = await fetch_live_match_for_guild(guild_id, random_mode=is_random)
        if not match:
            await ctx.reply("Could not retrieve live match info.")
            return
        prev_msg = live_embed_messages.get(guild_id)
        channel = ctx.channel
        if prev_msg:
            try:
                await prev_msg.delete()
            except Exception:
                pass
        embed = await format_live_match_embed(match, ctx.guild)
        new_msg = await channel.send(embed=embed)
        live_embed_messages[guild_id] = new_msg
    @livematch_cmd.error
    async def livematch_cmd_error(ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            wait_time = math.ceil(error.retry_after)  # round up
            await ctx.reply(f"You must wait {wait_time} seconds before using `!livematch` again.")
            return
        await ctx.reply("An error occurred while recalling the live match embed.")

    # ============================= Admin-Only Commands ============================

    @bot.command(name="setmmr")
    @is_admin_or_has_role()
    async def setmmr(ctx, mmr: int, member: discord.Member):
        if mmr < 0 or mmr > 20000:
            await ctx.reply("Invalid MMR value. Please provide a value between 0 and 20000.")
            return
        if member not in ctx.guild.members:
            await ctx.reply("That user is not in this server.")
            return
        try:
            user_ref = db.collection("players").document(str(member.id))
            user_ref.set({"mmr": mmr}, merge=True)
            await ctx.reply(f"{member.mention}'s MMR has been manually set to **{mmr}**.")
            await refresh_lobby_member_mmr(ctx.guild, member, mmr)
        except Exception as e:
            await ctx.reply(f"Failed to set MMR due to an error: {e}")
    @setmmr.error
    async def set_mmr_error(ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply("Usage: !setmmr `<mmr>` `<@user>`")
        elif isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")

    @bot.command(name="alert")
    @is_admin_or_has_role()
    async def alert(ctx):
        guild = ctx.guild
        guild_id = guild.id
        if guild_id not in lobby_players or len(lobby_players[guild_id]) != 10:
            await ctx.reply("We do not have 10 players in the lobby yet.")
            return
        mentions = []
        for user_id, _, _ in lobby_players[guild_id]:
            member = guild.get_member(user_id)
            if member:
                mentions.append(member.mention)
        if mentions:
            await ctx.reply(f"{' '.join(mentions)} lobby up.")
        else:
            await ctx.reply("Could not find any users to alert.")
    @alert.error
    async def alert_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")

    @bot.command(name="setpassword")
    @is_admin_or_has_role()
    async def set_password(ctx, *, new_password: str):
        save_lobby_password_for_guild(ctx.guild.id, new_password, server_name=ctx.guild.name, set_by=str(ctx.author))
        await update_lobby_embed(ctx.guild)
        await ctx.reply(f"Password updated to: `{new_password}`")
    @set_password.error
    async def set_password_error(ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply("Usage: !setpassword `<new_password>`")
        elif isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")

    @bot.command(name="changeprefix")
    @is_admin_or_has_role()
    async def change_prefix(ctx, new_prefix: str):
        old_prefix = load_guild_prefix(ctx.guild.id)
        save_guild_prefix(ctx.guild.id, new_prefix, server_name=ctx.guild.name, set_by=str(ctx.author))
        await ctx.reply(f"Command prefix changed from `{old_prefix}` to `{new_prefix}` for this server.")
    @change_prefix.error
    async def change_prefix_error(ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply("Usage: !changeprefix `<new_prefix>`")
        elif isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to change the prefix. You must be a server admin or have the 'Inhouse Admin' role.")

    @commands.cooldown(1, 60, commands.BucketType.guild)
    @bot.command(name="viewlogs")
    @is_admin_or_has_role()
    async def viewlogs(ctx, *, flags: str = ""):
        guild_id = ctx.guild.id
        guild_name = ctx.guild.name
        verbose = '--verbose' in (flags or "").lower()
        doc = db.collection("guild_specific_info").document(str(guild_id)).get()
        lines = []
        if verbose:
            lines.append(f"**Admin Logs (Verbose)** for `{guild_name}` (Guild ID: `{guild_id}`)")
        else:
            lines.append(f"**Admin Logs for `{guild_name}`**")
        if doc.exists:
            data = doc.to_dict()
            prefix_data = data.get("prefix", {})
            prefix = prefix_data.get("prefix", "Unknown")
            prefix_set_by = prefix_data.get("prefix_set_by", "Unknown")
            prefix_time = prefix_data.get("prefix_timestamp", "Unknown")
            if verbose:
                lines.append(f"**Prefix**:\n  • Value: `{prefix}`\n  • Set by: {prefix_set_by}\n  • Timestamp: `{prefix_time}`\n  • Full Doc: `{prefix_data}`")
            else:
                lines.append(f"**Prefix**: `{prefix}`\nSet by: {prefix_set_by}\nTime: {prefix_time}")
            password_data = data.get("password", {})
            password = password_data.get("password", "Unknown")
            password_set_by = password_data.get("password_set_by", "Unknown")
            password_time = password_data.get("password_timestamp", "Unknown")
            if verbose:
                lines.append(f"\n**Lobby Password**:\n  • Value: `{password}`\n  • Set by: {password_set_by}\n  • Timestamp: `{password_time}`\n  • Full Doc: `{password_data}`")
            else:
                lines.append(f"\n**Lobby Password**: `{password}`\nSet by: {password_set_by}\nTime: {password_time}")
            inhouse_mode_data = data.get("inhouse_mode", {})
            mode = inhouse_mode_data.get("mode", "Unknown")
            mode_set_by = inhouse_mode_data.get("mode_set_by", "Unknown")
            mode_time = inhouse_mode_data.get("mode_timestamp", "Unknown")
            if verbose:
                lines.append(f"\n**Inhouse Mode**:\n  • Value: `{mode}`\n  • Set by: {mode_set_by}\n  • Timestamp: `{mode_time}`\n  • Full Doc: `{inhouse_mode_data}`")
            else:
                lines.append(f"\n**Inhouse Mode**: `{mode}`\nSet by: {mode_set_by}\nTime: {mode_time}")
            league_id_data = data.get("league_id", {})
            bound_league_id = league_id_data.get("bound_league_id", "Unknown")
            league_bind_by = league_id_data.get("league_id_bound_by", "Unknown")
            league_bind_time = league_id_data.get("league_bind_timestamp", "Unknown")
            if verbose:
                lines.append(f"\n**League ID**:\n  • Value: `{bound_league_id}`\n  • Bound by: {league_bind_by}\n  • Timestamp: `{league_bind_time}`\n  • Full Doc: `{league_id_data}`")
            else:
                lines.append(f"\n**League ID**: `{bound_league_id}`\nBound by: {league_bind_by}\nTime: {league_bind_time}")
            live_channel_data = data.get("live_channel_id", {})
            live_channel_id = live_channel_data.get("live_channel_id", "Unknown")
            live_channel_time = live_channel_data.get("live_channel_timestamp", "Unknown")
            live_channel_set_by = live_channel_data.get("bound_by", "Unknown")
            if verbose:
                lines.append(f"\n**Live Channel ID**:\n  • Value: `{live_channel_id}`\n  • Set by: {live_channel_set_by}\n  • Timestamp: `{live_channel_time}`\n  • Full Doc: `{live_channel_data}`")
            else:
                lines.append(f"\n**Live Channel ID**: `{live_channel_id}`\nSet by: {live_channel_set_by}\nTime: {live_channel_time}")
            lobby_channel_data = data.get("lobby_channel_id", {})
            lobby_channel_id = lobby_channel_data.get("lobby_channel_id", "Unknown")
            lobby_channel_time = lobby_channel_data.get("lobby_channel_timestamp", "Unknown")
            lobby_channel_set_by = lobby_channel_data.get("bound_by", "Unknown")
            if verbose:
                lines.append(f"\n**Lobby Channel ID**:\n  • Value: `{lobby_channel_id}`\n  • Set by: {lobby_channel_set_by}\n  • Timestamp: `{lobby_channel_time}`\n  • Full Doc: `{lobby_channel_data}`")
            else:
                lines.append(f"\n**Lobby Channel ID**: `{lobby_channel_id}`\nSet by: {lobby_channel_set_by}\nTime: {lobby_channel_time}")
            preferred_roles_setting_data = data.get("preferred_roles_setting", {})
            preferred_roles_enabled = preferred_roles_setting_data.get("preferred_roles_enabled", True)
            preferred_roles_set_by = preferred_roles_setting_data.get("preferred_roles_set_by", "Unknown")
            preferred_roles_time = preferred_roles_setting_data.get("preferred_roles_timestamp", "Unknown")
            if verbose:
                lines.append(f"\n**Preferred Roles Integration**:\n  • Status: {'✅ Enabled' if preferred_roles_enabled else '❌ Disabled'}\n  • Set by: {preferred_roles_set_by}\n  • Timestamp: {preferred_roles_time}\n  • Field: preferred_roles_enabled = {preferred_roles_enabled}")
            else:
                lines.append(f"\n**Preferred Roles Integration**: {'✅ Enabled' if preferred_roles_enabled else '❌ Disabled'}\n Set by: {preferred_roles_set_by}\n Time: {preferred_roles_time}")
            # --- Captain Policy (policy + optional threshold) ---
            captain_data = data.get("captain_policy", {}) or {}
            # Values as stored by set_captain_policy()
            pol = captain_data.get("captain_policy")              # "min_diff" | "top2_if_close" | "simulate"
            thr = captain_data.get("captain_policy_threshold")    # int or None
            pol_set_by = captain_data.get("captain_policy_set_by", "Unknown")
            pol_time = captain_data.get("captain_policy_timestamp", "Unknown")
            # Fallback to runtime getter (provides default "min_diff" when not set)
            if pol is None:
                try:
                    pol, thr = get_captain_policy(guild_id)
                except Exception:
                    pol, thr = "min_diff", None
            # Build one-line summary + extras
            threshold_note = f" (threshold {thr})" if pol == "top2_if_close" and thr is not None else ""
            if verbose:
                lines.append(
                    f"\n**Captain Policy**:\n  • Value: `{pol}`{threshold_note}\n  • Set by: {pol_set_by}\n  • Timestamp: `{pol_time}`\n  • Full Doc: `{captain_data}`")
            else:
                lines.append(f"\n**Captain Policy**: `{pol}`{threshold_note}\n Set by: {pol_set_by}\n Time: {pol_time}")
            betting = get_betting_settings(guild_id)
            betting_data = betting.get("full_doc", {}) or {}
            betting_mode = betting.get("mode", BETTING_MODE_CLASSIC)
            betting_label = "Prize Pool" if betting_mode == BETTING_MODE_POOL else "Classic"
            prop_status = "Enabled" if betting.get("prop_markets_enabled") else "Disabled"
            carryover = betting.get("carryover_jackpot", 0)
            if verbose:
                lines.append(
                    f"\n**Betting Settings**:\n"
                    f"  - Mode: `{betting_mode}` ({betting_label})\n"
                    f"  - Prop Markets: `{prop_status}`\n"
                    f"  - Carryover Jackpot: `{carryover}`\n"
                    f"  - Set by: {betting.get('mode_set_by', 'Unknown')}\n"
                    f"  - Timestamp: `{betting.get('mode_timestamp', 'Unknown')}`\n"
                    f"  - Full Doc: `{betting_data}`"
                )
            else:
                lines.append(
                    f"\n**Betting Settings**: `{betting_label}`\n"
                    f"Prop Markets: {prop_status}\n"
                    f"Carryover Jackpot: `{carryover}`"
                )
        else:
            lines.append("No Firestore data found for this guild.")
        await ctx.reply("\n".join(lines))
    @viewlogs.error
    async def viewlogs_error(ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            wait = math.ceil(error.retry_after)
            await ctx.reply(f"Please wait {wait}s before using `!viewlogs` again.")
        elif isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")
        else:
            await ctx.reply("An unexpected error occurred while retrieving the logs.")

    @bot.command(name="submitmatch")
    @is_admin_or_has_role()
    async def submitmatch(ctx, match_id: str):
        await ctx.reply("Processing submitted match...")
        if not match_id.isdigit():
            await ctx.reply("Match ID must be a number.")
            return
        if is_match_processed(ctx.guild.id, match_id):
            existing = get_processed_match(ctx.guild.id, match_id) or {}
            await ctx.reply(
                f"Match `{match_id}` was already processed earlier "
                f"(source: `{existing.get('source', 'unknown')}`)."
            )
            return
        result = fetch_match_result(match_id)
        if not result:
            await ctx.reply("Could not fetch match result. Check the match ID.")
            return
        player_stats = build_ledger_player_stats(ctx.guild, result.get("player_stats", []))
        winner_ids = map_steam_ids_to_discord_ids(result["radiantplayers"] if result["radiant_win"] else result["direplayers"])
        loser_ids = map_steam_ids_to_discord_ids(result["direplayers"] if result["radiant_win"] else result["radiantplayers"])
        winning_team = "radiant" if result["radiant_win"] else "dire"
        doubled_user_ids = get_active_double_down_users(ctx.guild.id)
        mmr_changes = await adjust_mmr(bot, winner_ids, loser_ids, ctx.guild.id, doubled_user_ids=doubled_user_ids)
        clear_active_double_downs(ctx.guild.id)
        bet_results = resolve_bets(ctx.guild.id, winning_team, match_id=match_id, match_result=result)
        clear_guild_bets(ctx.guild.id)
        all_player_ids = winner_ids + loser_ids
        feederbucks_awards = []
        for discord_id in all_player_ids:
            try:
                member = ctx.guild.get_member(int(discord_id))
                nickname = member.display_name if member else str(discord_id)
                balance_after = update_balance(ctx.guild.id, discord_id, 50, nickname=nickname)
                feederbucks_awards.append(build_feederbucks_award(
                    f"participation_{discord_id}",
                    discord_id,
                    nickname,
                    50,
                    "Participation",
                    balance_after=balance_after,
                ))
            except Exception as e:
                print(f"[ERROR] Failed to award Feederbucks to user {discord_id}: {e}")
        try:
            log_processed_match(
                ctx.guild.id,
                match_id,
                league_id=get_bound_league_id(ctx.guild.id),
                source="submitmatch",
                processors=["betting", "inhouse_mmr", "feederbucks"],
                winning_team=winning_team,
                random_mode=False,
                processed_by=str(ctx.author),
                player_count=len(all_player_ids),
            )
        except Exception as e:
            print(f"[submitmatch] Failed to log processed match {match_id}: {e}")
        try:
            log_match_ledger(
                ctx.guild.id,
                match_id,
                league_id=get_bound_league_id(ctx.guild.id),
                source="submitmatch",
                winning_team=winning_team,
                random_mode=False,
                processed_by=str(ctx.author),
                mmr_changes=mmr_changes,
                bet_results=bet_results,
                player_stats=player_stats,
                feederbucks_awards=feederbucks_awards,
            )
        except Exception as e:
            print(f"[submitmatch] Failed to log ledger entry for match {match_id}: {e}")
        try:
            schedule_match_imp_enrichment(
                ctx.guild.id,
                match_id,
                channel_id=ctx.channel.id,
                notify_on_success=True,
            )
        except Exception as e:
            print(f"[submitmatch] Failed to schedule IMP enrichment for match {match_id}: {e}")
        await ctx.reply(f"Match submitted. `{winning_team.capitalize()}` won. MMRs and bets updated.\nAll participants received **50 Feederbucks** for playing.")
    @submitmatch.error
    async def submitmatch_error(ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply("Usage: !submitmatch `<match_id>`")
        elif isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")
        elif isinstance(error, commands.BadArgument):
            await ctx.reply("Invalid match ID. It should be a numeric string like `8351234567`.")
        else:
            await ctx.reply("An unexpected error occurred while submitting the match.")

    @bot.command(name="bindleague")
    @is_admin_or_has_role()
    async def bind_league_to_guild(ctx, league_id: str):
        save_league_guild_mapping(ctx.guild.id, league_id, server_name=ctx.guild.name, bound_by=str(ctx.author))
        await ctx.reply(f"League `{league_id}` bound to this server (Guild ID: `{ctx.guild.id}`).")
    @bind_league_to_guild.error
    async def bindleague_error(ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply("Usage: !bindleague `<league_id>`")
        elif isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")
        else:
            await ctx.reply("An unexpected error occurred while binding the league.")

    @bot.command(name="setlivechannel")
    @is_admin_or_has_role()
    async def set_live_channel(ctx):
        channel_id = ctx.channel.id
        data = {
            "live_channel_id": str(channel_id),
            "live_channel_timestamp": firestore.SERVER_TIMESTAMP,
            "bound_by": str(ctx.author),
        }
        doc_ref = db.collection("guild_specific_info").document(str(ctx.guild.id))
        doc_ref.set({"live_channel_id": data}, merge=True)
        live_channel_ids[ctx.guild.id] = channel_id
        await ctx.reply(f"This channel has been set to receive live match updates.")
    @set_live_channel.error
    async def setlivechannel_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")
        else:
            await ctx.reply("An unexpected error occurred while setting the live channel.")

    @bot.command(name="startpolling")
    @is_admin_or_has_role()
    async def start_polling(ctx):
        channel = ctx.channel
        match = await fetch_live_match_for_guild(ctx.guild.id, random_mode=False)
        if match:
            match_id = match.get("match_id")
            if ctx.guild.id not in polling_tasks:
                active_match_ids[ctx.guild.id] = match_id
                polling_tasks[ctx.guild.id] = asyncio.create_task(poll_live_match(match_id, ctx.guild, random_mode=False))
                await channel.send(f"Started match polling for match ID {match_id} in guild {ctx.guild.name}")
                random_polling_flags[ctx.guild.id] = False
        else:
            await channel.send("No live match found for the bound league.")
    @start_polling.error
    async def start_polling_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")

    @bot.command(name="stoppolling")
    @is_admin_or_has_role()
    async def stop_polling(ctx):
        guild_id = ctx.guild.id
        # Cancel polling task
        if guild_id in polling_tasks and not polling_tasks[guild_id].done():
            polling_tasks[guild_id].cancel()
            polling_tasks.pop(ctx.guild.id, None)
        # CLEAR ALL MATCH STATE
        active_match_ids.pop(guild_id, None)
        random_polling_flags.pop(guild_id, None)
        match_tracking_start_times.pop(guild_id, None)
        live_embed_messages.pop(guild_id, None)

        await ctx.reply("Stopped polling and cleared active match state for this server.")
    @stop_polling.error
    async def stop_polling_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")

    @bot.command(name="randompoll")
    @is_admin_or_has_role()
    async def random_poll(ctx):
        channel = ctx.channel
        match = await fetch_live_match_for_guild(ctx.guild.id, random_mode=True)
        if match:
            match_id = match.get("match_id")
            if ctx.guild.id not in polling_tasks:
                active_match_ids[ctx.guild.id] = match_id
                polling_tasks[ctx.guild.id] = asyncio.create_task(poll_live_match(match_id, ctx.guild, random_mode=True))
                await channel.send(f"Started random match polling for match ID {match_id} in guild {ctx.guild.name}")
                random_polling_flags[ctx.guild.id] = True
                match_tracking_start_times[ctx.guild.id] = time.time()
            else:
                await channel.send("Polling is already running for this server.")
        else:
            await channel.send("No valid random live match found.")
    @random_poll.error
    async def random_poll_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")
        else:
            await ctx.reply("An unexpected error occurred while starting random match polling.")
            print(f"[ERROR] random_poll command: {error}")

    @bot.command(name="toggle_roles")
    @is_admin_or_has_role()
    async def toggle_preferred_roles(ctx, mode: str):
        mode = mode.lower()
        if mode not in ["on", "off"]:
            await ctx.reply("Usage: !toggle_roles `<on|off>`")
            return
        enabled = (mode == "on")
        save_preferred_roles_setting(ctx.guild.id, enabled, set_by=ctx.author)
        await update_lobby_embed(ctx.guild)
        status = "enabled" if enabled else "disabled"
        await ctx.reply(f"Preferred roles integration is now {status} for team balancing.")
    @toggle_preferred_roles.error
    async def toggle_preferred_roles_error(ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            # e.g., user typed `!toggle_roles` with no argument
            await ctx.reply(
                "Missing required argument `mode`.\n"
                "Usage: !toggle_roles `<on|off>`"
            )
        elif isinstance(error, commands.BadArgument):
            await ctx.reply(
                "Invalid argument for `mode`. Use `on` or `off`.\n"
                "Usage: !toggle_roles `<on|off>`"
            )
        elif isinstance(error, commands.UserInputError):
            await ctx.reply(
                "Incorrect usage.\n"
                "Usage: !toggle_roles `<on|off>`"
            )
        elif isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")
        else:
            await ctx.reply("An unexpected error occurred while toggling preferred roles.")
            print(f"[ERROR] toggle_preferred_roles command: {error}")
    
    @bot.command(name="lobbyroles")
    @is_admin_or_has_role()
    async def lobby_roles(ctx):
        """Show all 10 lobby players and their preferred roles. Admin-only. Only works at 10/10."""
        guild_id = ctx.guild.id
        # Must have a lobby and it must be full
        if guild_id not in lobby_players or len(lobby_players[guild_id]) != 10:
            await ctx.reply("This command only works when the lobby is full (10/10).")
            return
        # Build nice embed
        embed = discord.Embed(
            title="Preferred Roles — Current Lobby (10/10)",
            description="Most → Least preferred",
            color=discord.Color.blurple()
        )
        lines = []
        for uid, display_name, _ in lobby_players[guild_id]:
            doc = db.collection("players").document(str(uid)).get()
            prefs = None
            if doc.exists:
                data = doc.to_dict() or {}
                if isinstance(data.get("preferred_roles"), list):
                    prefs = data["preferred_roles"]
            if prefs and len(prefs) == 5 and sorted(prefs) == [1, 2, 3, 4, 5]:
                # Example format: [1 4 5 3 2]
                pretty = "[" + " ".join(str(p) for p in prefs) + "]"
                lines.append(f"• **{display_name}**: {pretty}")
            else:
                lines.append(f"• **{display_name}**: _No preferred roles set_")
        # Keep the list tidy even with long names
        embed.add_field(name="Players", value="\n".join(lines), inline=False)
        await ctx.reply(embed=embed)
    @lobby_roles.error
    async def lobby_roles_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")
        else:
            await ctx.reply("An unexpected error occurred while listing lobby roles.")
    
    @bot.command(name="pose")
    @is_global_admin()
    async def pose(ctx, member: discord.Member, *, raw: str):
        """Global admin-only: execute another command as if sent by <member>.
        Usage: !pose @user <other command and args>
        """
        if not raw.strip():
            return await ctx.reply("Usage: !pose `<@user>` `<other command with args>`")
        if raw.strip().lower().startswith(("pose ", "!pose")):
            return await ctx.reply("You can’t pose a `pose` command.")
        # Resolve dynamic prefix
        prefix = await bot.get_prefix(ctx.message)
        if isinstance(prefix, (list, tuple)):
            prefix = prefix[0]
        cmd_text = raw if raw.lstrip().startswith(prefix) else f"{prefix}{raw.lstrip()}"
        # Proxy message that spoofs BOTH author and member
        class _MessageProxy:
            __slots__ = ("_orig", "author", "member", "content")
            def __init__(self, orig: discord.Message, new_author: discord.Member, new_content: str):
                self._orig = orig
                self.author = new_author
                self.member = new_author
                self.content = new_content
            def __getattr__(self, name):
                return getattr(self._orig, name)
        proxy_msg = _MessageProxy(ctx.message, member, cmd_text)
        new_ctx = await bot.get_context(proxy_msg, cls=commands.Context)
        if not new_ctx.command:
            return await ctx.reply(f"Unknown command in `pose`: `{raw.split()[0]}`")
        # Let discord.py run checks and error handlers normally
        try:
            await bot.invoke(new_ctx)
        except commands.CheckFailure as e:
            # Re-dispatch so your existing on_command_error shows the usual message
            bot.dispatch("command_error", new_ctx, e)
        except Exception as e:
            await ctx.reply(f"Error while posing: `{e}`")
    @pose.error
    async def pose_error(ctx, error):
        """Local error handler for !pose."""
        if isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to use `!pose`. Only Sangui can use this command :)")
        elif isinstance(error, commands.BadArgument):
            await ctx.reply("Invalid member specified. Make sure to mention a valid user.")
        else:
            await ctx.reply(f"An unexpected error occurred in `!pose`: `{error}`")

    @bot.command(name="refresh_vip")
    @is_global_admin()
    async def refresh_vip(ctx):
        success, error_message, role, reassigned_count = await reset_vip_feeder_role(ctx.guild)
        if not success:
            await ctx.reply(error_message)
            return
        await ctx.reply(
            f"Recreated `{role.name}` and refreshed its role hierarchy position.\n"
            f"Role ID: `{role.id}`\n"
            f"Restored to `{reassigned_count}` active holder(s)."
        )

    @refresh_vip.error
    async def refresh_vip_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to use this command. Only Sangui can use it.")
        else:
            await ctx.reply(f"`!refresh_vip` failed: `{error}`")

    @bot.command(name="refresh_custom_roles")
    @is_global_admin()
    async def refresh_custom_roles(ctx):
        success, error_message, stats = await reset_custom_store_roles(ctx.guild)
        if not success:
            await ctx.reply(error_message)
            return
        await ctx.reply(
            "Refreshed custom store roles without changing their expiration dates.\n"
            f"Custom roles refreshed: `{stats['roles_refreshed']}`\n"
            f"Members restored: `{stats['members_restored']}`\n"
            f"Entitlements updated: `{stats['entitlements_updated']}`"
        )

    @refresh_custom_roles.error
    async def refresh_custom_roles_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to use this command. Only Sangui can use it.")
        else:
            await ctx.reply(f"`!refresh_custom_roles` failed: `{error}`")

    """@bot.command(name="immortaldraft", help="Launch the Immortal Draft using the current lobby captains and pool.")
    @is_admin_or_has_role()
    async def immortaldraft_cmd(ctx):
        try:
            await start_immortal_draft(ctx.bot, ctx.guild, ctx.channel)
        except Exception as e:
            # keep errors quiet but informative for admins
            await ctx.reply(f"Failed to start Immortal Draft: `{e}`", mention_author=False)"""
    
    @bot.command(name="captainpolicy", help="Show or set the captain selection policy: min_diff | top2_if_close [threshold] | simulate")
    @is_admin_or_has_role()
    async def captainpolicy(ctx, policy: str = None, threshold: int | None = None):
        gid = ctx.guild.id
        # Show current setting
        if policy is None:
            pol, thr = get_captain_policy(gid)
            extra = f" (threshold {thr})" if pol == "top2_if_close" and thr is not None else ""
            return await ctx.reply(
                f"Captain policy: **{pol}**{extra}\n"
                "Options: `min_diff`, `top2_if_close [threshold]`, `simulate`.",
                mention_author=False
            )
        policy = policy.lower()
        if policy not in {"min_diff", "top2_if_close", "simulate"}:
            return await ctx.reply(
                "Invalid policy. Use: `min_diff`, `top2_if_close [threshold]`, or `simulate`.",
                mention_author=False
            )
        # Handle threshold for top2_if_close
        if policy == "top2_if_close":
            try:
                threshold = 200 if threshold is None else int(threshold)
            except (TypeError, ValueError):
                return await ctx.reply("Threshold must be an integer.", mention_author=False)
        else:
            threshold = None
        # Save
        try:
            set_captain_policy(gid, policy, threshold, set_by=str(ctx.author))
        except Exception as e:
            return await ctx.reply(f"Failed to save policy: `{e}`", mention_author=False)
        # Build confirmation text first
        confirm = f"Captain policy set to **{policy}**"
        if policy == "top2_if_close":
            confirm += f" (threshold **{threshold}**)"
        applied = False
        # If we’re in Immortal mode with a full lobby, re-seed the lobby embed now
        try:
            if inhouse_mode.get(gid) == "immortal" and len(lobby_players.get(gid, [])) == 10:
                all_pairs = get_all_captain_pairs(lobby_players[gid])
                thr = threshold if threshold is not None else (get_captain_policy(gid)[1] or 200)
                chooser = deps.get("choose_captain_pair_index") or choose_captain_pair_index
                preferred_index = chooser(lobby_players[gid], all_pairs, policy=policy, threshold=thr)
                captain_draft_state[gid] = {"pairs": all_pairs, "index": preferred_index}
                captains, pool, _ = all_pairs[preferred_index]
                # reroll display uses 1-based count
                lobby_embed = build_immortal_embed(captains, pool, ctx.guild, preferred_index + 1)
                lobby_msg = lobby_message.get(gid)
                if lobby_msg:
                    await lobby_msg.edit(embed=lobby_embed)
                    applied = True
        except Exception:
            pass
        confirm += " (applied to the current lobby)." if applied else " (will take effect next time captains are generated)."
        await ctx.reply(confirm, mention_author=False)
    @captainpolicy.error
    async def captainpolicy_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")
        else:
            await ctx.reply(f"An unexpected error occurred while setting the captain policy: `{error}`")

    @bot.command(name="setlobbychannel")
    @is_admin_or_has_role()
    async def set_lobby_channel(ctx):
        guild_id = ctx.guild.id
        channel_id = ctx.channel.id
        data = {
            "lobby_channel_id": str(channel_id),
            "lobby_channel_timestamp": firestore.SERVER_TIMESTAMP,
            "bound_by": str(ctx.author),
        }
        doc_ref = db.collection("guild_specific_info").document(str(guild_id))
        doc_ref.set({"lobby_channel_id": data}, merge=True)
        lobby_channel_ids[guild_id] = channel_id
        await ctx.reply("This channel has been set as the lobby channel.")
    @set_lobby_channel.error
    async def set_lobby_channel_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.reply("You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")
        else:
            await ctx.reply("An unexpected error occurred while setting the lobby channel.")

    # ================================ Help Command ================================

    @bot.command(name="help")
    async def help_command(ctx, *, category: str = ""):
        category = category.lower().strip()
        if category == "":
            embed = discord.Embed(
                title="Available Commands",
                description=(
                    "__**General Commands**__\n"
                    "**!cfg `<steam_id>`** - Link your Steam ID to fetch your MMR from STRATZ.\n"
                    "**!setpreferredroles `<1 2 3 4 5>`** - Set your role preferences from most to least preferred.\n"
                    "**!viewpreferredroles `[@user]`** - View preferred roles for yourself or another user.\n"
                    "**!mmr `[@user]`** - Show your MMR or another user's MMR.\n"
                    "**!inhouse_mmr `[@user]`** - Show inhouse MMR for yourself or another user.\n"
                    "**!balance `[@user]`** - Show your or another user's Feederbucks balance.\n"
                    "**!leaderboard** - View top 10 inhouse MMR players in this server.\n"
                    "**!avgimp** - View the highest average IMP players in this server (minimum 4 matches).\n"
                    "**!ledger** - View the last 5 processed matches with MMR, bets, and Feederbucks awards.\n"
                    "**!topstats** - View the best recorded player stats from logged matches.\n"
                    "**!send `<amount>` `<@user>`** - Send Feederbucks to another user in the server.\n"
                    "**!livematch** - Recall and refresh the live match embed in the channel (30s cooldown).\n\n"
                   
                    "__**Betting / Store Commands**__\n"
                    "**!bets** - View active betting markets, pools, and odds.\n"
                    "**!bet `[market]` `<amt>` `<option>`** - Bet Feederbucks on an active market. Options are usually `radiant|dire`, or `over|under` for O/U prop markets.\n"
                    "**!bettingrules** - View betting integrity and payout rules.\n"
                    "**!store** - View the store.\n"
                    "**!buy `<item_number>` `<amount>` `[any additional optional parameters]`** - Buy a store item by its number from `!store`.\n"
                    "**!dd_tokens `[@user]`** - View double down token balance.\n"
                    "**!dd** - Double your inhouse MMR gain/loss for the current match.\n\n"
                    "__**Admin Commands**__\n"
                    "Use `!help admin` to see the list of admin-only commands."
                ),
                color=discord.Color.blurple()
            )
            await ctx.reply(embed=embed)
            return
        elif category == "admin":
            embed = discord.Embed(
                title="Admin Commands",
                description=(
                    "__**Player & Lobby Management**__\n"
                    "**!add `<@user1>` `<@user2>` ...** - Add one or more users to the lobby.\n"
                    "**!remove `<@user1>` `<@user2>` ...** - Remove one or more users from the lobby.\n"
                    "**!replace `<@user1|placeholder1>` `<@user2|placeholder2>`** - Replace one lobby user or placeholder with another.\n"
                    "**!lobby** - Create or refresh the inhouse lobby.\n"
                    "**!reset** - Clear the current lobby and start fresh.\n"
                    "**!cfg `<steam_id>` `[@user]` `[--force]`** - Link a player's Steam ID and fetch their MMR.\n"
                    "**!setmmr `<mmr>` `<@user>`** - Manually set a user's MMR.\n"
                    "**!setpreferredroles `<1 2 3 4 5>` `<@user>`** - Set preferred roles for another user.\n"
                    "**!alert** - Mention all 10 players when the lobby is full.\n\n"

                    "__**Lobby Configuration**__\n"
                    "**!lobby `<regular|immortal>`** - Set the inhouse mode for this server.\n"
                    "Modes: `regular` = balanced shuffle; `immortal` = immortal draft.\n"
                    "**!setpassword `<new_password>`** - Change the inhouse lobby password.\n"
                    "**!setlobbychannel** - Set current channel for lobby embeds and reset posts.\n"
                    "**!toggle_roles `<on|off>`** - Enable/disable preferred role usage in team balancing.\n"
                    "**!lobbyroles** - Show preferred roles for all 10 lobby players.\n\n"

                    "__**Bot Settings**__\n"
                    "**!changeprefix `<new_prefix>`** - Change the bot command prefix for this server.\n"
                    "**!betmode `<classic|pool>`** - Set the betting mode for future matches.\n"
                    "**!propmarkets `<on|off>`** - Enable/disable future high-risk prop markets.\n"
                    "**!voidmarket `<selector>` `[reason]`** - Void and refund suspicious betting markets.\n"
                    "Selectors: `<market>` = one market by number/name from `!bets`; `side` = all non-Match-Winner markets; `prop` = only high-risk prop markets; `all` = every active market.\n"
                    "Examples: `!voidmarket 2 suspicious first blood`, `!voidmarket prop suspected collusion`, `!voidmarket all data issue`.\n"
                    "**!modifycost `<item_index|item_name>` `<cost>`** - Override a store item cost for this server. Example: `!modifycost 2 60000`.\n"
                    "**!viewlogs** - View recent configuration logs for this server.\n"
                    "**!viewlogs --verbose** - View detailed logs with full Firestore data.\n\n"

                    "__**Captain & Draft Settings**__\n"
                    "**!captainpolicy `<policy>` `[threshold]`** - Set captain selection policy.\n\n"

                    "__**Match Tracking**__\n"
                    "**!bindleague `<league_id>`** - Bind a Steam league ID for live match tracking.\n"
                    "**!setlivechannel** - Set current channel for live match updates.\n"
                    "**!startpolling** - Start live match polling for the bound league.\n"
                    "**!stoppolling** - Stop live match polling.\n"
                    "**!randompoll** - Start polling for random public live matches.\n"
                    "**!submitmatch `<match_id>`** - Submit match result and resolve MMR + bets."
                ),
                color=discord.Color.gold()
            )
            await ctx.reply(embed=embed)
            return
        else:
            await ctx.reply("Unknown help category. Try `!help` or `!help admin`.")

