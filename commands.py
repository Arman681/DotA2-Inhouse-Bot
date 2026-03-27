# commands.py
import asyncio
import math
import time
import discord
from discord.ext import commands

def attach_commands(bot, deps):
    # ---- pull in all helpers/state you already have in FeederBot.py ----
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

    # Coins / betting
    get_balance                     = deps["get_balance"]
    place_bet                       = deps["place_bet"]
    update_balance                  = deps["update_balance"]
    resolve_bets                    = deps["resolve_bets"]
    clear_guild_bets                = deps["clear_guild_bets"]
    DD_TOKEN_COST                   = deps["DD_TOKEN_COST"]
    get_dd_token_balance            = deps["get_dd_token_balance"]
    update_dd_token_balance         = deps["update_dd_token_balance"]
    has_active_double_down          = deps["has_active_double_down"]
    activate_double_down            = deps["activate_double_down"]
    get_active_double_down_users    = deps["get_active_double_down_users"]
    clear_active_double_downs       = deps["clear_active_double_downs"]

    # Match / live tracking
    fetch_live_match_for_guild   = deps["fetch_live_match_for_guild"]
    poll_live_match              = deps["poll_live_match"]
    format_live_match_embed      = deps["format_live_match_embed"]
    map_steam_ids_to_discord_ids = deps["map_steam_ids_to_discord_ids"]
    fetch_match_result           = deps["fetch_match_result"]

    # In-memory state dicts (same ones you already maintain)
    active_match_ids            = deps["active_match_ids"]
    polling_tasks               = deps["polling_tasks"]
    random_polling_flags        = deps["random_polling_flags"]
    match_tracking_start_times  = deps["match_tracking_start_times"]
    live_embed_messages         = deps["live_embed_messages"]
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

    # ============================== 👥 General Commands ==============================

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
                f"🔄 {target.mention}, your Steam ID `{steam_id}` has been force-updated "
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
                    "⚠️ STRATZ does not provide season rank values beyond 80.\n"
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
        top_players = get_top_players(ctx.guild.id)
        if not top_players:
            await ctx.reply("No leaderboard data found for this server.")
            return
        lines = []
        for rank, (user_id, stored_nickname, mmr) in enumerate(top_players, start=1):
            # If they’re still in the server, use their current display name
            member = ctx.guild.get_member(int(user_id))
            if member:
                name = member.display_name
            else:
                # Fall back to the nickname stored in inhouse_mmr
                name = stored_nickname or "Unknown"

                # If that nickname is useless, try the main players collection
                if name.lower() == "unknown":
                    player_doc = db.collection("players").document(str(user_id)).get()
                    if player_doc.exists:
                        pdata = player_doc.to_dict() or {}
                        name = (
                            pdata.get("discord_nickname")
                            or pdata.get("steam_name")
                            or name
                        )
            lines.append(f"**#{rank}** - {name}: {mmr}")
        await ctx.reply("**Top 10 Inhouse Players**\n" + "\n".join(lines))

    @commands.cooldown(1, 5, commands.BucketType.user)  # 1 use / 5s per user
    @bot.command(name="bet")
    async def bet(ctx, *args):
        DEFAULT_BET = 100
        # Allow:
        # !bet
        # !bet 200
        # !bet radiant
        # !bet 200 radiant
        # !bet radiant 200
        amount = None
        team = None
        for arg in args:
            lower = arg.lower()
            if lower in ("radiant", "dire"):
                # If they somehow gave two different teams, bail out
                if team is not None and team != lower:
                    await ctx.reply("Please specify only one team: `radiant` or `dire`.")
                    return
                team = lower
            else:
                # Treat anything else as the amount
                if amount is not None:
                    await ctx.reply(
                        "Too many numbers. Usage: `!bet [amount] [radiant|dire]`."
                    )
                    return
                try:
                    amount = int(arg)
                except ValueError:
                    await ctx.reply(
                        "Invalid argument. Usage: `!bet [amount] [radiant|dire]` "
                        "(`amount` must be a number)."
                    )
                    return
        # Default to 100 coins if no amount was explicitly given
        if amount is None:
            amount = DEFAULT_BET
        if amount <= 0:
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
        duration = match.get("scoreboard", {}).get("duration", 0)
        if is_random:
            start_time = match_tracking_start_times.get(ctx.guild.id)
            if start_time and (time.time() - start_time > 180):
                await ctx.reply("Bets are closed. More than 3 minutes have passed since this random match began tracking.")
                return
        else:
            if duration >= 120:
                await ctx.reply("Bets are closed. The match has passed the 2:00 mark.")
                return
        # --- Auto-detect bettor's team if they're playing ---
        player_team = None  # 0 = Radiant, 1 = Dire
        for player in match.get("players", []):
            steam_id = player.get("account_id")
            discord_id = get_discord_id_from_steam_id(str(steam_id))
            if discord_id == str(ctx.author.id):
                player_team = player.get("team")  # 0 = Radiant, 1 = Dire
                break
        # If team not provided, fill it from player's team (if playing), else prompt
        if team is None:
            if player_team is not None:
                team = "radiant" if player_team == 0 else "dire"
                auto_detected = True # they didn't specify, but we found it
            else:
                await ctx.reply(
                    "You’re not in the current match. Please specify a team:\n"
                    "Example: `!bet radiant` (bets 100 coins) or `!bet 200 radiant`."
                )
                return
        # Normalize & validate team now that it’s known
        team = str(team).lower().strip()
        if team not in ["radiant", "dire"]:
            await ctx.reply("Invalid team. Choose `radiant` or `dire`.")
            return
        if player_team is not None:
            if (player_team == 0 and team == "dire") or (player_team == 1 and team == "radiant"):
                await ctx.reply(
                    f"You are currently playing on the **{'Radiant' if player_team == 0 else 'Dire'}** team.\n"
                    f"You cannot place a bet on the **opposing team** during a match you are in."
                )
                return
        entry_ref = db.collection("bets").document(str(ctx.guild.id)).collection("entries").document(str(ctx.author.id))
        existing_bet_doc = entry_ref.get()
        previous_bet = 0
        delta = amount
        is_update = False
        if existing_bet_doc.exists:
            existing_bet = existing_bet_doc.to_dict()
            previous_bet = existing_bet.get("amount", 0)
            previous_team = existing_bet.get("team", "")
            if team != previous_team:
                await ctx.reply(
                    f"You already bet on **{previous_team.capitalize()}**. "
                    f"You cannot change teams once your bet is placed."
                )
                return
            if amount <= previous_bet:
                await ctx.reply(
                    f"You already bet `{previous_bet}`. You can only **increase** your bet amount."
                )
                return
            delta = amount - previous_bet
            is_update = True
        current_balance = get_balance(ctx.guild.id, ctx.author.id, nickname=nickname)
        if (current_balance + previous_bet) < amount:
            await ctx.reply("You don’t have enough balance.")
        else:
            place_bet(user_id, team, amount, delta, ctx.guild.id, nickname)
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
                    f"You updated your bet from `{previous_bet}` to `{amount}` on **{team.capitalize()}**. "
                    f"Your balance went from {current_balance} to {new_balance}."
                )
            else:
                await ctx.reply(
                    prefix +
                    f"You bet `{amount}` on **{team.capitalize()}** for this match. "
                    f"Your balance went from {current_balance} to {new_balance}."
                )
    @bet.error
    async def bet_error(ctx, error):
        await ctx.reply("An unexpected error occurred while placing your bet. Usage: `!bet [amount] [radiant|dire]`.")

    @bot.command(name="balance")
    async def balance(ctx, member: discord.Member = None):
        member = member or ctx.author
        user_id = str(member.id)
        guild_id = str(ctx.guild.id)
        coins = get_balance(guild_id, user_id, nickname=member.display_name)
        await ctx.reply(f"{member.display_name}'s balance: `{coins}` coins.")

    @bot.command(name="store")
    async def store(ctx):
        await ctx.reply(
            "**Store**\n"
            f"**dd_tokens** — `{DD_TOKEN_COST}` coins each\n\n"
            "Use: `!buy dd_tokens <amount>`"
        )

    @bot.command(name="buy")
    async def buy(ctx, item: str = None, amount: int = 1):
        if item is None:
            await ctx.reply("Usage: !buy `dd_tokens` `<amount>`")
            return
        item = item.lower().strip()
        if item != "dd_tokens":
            await ctx.reply("That item is not sold in the store. Right now the only item is `dd_tokens`.")
            return
        if amount <= 0:
            await ctx.reply("Amount must be greater than 0.")
            return
        guild_id = str(ctx.guild.id)
        user_id = str(ctx.author.id)
        nickname = ctx.author.display_name
        total_cost = DD_TOKEN_COST * amount
        current_balance = get_balance(guild_id, user_id, nickname=nickname)
        if current_balance < total_cost:
            await ctx.reply(
                f"You do not have enough coins.\n"
                f"Cost: `{total_cost}` coins\n"
                f"Your balance: `{current_balance}` coins"
            )
            return
        update_balance(guild_id, user_id, -total_cost, nickname=nickname)
        update_dd_token_balance(guild_id, user_id, amount, nickname=nickname)
        new_balance = get_balance(guild_id, user_id, nickname=nickname)
        new_tokens = get_dd_token_balance(guild_id, user_id, nickname=nickname)
        await ctx.reply(
            f"You bought `{amount}` dd_tokens for `{total_cost}` coins.\n"
            f"New balance: `{new_balance}` coins\n"
            f"Your dd_tokens: `{new_tokens}`"
        )

    @buy.error
    async def buy_error(ctx, error):
        if isinstance(error, commands.BadArgument):
            await ctx.reply("Usage: !buy `dd_tokens` `<amount>`")
        else:
            await ctx.reply("An unexpected error occurred while buying from the store.")

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
            await ctx.reply("You do not have any dd_tokens. Buy one first with `!buy dd_tokens 1`.")
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
    async def send_coins(ctx, amount: int, member: discord.Member):
        if amount <= 0:
            await ctx.reply("Amount must be greater than 0.")
            return
        sender_id = str(ctx.author.id)
        receiver_id = str(member.id)
        guild_id = str(ctx.guild.id)
        sender_balance = get_balance(guild_id, sender_id, nickname=ctx.author.display_name)
        if sender_id == receiver_id:
            await ctx.reply("You cannot send coins to yourself.")
            return
        if sender_balance < amount:
            await ctx.reply(f"You do not have enough coins. Your balance is `{sender_balance}`.")
            return
        update_balance(guild_id, sender_id, -amount)
        update_balance(guild_id, receiver_id, amount)
        await ctx.reply(f"💸 {ctx.author.display_name} sent `{amount}` coins to {member.display_name}.\nYour new balance: `{get_balance(guild_id, sender_id)}`.")
    @send_coins.error
    async def send_coins_error(ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply("Usage: !send `<amount>` `<@user>`")
        elif isinstance(error, commands.BadArgument):
            await ctx.reply("Invalid argument. Usage: !send `<amount>` `<@user>` — make sure `<amount>` is a number and `<@user>` is a valid user.")
        else:
            await ctx.reply("An unexpected error occurred while sending coins.")

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

    # ========================== 🏠 Lobby Management Commands =========================

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
            except:
                message = None
        current_players = lobby_players[guild_id]
        # -------------------------------
        # Parse OLD target
        # -------------------------------
        old_is_mention = len(ctx.message.mentions) >= 1
        old_target_uid = None
        old_target_name = None
        old_index = None
        if old_is_mention:
            old_member = ctx.message.mentions[0]
            for i, (uid, name, mmr) in enumerate(current_players):
                if str(uid) == str(old_member.id):
                    old_target_uid = uid
                    old_target_name = name
                    old_index = i
                    break
        else:
            old_placeholder_name = args[0].strip().lower()
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
        # Parse NEW target
        # -------------------------------
        mentions = ctx.message.mentions
        # Case 1: replacing with a mentioned user
        if len(mentions) >= 2:
            new_member = mentions[1]
            if any(str(uid) == str(new_member.id) for uid, _, _ in current_players):
                await ctx.reply("That replacement user is already in the lobby.")
                return
            new_tuple = (new_member.id, new_member.display_name, get_mmr(new_member))
        # Case 2: old target is mention, new target is placeholder
        elif len(mentions) == 1:
            if len(args) != 3:
                await ctx.reply(
                    "Usage for replacing with a placeholder:\n"
                    "`!replace @olduser <placeholder_name> <mmr>`"
                )
                return
            placeholder_name = args[1].strip()
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
            if any(str(uid) == placeholder_id for uid, _, _ in current_players):
                await ctx.reply("That placeholder is already in the lobby.")
                return
            if any(name.lower() == placeholder_name.lower() for uid, name, _ in current_players if uid != old_target_uid):
                await ctx.reply("That name is already in the lobby.")
                return
            new_tuple = (placeholder_id, placeholder_name, placeholder_mmr)
        # Case 3: no mentions at all -> placeholder replaced with placeholder
        else:
            if len(args) != 3:
                await ctx.reply(
                    "Usage for placeholder replacement:\n"
                    "`!replace <old_placeholder> <new_placeholder> <mmr>`"
                )
                return
            placeholder_name = args[1].strip()
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
            if any(str(uid) == placeholder_id for uid, _, _ in current_players):
                await ctx.reply("That replacement placeholder is already in the lobby.")
                return
            if any(name.lower() == placeholder_name.lower() for uid, name, _ in current_players if uid != old_target_uid):
                await ctx.reply("That name is already in the lobby.")
                return
            new_tuple = (placeholder_id, placeholder_name, placeholder_mmr)
        # -------------------------------
        # Special case: old target placeholder -> new target mention
        # -------------------------------
        if not old_is_mention and len(mentions) == 1:
            new_member = mentions[0]
            if any(str(uid) == str(new_member.id) for uid, _, _ in current_players):
                await ctx.reply("That replacement user is already in the lobby.")
                return
            new_tuple = (new_member.id, new_member.display_name, get_mmr(new_member))
        # -------------------------------
        # Perform replacement
        # -------------------------------
        current_players[old_index] = new_tuple
        await full_post_rocket_reset(guild_id, message)
        save_lobby_players(guild_id, current_players)
        await update_lobby_embed(ctx.guild)
        await ctx.reply(f"Replaced **{old_target_name}** with **{new_tuple[1]}** in the lobby.")
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

    # ============================= 🔐 Admin-Only Commands ============================

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
            lines.append(f"📜 **Admin Logs (Verbose)** for `{guild_name}` (Guild ID: `{guild_id}`)")
        else:
            lines.append(f"📜 **Admin Logs for `{guild_name}`**")
        if doc.exists:
            data = doc.to_dict()
            prefix_data = data.get("prefix", {})
            prefix = prefix_data.get("prefix", "Unknown")
            prefix_set_by = prefix_data.get("prefix_set_by", "Unknown")
            prefix_time = prefix_data.get("prefix_timestamp", "Unknown")
            if verbose:
                lines.append(f"🔧 **Prefix**:\n  • Value: `{prefix}`\n  • Set by: {prefix_set_by}\n  • Timestamp: `{prefix_time}`\n  • Full Doc: `{prefix_data}`")
            else:
                lines.append(f"🔧 **Prefix**: `{prefix}`\nSet by: {prefix_set_by}\nTime: {prefix_time}")
            password_data = data.get("password", {})
            password = password_data.get("password", "Unknown")
            password_set_by = password_data.get("password_set_by", "Unknown")
            password_time = password_data.get("password_timestamp", "Unknown")
            if verbose:
                lines.append(f"\n🔐 **Lobby Password**:\n  • Value: `{password}`\n  • Set by: {password_set_by}\n  • Timestamp: `{password_time}`\n  • Full Doc: `{password_data}`")
            else:
                lines.append(f"\n🔐 **Lobby Password**: `{password}`\nSet by: {password_set_by}\nTime: {password_time}")
            inhouse_mode_data = data.get("inhouse_mode", {})
            mode = inhouse_mode_data.get("mode", "Unknown")
            mode_set_by = inhouse_mode_data.get("mode_set_by", "Unknown")
            mode_time = inhouse_mode_data.get("mode_timestamp", "Unknown")
            if verbose:
                lines.append(f"\n🛠️ **Inhouse Mode**:\n  • Value: `{mode}`\n  • Set by: {mode_set_by}\n  • Timestamp: `{mode_time}`\n  • Full Doc: `{inhouse_mode_data}`")
            else:
                lines.append(f"\n🛠️ **Inhouse Mode**: `{mode}`\nSet by: {mode_set_by}\nTime: {mode_time}")
            league_id_data = data.get("league_id", {})
            bound_league_id = league_id_data.get("bound_league_id", "Unknown")
            league_bind_by = league_id_data.get("league_id_bound_by", "Unknown")
            league_bind_time = league_id_data.get("league_bind_timestamp", "Unknown")
            if verbose:
                lines.append(f"\n🏆 **League ID**:\n  • Value: `{bound_league_id}`\n  • Bound by: {league_bind_by}\n  • Timestamp: `{league_bind_time}`\n  • Full Doc: `{league_id_data}`")
            else:
                lines.append(f"\n🏆 **League ID**: `{bound_league_id}`\nBound by: {league_bind_by}\nTime: {league_bind_time}")
            live_channel_data = data.get("live_channel_id", {})
            live_channel_id = live_channel_data.get("live_channel_id", "Unknown")
            live_channel_time = live_channel_data.get("live_channel_timestamp", "Unknown")
            live_channel_set_by = live_channel_data.get("bound_by", "Unknown")
            if verbose:
                lines.append(f"\n📺 **Live Channel ID**:\n  • Value: `{live_channel_id}`\n  • Set by: {live_channel_set_by}\n  • Timestamp: `{live_channel_time}`\n  • Full Doc: `{live_channel_data}`")
            else:
                lines.append(f"\n📺 **Live Channel ID**: `{live_channel_id}`\nSet by: {live_channel_set_by}\nTime: {live_channel_time}")
            lobby_channel_data = data.get("lobby_channel_id", {})
            lobby_channel_id = lobby_channel_data.get("lobby_channel_id", "Unknown")
            lobby_channel_time = lobby_channel_data.get("lobby_channel_timestamp", "Unknown")
            lobby_channel_set_by = lobby_channel_data.get("bound_by", "Unknown")
            if verbose:
                lines.append(f"\n🧾 **Lobby Channel ID**:\n  • Value: `{lobby_channel_id}`\n  • Set by: {lobby_channel_set_by}\n  • Timestamp: `{lobby_channel_time}`\n  • Full Doc: `{lobby_channel_data}`")
            else:
                lines.append(f"\n🧾 **Lobby Channel ID**: `{lobby_channel_id}`\nSet by: {lobby_channel_set_by}\nTime: {lobby_channel_time}")
            preferred_roles_setting_data = data.get("preferred_roles_setting", {})
            preferred_roles_enabled = preferred_roles_setting_data.get("preferred_roles_enabled", True)
            preferred_roles_set_by = preferred_roles_setting_data.get("preferred_roles_set_by", "Unknown")
            preferred_roles_time = preferred_roles_setting_data.get("preferred_roles_timestamp", "Unknown")
            if verbose:
                lines.append(f"\n🎯 **Preferred Roles Integration**:\n  • Status: {'✅ Enabled' if preferred_roles_enabled else '❌ Disabled'}\n  • Set by: {preferred_roles_set_by}\n  • Timestamp: {preferred_roles_time}\n  • Field: preferred_roles_enabled = {preferred_roles_enabled}")
            else:
                lines.append(f"\n🎯 **Preferred Roles Integration**: {'✅ Enabled' if preferred_roles_enabled else '❌ Disabled'}\n Set by: {preferred_roles_set_by}\n Time: {preferred_roles_time}")
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
                    f"\n🧭 **Captain Policy**:\n  • Value: `{pol}`{threshold_note}\n  • Set by: {pol_set_by}\n  • Timestamp: `{pol_time}`\n  • Full Doc: `{captain_data}`")
            else:
                lines.append(f"\n🧭 **Captain Policy**: `{pol}`{threshold_note}\n Set by: {pol_set_by}\n Time: {pol_time}")
        else:
            lines.append("No Firestore data found for this guild.")
        await ctx.reply("\n".join(lines))
    @viewlogs.error
    async def viewlogs_error(ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            wait = math.ceil(error.retry_after)
            await ctx.reply(f"⏳ Please wait {wait}s before using `!viewlogs` again.")
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
        result = fetch_match_result(match_id)
        if not result:
            await ctx.reply("Could not fetch match result. Check the match ID.")
            return
        winner_ids = map_steam_ids_to_discord_ids(result["radiantplayers"] if result["radiant_win"] else result["direplayers"])
        loser_ids = map_steam_ids_to_discord_ids(result["direplayers"] if result["radiant_win"] else result["radiantplayers"])
        winning_team = "radiant" if result["radiant_win"] else "dire"
        doubled_user_ids = get_active_double_down_users(ctx.guild.id)
        await adjust_mmr(bot, winner_ids, loser_ids, ctx.guild.id, doubled_user_ids=doubled_user_ids)
        clear_active_double_downs(ctx.guild.id)
        resolve_bets(ctx.guild.id, winning_team)
        clear_guild_bets(ctx.guild.id)
        all_player_ids = winner_ids + loser_ids
        for discord_id in all_player_ids:
            try:
                update_balance(ctx.guild.id, discord_id, 50)
            except Exception as e:
                print(f"[ERROR] Failed to award coins to user {discord_id}: {e}")
        await ctx.reply(f"Match submitted. `{winning_team.capitalize()}` won. MMRs and bets updated.\nAll participants received **50 coins** for playing.")
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
            title="🧭 Preferred Roles — Current Lobby (10/10)",
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
                f"📋 Captain policy: **{pol}**{extra}\n"
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

    # ================================ ℹ️ Help Command ================================

    @bot.command(name="help")
    async def help_command(ctx, *, category: str = ""):
        category = category.lower().strip()
        if category == "":
            help_text = (
                "\n**📜 Available Commands:**\n\n"
                "__**General Commands**__\n"
                "**!cfg `<steam_id>`** - Link your Steam ID to fetch your MMR from STRATZ.\n"
                "**!setpreferredroles `<1 2 3 4 5>`** - Set your role preferences from most to least preferred.\n"
                "**!viewpreferredroles `[@user]`** - View preferred roles for yourself or another user.\n"
                "**!mmr `[@user]`** - Show your MMR or another user's MMR.\n"
                "**!inhouse_mmr `[@user]`** - Show inhouse MMR for yourself or another user\n"
                "**!balance `[@user]`** - Show your or another user's coin balance\n"
                "**!leaderboard** - View top 10 inhouse MMR players in this server\n"
                "**!send `<amount>` `<@user>`** - Send coins to another user in the server\n"
                "**!livematch** - Recall and refresh the live match embed in the channel (30s cooldown)\n\n"
                "__**Betting / Store Commands**__\n"
                "**!bet `<amt>` `<radiant|dire>`** - Bet coins on the current inhouse match\n"
                "**!store** - View the store\n"
                "**!buy `<dd_tokens>` `<amount>`** - Buy double down tokens\n"
                "**!dd_tokens `[@user]`** - View double down token balance for yourself or another user\n"
                "**!dd** - Double your inhouse MMR gain/loss for the current match\n"
                "**!balance `[@user]`** - Show your or another user’s coin balance\n\n"
                "__**Admin Commands**__\n"
                "Use `!help admin` to see the list of admin-only commands.\n"
            )
        elif category == "admin":
            help_text = (
                "\n__**Admin Commands**__\n\n"
                "__**Player & Lobby Management**__\n"
                "**!add `<@user1>` `<@user2>` ...** - Manually add one or more users to the lobby.\n"
                "**!remove `<@user1>` `<@user2>` ...** - Manually remove one or more users from the lobby.\n"
                "**!replace `<@user1|placeholder1>` `<@user2|placeholder2>`** - Replace one lobby user or placeholder with another user or placeholder\n"
                "**!lobby** - Create or refresh the inhouse lobby.\n"
                "**!reset** - Clear the current lobby and start fresh.\n"
                "**!cfg `<steam_id>` `[@user]` `[--force]`** - Link a player's Steam ID and fetch their MMR\n"
                "  • Without `--force`: Will not overwrite existing Steam ID and MMR\n"
                "  • With `--force`: Forcibly updates Steam ID and MMR, even if already set\n"
                "**!setmmr `<mmr>` `<@user>`** - Manually set a user's MMR\n"
                "**!setpreferredroles `<1 2 3 4 5>` `<@user>`** - Set preferred roles for another user\n"
                "**!alert** - Mention all 10 players when the lobby is full\n\n"
                "__**Lobby Configuration**__\n"
                "**!lobby `<mode>`** - Set the inhouse mode for this server\n"
                "  • `regular` — Regular Captain's Mode (MMR-balanced teams)\n"
                "  • `immortal` — Captain's Mode with Immortal Draft (captains pick teams)\n"
                "**!setpassword `<new_password>`** - Change the inhouse lobby password\n"
                "**!setlobbychannel** - Set current channel for lobby embeds and reset posts\n"
                "**!toggle_roles `<on|off>`** - Enable/disable preferred role usage in team balancing\n"
                "**!lobbyroles** - Show preferred roles for all 10 lobby players (requires full lobby)\n\n"
                "__**Bot Settings**__\n"
                "**!changeprefix `<new_prefix>`** - Change the bot command prefix for this server\n"
                "**!viewlogs** - View recent configuration logs for this server\n"
                "**!viewlogs --verbose** - View detailed logs with full Firestore data\n\n"
                "__**Captain & Draft Settings**__\n"
                "**!captainpolicy `<policy>` `[threshold]`** - Set captain selection policy\n"
                "  • `min_diff` — Choose pair with lowest MMR difference (default)\n"
                "  • `top2_if_close` `<threshold>` — Prioritize top 2 if within threshold\n"
                "  • `simulate` — Simulate draft to find most balanced captain pair\n\n"
                "__**Match Tracking**__\n"
                "**!bindleague `<league_id>`** - Bind a Steam league ID for live match tracking\n"
                "**!setlivechannel** - Set current channel for live match updates\n"
                "**!startpolling** - Start live match polling for the bound league\n"
                "**!stoppolling** - Stop live match polling\n"
                "**!randompoll** - Start polling for random public live matches\n"
                "**!submitmatch `<match_id>`** - Submit match result and resolve MMR + bets\n"
            )
        else:
            help_text = "Unknown help category. Try `!help` or `!help admin`."
        for chunk in [help_text[i:i+2000] for i in range(0, len(help_text), 2000)]:
            await ctx.reply(chunk)