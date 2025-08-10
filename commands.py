# commands.py
import asyncio
import time
import discord
from discord.ext import commands

def attach_commands(bot, deps):
    # ---- pull in all helpers/state you already have in FeederBot.py ----
    # Checks / auth
    user_is_admin_or_has_role = deps["user_is_admin_or_has_role"]   # async fn(author) -> bool
    is_admin_or_has_role      = deps["is_admin_or_has_role"]        # decorator factory @is_admin_or_has_role()

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
    get_balance          = deps["get_balance"]
    place_bet            = deps["place_bet"]
    update_balance       = deps["update_balance"]
    resolve_bets         = deps["resolve_bets"]
    clear_guild_bets     = deps["clear_guild_bets"]

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
    livematch_cooldowns         = deps["livematch_cooldowns"]
    livematch_timers            = deps["livematch_timers"]

    # Lobby state + helpers
    lobby_players                = deps["lobby_players"]
    lobby_message                = deps["lobby_message"]
    inhouse_mode                 = deps["inhouse_mode"]
    update_lobby_embed           = deps["update_lobby_embed"]
    build_lobby_embed            = deps["build_lobby_embed"]
    save_lobby_players           = deps["save_lobby_players"]
    save_lobby_message_id        = deps["save_lobby_message_id"]
    save_lobby_password_for_guild= deps["save_lobby_password_for_guild"]
    load_inhouse_mode_for_guild  = deps["load_inhouse_mode_for_guild"]
    save_inhouse_mode_for_guild  = deps["save_inhouse_mode_for_guild"]
    save_preferred_roles_setting = deps["save_preferred_roles_setting"]

    # Guild settings
    save_guild_prefix            = deps["save_guild_prefix"]
    save_league_guild_mapping    = deps["save_league_guild_mapping"]
    live_channel_ids             = deps["live_channel_ids"]

    # Misc helpers
    get_discord_id_from_steam_id = deps["get_discord_id_from_steam_id"]
    adjust_mmr                   = deps["adjust_mmr"]

    # ============================== 👥 General Commands ==============================

    @bot.command(name="cfg")
    async def cfg_cmd(ctx, steam_id: str, member: discord.Member = None, *, force: str = None):
        MMR_CAP_FOR_TOP_RANKS = 5650
        if steam_id is None:
            await ctx.send("Please provide a valid numeric Steam friend code or Steam ID.")
            return

        force_flag = (force is not None and force.strip().lower() == "--force")
        target = member or ctx.author

        if target != ctx.author or force_flag:
            is_authorized = await user_is_admin_or_has_role(ctx.author)
            if not is_authorized:
                await ctx.send("❌ You do not have permission to configure another user or use `--force`.")
                return

        steam_id_32 = convert_to_steam32(steam_id)
        if steam_id_32 is None:
            await ctx.send("❌ Invalid Steam ID provided.")
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
                await ctx.send(
                    "⚠️ STRATZ does not provide season rank values beyond 80.\n"
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
            await ctx.send(
                f"🔄 {target.mention}, your Steam ID `{steam_id}` has been force-updated "
                f"with an estimated MMR of **{mmr if mmr is not None else 'N/A'}**."
            )
            return

        # Case A
        if existing_steam_id is not None and isinstance(existing_mmr, (int, float)):
            await ctx.send(
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
            await ctx.send(
                f"{target.mention}, your Steam ID `{steam_id}` has been linked. "
                f"(Existing MMR {int(existing_mmr)} preserved.)"
            )
            return

        # Case C
        if existing_steam_id is not None and not isinstance(existing_mmr, (int, float)):
            mmr, season_rank, source = await fetch_mmr(existing_steam_id)
            if mmr is None and season_rank is not None and season_rank >= 80:
                mmr = MMR_CAP_FOR_TOP_RANKS
                await ctx.send(
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
                await ctx.send(f"{target.mention}, your MMR has been set to **{mmr}**.")
            else:
                await ctx.send(f"{target.mention}, Steam ID was linked earlier, but I still couldn’t determine your MMR.")
            return

        # Case D
        mmr, season_rank, source = await fetch_mmr(steam_id_32)
        if mmr is None and season_rank is not None and season_rank >= 80:
            mmr = MMR_CAP_FOR_TOP_RANKS
            await ctx.send(
                "⚠️ STRATZ does not provide season rank values beyond 80.\n"
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
            await ctx.send(
                f"{target.mention}, your Steam ID `{steam_id}` has been linked "
                f"with an estimated MMR of **{mmr}**."
            )
        else:
            await ctx.send(
                f"{target.mention}, Steam ID linked, but MMR could not be determined."
            )
    @cfg_cmd.error
    async def cfg_cmd_error(ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❗ Usage: `!cfg <steam_id>` (optional: `@user`)")

    @bot.command(name="mmr")
    async def mmr_lookup(ctx, member: discord.Member = None):
        user = member or ctx.author
        mmr = get_mmr(user)
        await ctx.send(f"{user.display_name}'s MMR is **{mmr}**.")

    @bot.command(name="inhouse_mmr")
    async def inhouse_mmr(ctx, member: discord.Member = None):
        member = member or ctx.author
        mmr = get_inhouse_mmr(ctx.guild.id, str(member.id))
        await ctx.send(f"{member.display_name}'s inhouse MMR is **{mmr}**.")

    @bot.command(name="leaderboard")
    async def leaderboard(ctx):
        top_players = get_top_players(ctx.guild.id)
        if not top_players:
            await ctx.send("No leaderboard data found for this server.")
            return
        lines = []
        for rank, (user_id, mmr) in enumerate(top_players, start=1):
            member = ctx.guild.get_member(int(user_id))
            name = member.display_name if member else f"User {user_id}"
            lines.append(f"**#{rank}** - {name}: {mmr} MMR")
        await ctx.send("🏆 **Top 10 Inhouse Players**\n" + "\n".join(lines))

    @bot.command(name="bet")
    async def bet(ctx, amount: int, team: str):
        team = team.lower()
        if team not in ["radiant", "dire"]:
            await ctx.send("❌ Invalid team. Choose `radiant` or `dire`.")
            return
        if amount <= 0:
            await ctx.send("❌ Bet amount must be greater than 0.")
            return
        user_id = str(ctx.author.id)
        nickname = ctx.author.nick if ctx.author.nick else ctx.author.display_name

        if ctx.guild.id not in active_match_ids:
            await ctx.send("❌ There is no active match in progress to bet on.")
            return

        is_random = random_polling_flags.get(ctx.guild.id, False)
        match = await fetch_live_match_for_guild(ctx.guild.id, random_mode=is_random)
        if not match:
            await ctx.send("⚠️ Could not retrieve live match info. Betting may be closed.")
            return

        duration = match.get("scoreboard", {}).get("duration", 0)
        if is_random:
            start_time = match_tracking_start_times.get(ctx.guild.id)
            if start_time and (time.time() - start_time > 180):
                await ctx.send("⏳ Bets are closed. More than 3 minutes have passed since this random match began tracking.")
                return
        else:
            if duration >= 120:
                await ctx.send("⏳ Bets are closed. The match has passed the 2:00 mark.")
                return

        # block betting opposite team if user is playing
        player_team = None
        for player in match.get("players", []):
            steam_id = player.get("account_id")
            discord_id = get_discord_id_from_steam_id(str(steam_id))
            if discord_id == str(ctx.author.id):
                player_team = player.get("team")  # 0 = Radiant, 1 = Dire
                break
        if player_team is not None:
            if (player_team == 0 and team == "dire") or (player_team == 1 and team == "radiant"):
                await ctx.send(
                    f"❌ You are currently playing on the **{'Radiant' if player_team == 0 else 'Dire'}** team.\n"
                    f"You cannot place a bet on the **opposing team** during a match you are in."
                )
                return

        entry_ref = db.collection("guild_specific_info").document(str(ctx.guild.id)).collection("bets").document(str(ctx.author.id))
        existing_bet_doc = entry_ref.get()
        previous_amount = 0
        is_update = False
        if existing_bet_doc.exists:
            existing_bet = existing_bet_doc.to_dict()
            previous_amount = existing_bet.get("amount", 0)
            previous_team = existing_bet.get("team", "")
            if team != previous_team:
                await ctx.send(
                    f"❌ You already bet on **{previous_team.capitalize()}**. "
                    f"You cannot change teams once your bet is placed."
                )
                return
            if amount <= previous_amount:
                await ctx.send(
                    f"❌ You already bet `{previous_amount}`. You can only **increase** your bet amount."
                )
                return
            is_update = True

        old_balance = get_balance(ctx.guild.id, ctx.author.id)
        success = place_bet(user_id, team, amount, ctx.guild.id, nickname)
        new_balance = get_balance(ctx.guild.id, ctx.author.id)
        if not success:
            await ctx.send("❌ You don’t have enough balance.")
        else:
            if is_update:
                await ctx.send(
                    f"🔁 You updated your bet from `{previous_amount}` to `{amount}` on **{team.capitalize()}**. "
                    f"Your balance went from {old_balance} to {new_balance}."
                )
            else:
                await ctx.send(
                    f"✅ You bet `{amount}` on **{team.capitalize()}** for this match. "
                    f"Your balance went from {old_balance} to {new_balance}."
                )
    @bet.error
    async def bet_error(ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❗ Usage: `!bet <amount> <radiant|dire>`")
        elif isinstance(error, commands.BadArgument):
            await ctx.send("❗ Invalid argument. Usage: `!bet <amount> <radiant|dire>` — make sure `<amount>` is a number.")
        else:
            await ctx.send("⚠️ An unexpected error occurred while placing your bet.")

    @bot.command(name="balance")
    async def balance(ctx, member: discord.Member = None):
        member = member or ctx.author
        user_id = str(member.id)
        guild_id = str(ctx.guild.id)
        coins = get_balance(guild_id, user_id)
        await ctx.send(f"💰 {member.display_name}'s balance: `{coins}` coins.")

    @bot.command(name="send")
    async def send_coins(ctx, amount: int, member: discord.Member):
        if amount <= 0:
            await ctx.send("❌ Amount must be greater than 0.")
            return
        sender_id = str(ctx.author.id)
        receiver_id = str(member.id)
        guild_id = str(ctx.guild.id)
        sender_balance = get_balance(guild_id, sender_id)
        if sender_id == receiver_id:
            await ctx.send("❌ You cannot send coins to yourself.")
            return
        if sender_balance < amount:
            await ctx.send(f"❌ You do not have enough coins. Your balance is `{sender_balance}`.")
            return
        update_balance(guild_id, sender_id, -amount)
        update_balance(guild_id, receiver_id, amount)
        await ctx.send(f"💸 {ctx.author.display_name} sent `{amount}` coins to {member.display_name}.\nYour new balance: `{get_balance(guild_id, sender_id)}`.")
    @send_coins.error
    async def send_coins_error(ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❗ Usage: `!send <amount> <@user>`")
        elif isinstance(error, commands.BadArgument):
            await ctx.send("❗ Invalid argument. Usage: `!send <amount> <@user>` — make sure `<amount>` is a number and `<@user>` is a valid user.")
        else:
            await ctx.send("⚠️ An unexpected error occurred while sending coins.")

    @bot.command(name="setpreferredroles")
    async def set_preferred_roles(ctx, r1: int, r2: int, r3: int, r4: int, r5: int, member: discord.Member = None):
        roles = [r1, r2, r3, r4, r5]
        if sorted(roles) != [1, 2, 3, 4, 5]:
            await ctx.send("❌ Usage: !setpreferredroles '1 2 3 4 5' (enter each role starting from most preferred going to least preferred).")
            return
        target = member or ctx.author
        if member and member != ctx.author:
            if not await user_is_admin_or_has_role(ctx.author):
                await ctx.send("❌ You do not have permission to set roles for other users.")
                return
        user_id = str(target.id)
        doc_ref = db.collection("players").document(user_id)
        doc_ref.set({"preferred_roles": roles}, merge=True)
        if target == ctx.author:
            await ctx.send(f"✅ {ctx.author.mention}, your preferred roles have been saved: {roles}")
        else:
            await ctx.send(f"✅ {ctx.author.mention} set preferred roles for {target.mention}: {roles}")
    @set_preferred_roles.error
    async def set_preferred_roles_error(ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❌ You must provide 5 role numbers in order of preference.\nExample: `!setpreferredroles 3 2 4 5 1`")
        elif isinstance(error, commands.BadArgument):
            await ctx.send("❌ Invalid input. Make sure the first five values are numbers (1–5), followed optionally by a valid @user mention.")
        elif isinstance(error, commands.UserInputError):
            await ctx.send("❌ Incorrect usage. Try: `!setpreferredroles 1 2 3 4 5` or `!setpreferredroles 1 2 3 4 5 @user`")
        else:
            await ctx.send("⚠️ An unexpected error occurred while processing your preferred roles.")
            raise error

    @bot.command(name="viewpreferredroles")
    async def view_preferred_roles(ctx, member: discord.Member = None):
        target = member or ctx.author
        user_id = str(target.id)
        doc_ref = db.collection("players").document(user_id)
        doc = doc_ref.get()
        if not doc.exists or "preferred_roles" not in doc.to_dict():
            await ctx.send(f"ℹ️ {target.display_name} has not set their preferred roles.")
            return
        preferred_roles = doc.to_dict()["preferred_roles"]
        formatted = ", ".join(f"Pos {r}" for r in preferred_roles)
        await ctx.send(f"📊 {target.mention}'s preferred roles (most to least preferred): {formatted}")
    @view_preferred_roles.error
    async def view_preferred_roles_error(ctx, error):
        if isinstance(error, commands.BadArgument):
            await ctx.send("❌ Invalid user. Make sure to mention a valid user in the server.")
        else:
            await ctx.send("⚠️ An unexpected error occurred while fetching preferred roles.")
            raise error

    # ========================== 🏠 Lobby Management Commands =========================

    @bot.command(name="add")
    async def add_to_lobby(ctx, *members: discord.Member):
        if not members:
            await ctx.send("❗ Usage: `!add @player1 [@player2 ...]`")
            return
        guild_id = ctx.guild.id
        if guild_id not in lobby_players:
            lobby_players[guild_id] = []
        if len(lobby_players[guild_id]) >= 10:
            await ctx.send("Lobby is already full. Cannot add more players.")
            return
        added = []
        for member in members:
            if any(uid == member.id for uid, _, _ in lobby_players[guild_id]):
                continue
            mmr = get_mmr(member)
            display_name = member.display_name
            lobby_players[guild_id].append((member.id, display_name, mmr))
            save_lobby_players(guild_id, lobby_players[guild_id])
            added.append(display_name)
        if added:
            await update_lobby_embed(ctx.guild)
            await ctx.send(f"Added to lobby: {', '.join(added)}")
        else:
            await ctx.send("No new members were added.")

    @bot.command(name="remove")
    async def remove_from_lobby(ctx, *members: discord.Member):
        if not members:
            await ctx.send("❗ Usage: `!remove @player1 [@player2 ...]`")
            return
        guild_id = ctx.guild.id
        removed = []
        if guild_id not in lobby_players:
            await ctx.send("There is no lobby for this server yet.")
            return
        for member in members:
            for i, (uid, _, _) in enumerate(lobby_players[guild_id]):
                if uid == member.id:
                    del lobby_players[guild_id][i]
                    save_lobby_players(guild_id, lobby_players[guild_id])
                    removed.append(member.display_name)
                    break
        if removed:
            channel = ctx.channel
            message = await channel.fetch_message(lobby_message[guild_id].id)
            if len(lobby_players[guild_id]) < 10:
                await update_lobby_embed(ctx.guild)
                for reaction in message.reactions:
                    if str(reaction.emoji) in ["🚀", "♻️"]:
                        await message.clear_reaction(reaction.emoji)
                await ctx.send(f"Removed from lobby: {', '.join(removed)}")
        else:
            await ctx.send("None of the specified members were in the lobby.")

    @bot.command(name="lobby")
    async def lobby_cmd(ctx, mode: str = None):
        guild_id = ctx.guild.id
        existing_players = lobby_players.get(guild_id, [])
        if mode:
            if not await user_is_admin_or_has_role(ctx.author):
                await ctx.send("❌ You don't have permission to change the inhouse mode.")
                return
            selected_mode = mode.lower() if mode.lower() in ["regular", "immortal"] else "regular"
            save_inhouse_mode_for_guild(guild_id, selected_mode, server_name=ctx.guild.name, set_by=str(ctx.author))
        else:
            selected_mode = load_inhouse_mode_for_guild(guild_id)
        inhouse_mode[guild_id] = selected_mode
        if guild_id not in lobby_players:
            lobby_players[guild_id] = existing_players
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass
        if guild_id in lobby_message:
            try:
                await lobby_message[guild_id].delete()
            except discord.NotFound:
                pass
        embed = build_lobby_embed(ctx.guild, selected_mode)
        message = await ctx.send(embed=embed)
        lobby_message[guild_id] = message
        save_lobby_message_id(guild_id, message.id)
        save_lobby_players(guild_id, lobby_players[guild_id])
        await message.add_reaction("👍")
        await message.add_reaction("👎")
        if len(lobby_players[guild_id]) == 10:
            await message.add_reaction("🚀")

    @bot.command(name="reset")
    async def reset(ctx, *args):
        if args:
            await ctx.send("❗ Usage: `!reset` (no extra arguments allowed)")
            return
        guild_id = ctx.guild.id
        lobby_players[guild_id] = []
        try:
            if guild_id in lobby_message:
                await lobby_message[guild_id].delete()
        except discord.NotFound:
            pass
        embed = build_lobby_embed(ctx.guild)
        message = await ctx.send(embed=embed)
        lobby_message[guild_id] = message
        save_lobby_message_id(guild_id, message.id)
        save_lobby_players(guild_id, lobby_players[guild_id])
        await message.add_reaction("👍")
        await message.add_reaction("👎")
        await ctx.send("Lobby has been cleared and refreshed.")

    @bot.command(name="livematch")
    async def livematch_cmd(ctx):
        guild_id = ctx.guild.id
        now = time.time()
        last_called = livematch_cooldowns.get(guild_id, 0)
        if now - last_called < 30:
            wait_time = int(30 - (now - last_called))
            await ctx.send(f"⏳ You must wait {wait_time} seconds before using `!livematch` again.")
            return

        match_id = active_match_ids.get(guild_id)
        if not match_id:
            await ctx.send("❌ There is no active match to display.")
            return

        is_random = random_polling_flags.get(guild_id, False)
        match = await fetch_live_match_for_guild(guild_id, random_mode=is_random)
        if not match:
            await ctx.send("⚠️ Could not retrieve live match info.")
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

        livematch_cooldowns[guild_id] = now
        if guild_id in livematch_timers:
            livematch_timers[guild_id].cancel()

        async def clear_livematch_timer(gid):
            try:
                await asyncio.sleep(45)
                if time.time() - livematch_cooldowns.get(gid, 0) >= 45:
                    livematch_cooldowns.pop(gid, None)
                    livematch_timers.pop(gid, None)
            except asyncio.CancelledError:
                pass
        livematch_timers[guild_id] = asyncio.create_task(clear_livematch_timer(guild_id))
    @livematch_cmd.error
    async def livematch_cmd_error(ctx, error):
        await ctx.send("⚠️ An error occurred while recalling the live match embed.")

    # ============================= 🔐 Admin-Only Commands ============================

    @bot.command(name="setmmr")
    @is_admin_or_has_role()
    async def setmmr(ctx, mmr: int, member: discord.Member):
        if mmr < 0 or mmr > 20000:
            await ctx.send("❌ Invalid MMR value. Please provide a value between 0 and 20000.")
            return
        if member not in ctx.guild.members:
            await ctx.send("That user is not in this server.")
            return
        try:
            user_ref = db.collection("players").document(str(member.id))
            user_ref.set({"mmr": mmr}, merge=True)
            await ctx.send(f"{member.mention}'s MMR has been manually set to **{mmr}**.")
        except Exception as e:
            await ctx.send(f"Failed to set MMR due to an error: {e}")
    @setmmr.error
    async def set_mmr_error(ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❗ Usage: `!setmmr <mmr> @user`")
        elif isinstance(error, commands.CheckFailure):
            await ctx.send("❌ You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")

    @bot.command(name="alert")
    @is_admin_or_has_role()
    async def alert(ctx):
        guild = ctx.guild
        guild_id = guild.id
        if guild_id not in lobby_players or len(lobby_players[guild_id]) != 10:
            await ctx.send("We do not have 10 players in the lobby yet.")
            return
        mentions = []
        for user_id, _, _ in lobby_players[guild_id]:
            member = guild.get_member(user_id)
            if member:
                mentions.append(member.mention)
        if mentions:
            await ctx.send(f"{' '.join(mentions)} lobby up.")
        else:
            await ctx.send("Could not find any users to alert.")
    @alert.error
    async def alert_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send("❌ You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")

    @bot.command(name="setpassword")
    @is_admin_or_has_role()
    async def set_password(ctx, *, new_password: str):
        save_lobby_password_for_guild(ctx.guild.id, new_password, server_name=ctx.guild.name, set_by=str(ctx.author))
        await update_lobby_embed(ctx.guild)
        await ctx.send(f"Password updated to: `{new_password}`")
    @set_password.error
    async def set_password_error(ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❗ Usage: `!setpassword <new_password>`")
        elif isinstance(error, commands.CheckFailure):
            await ctx.send("❌ You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")

    @bot.command(name="changeprefix")
    @is_admin_or_has_role()
    async def change_prefix(ctx, new_prefix: str):
        save_guild_prefix(ctx.guild.id, new_prefix, server_name=ctx.guild.name, set_by=str(ctx.author))
        await ctx.send(f"✅ Command prefix changed to `{new_prefix}` for this server.")
    @change_prefix.error
    async def change_prefix_error(ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❗ Usage: `!changeprefix <new_prefix>`")
        elif isinstance(error, commands.CheckFailure):
            await ctx.send("❌ You do not have permission to change the prefix. You must be a server admin or have the 'Inhouse Admin' role.")

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

            preferred_roles_setting_data = data.get("preferred_roles_setting", {})
            preferred_roles_enabled = preferred_roles_setting_data.get("preferred_roles_enabled", True)
            preferred_roles_set_by = preferred_roles_setting_data.get("preferred_roles_set_by", "Unknown")
            preferred_roles_time = preferred_roles_setting_data.get("preferred_roles_timestamp", "Unknown")
            if verbose:
                lines.append(f"\n🎯 **Preferred Roles Integration**:\n  • Status: {'✅ Enabled' if preferred_roles_enabled else '❌ Disabled'}\n  • Set by: {preferred_roles_set_by}\n  • Timestamp: {preferred_roles_time}\n  • Field: preferred_roles_enabled = {preferred_roles_enabled}")
            else:
                lines.append(f"\n🎯 **Preferred Roles Integration**: {'✅ Enabled' if preferred_roles_enabled else '❌ Disabled'}\n Set by: {preferred_roles_set_by}\n Time: {preferred_roles_time}")
        else:
            lines.append("❌ No Firestore data found for this guild.")
        await ctx.send("\n".join(lines))
    @viewlogs.error
    async def viewlogs_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send("❌ You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")
        else:
            await ctx.send("⚠️ An unexpected error occurred while retrieving the logs.")

    @bot.command(name="submitmatch")
    @is_admin_or_has_role()
    async def submitmatch(ctx, match_id: str):
        await ctx.send("📊 Processing submitted match...")
        if not match_id.isdigit():
            await ctx.send("❗ Match ID must be a number.")
            return
        result = fetch_match_result(match_id)
        if not result:
            await ctx.send("❌ Could not fetch match result. Check the match ID.")
            return
        winner_ids = map_steam_ids_to_discord_ids(result["radiantplayers"] if result["radiant_win"] else result["direplayers"])
        loser_ids = map_steam_ids_to_discord_ids(result["direplayers"] if result["radiant_win"] else result["radiantplayers"])
        winning_team = "radiant" if result["radiant_win"] else "dire"
        await adjust_mmr(bot, winner_ids, loser_ids, ctx.guild.id)
        resolve_bets(ctx.guild.id, winning_team)
        clear_guild_bets(ctx.guild.id)
        all_player_ids = winner_ids + loser_ids
        for discord_id in all_player_ids:
            try:
                update_balance(ctx.guild.id, discord_id, 50)
            except Exception as e:
                print(f"[ERROR] Failed to award coins to user {discord_id}: {e}")
        await ctx.send(f"✅ Match submitted. `{winning_team.capitalize()}` won. MMRs and bets updated.\nAll participants received **50 coins** for playing.")
    @submitmatch.error
    async def submitmatch_error(ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❗ Usage: `!submitmatch <match_id>`")
        elif isinstance(error, commands.BadArgument):
            await ctx.send("❗ Invalid match ID. It should be a numeric string like `8351234567`.")
        else:
            await ctx.send("⚠️ An unexpected error occurred while submitting the match.")

    @bot.command(name="bindleague")
    @is_admin_or_has_role()
    async def bind_league_to_guild(ctx, league_id: str):
        save_league_guild_mapping(ctx.guild.id, league_id, server_name=ctx.guild.name, bound_by=str(ctx.author))
        await ctx.send(f"✅ League `{league_id}` bound to this server (Guild ID: `{ctx.guild.id}`).")
    @bind_league_to_guild.error
    async def bindleague_error(ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❗ Usage: `!bindleague <league_id>`")
        elif isinstance(error, commands.CheckFailure):
            await ctx.send("❌ You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")
        else:
            await ctx.send("⚠️ An unexpected error occurred while binding the league.")

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
        await ctx.send(f"✅ This channel has been set to receive live match updates.")
    @set_live_channel.error
    async def setlivechannel_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send("❌ You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")
        else:
            await ctx.send("⚠️ An unexpected error occurred while setting the live channel.")

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
                await channel.send(f"[🚀] Started match polling for match ID {match_id} in guild {ctx.guild.name}")
                random_polling_flags[ctx.guild.id] = False
        else:
            await channel.send("⚠️ No live match found for the bound league.")
    @start_polling.error
    async def start_polling_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send("❌ You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")

    @bot.command(name="stoppolling")
    @is_admin_or_has_role()
    async def stop_polling(ctx):
        if ctx.guild.id in polling_tasks and not polling_tasks[ctx.guild.id].done():
            polling_tasks[ctx.guild.id].cancel()
            await ctx.send("🛑 Stopped polling for this server.")
        else:
            await ctx.send("ℹ️ No polling is currently running for this server.")
    @stop_polling.error
    async def stop_polling_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send("❌ You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")

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
                await channel.send(f"[🎲] Started random match polling for match ID {match_id} in guild {ctx.guild.name}")
                random_polling_flags[ctx.guild.id] = True
                match_tracking_start_times[ctx.guild.id] = time.time()
            else:
                await channel.send("⚠️ Polling is already running for this server.")
        else:
            await channel.send("⚠️ No valid random live match found.")
    @random_poll.error
    async def random_poll_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send("❌ You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")
        else:
            await ctx.send("⚠️ An unexpected error occurred while starting random match polling.")
            print(f"[ERROR] random_poll command: {error}")

    @bot.command(name="toggle_roles")
    @is_admin_or_has_role()
    async def toggle_preferred_roles(ctx, mode: str):
        mode = mode.lower()
        if mode not in ["on", "off"]:
            await ctx.send("❗ Usage: `!toggle_roles on` or `!toggle_roles off`")
            return
        enabled = (mode == "on")
        save_preferred_roles_setting(ctx.guild.id, enabled, set_by=ctx.author)
        status = "enabled ✅" if enabled else "disabled ❌"
        await ctx.send(f"Preferred roles integration is now {status} for team balancing.")
    @toggle_preferred_roles.error
    async def toggle_preferred_roles_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send("❌ You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")
        else:
            await ctx.send("⚠️ An unexpected error occurred while toggling preferred roles.")
            print(f"[ERROR] toggle_preferred_roles command: {error}")
    
    @bot.command(name="lobbyroles")
    @is_admin_or_has_role()
    async def lobby_roles(ctx):
        """Show all 10 lobby players and their preferred roles. Admin-only. Only works at 10/10."""
        guild_id = ctx.guild.id
        # Must have a lobby and it must be full
        if guild_id not in lobby_players or len(lobby_players[guild_id]) != 10:
            await ctx.send("❌ This command only works when the lobby is full (10/10).")
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
        await ctx.send(embed=embed)
    @lobby_roles.error
    async def lobby_roles_error(ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send("❌ You do not have permission to use this command. You must be a server admin or have the 'Inhouse Admin' role.")
        else:
            await ctx.send("⚠️ An unexpected error occurred while listing lobby roles.")

    # ================================ ℹ️ Help Command ================================

    @bot.command(name="help")
    async def help_command(ctx, *, category: str = ""):
        category = category.lower().strip()
        if category == "":
            help_text = (
                "\n**📜 Available Commands:**\n\n"
                "__**👥 General Commands**__\n"
                "**!cfg `steam_id` `@user`** - Link your Steam ID to fetch your MMR from STRATZ.\n"
                "**!setpreferredroles `1 2 3 4 5` `@user`** - Set your role preferences from most to least preferred (admin can set for others).\n"
                "**!viewpreferredroles `@user`** - View preferred roles for yourself or another user.\n"
                "**!mmr `@user`** - Show your MMR or another user's MMR.\n"
                "**!inhouse_mmr `@user`** - Show inhouse MMR for yourself or another user\n"
                "**!balance `@user`** - Show your or another user's coin balance\n"
                "**!leaderboard** - View top 10 inhouse MMR players in this server\n"
                "**!send `amount` `@user`** - Send coins to another user in the server\n"
                "**!livematch** - Recall and refresh the live match embed in the channel (30s cooldown)\n\n"
                "__**🏠 Lobby Management**__\n"
                "**!add `@user1` `@user2` ...** - Manually add one or more users to the lobby.\n"
                "**!remove `@user1` `@user2` ...** - Manually remove one or more users from the lobby.\n"
                "**!lobby** - Create or refresh the inhouse lobby.\n"
                "**!reset** - Clear the current lobby and start fresh.\n\n"
                "__**🎲 Betting Commands**__\n"
                "**!bet `amt` `radiant|dire`** - Bet coins on the current inhouse match\n"
                "**!balance `@user`** - Show your or another user’s coin balance\n\n"
                "__**🔐 Admin Commands**__\n"
                "Use `!help admin` to see the list of admin-only commands.\n"
            )
        elif category == "admin":
            help_text = (
                "\n__**🔐 Admin Commands**__\n"
                "**!lobby `mode`** - Sets the lobby mode for the inhouse \n"
                "Modes: • `regular` — Regular Captain’s Mode (MMR-balanced teams) \n"
                "           • `immortal` — Captain’s Mode with Immortal Draft (captains pick teams) \n"
                "**!toggle_roles `on|off`** - Enable or disable preferred role usage in team balancing.\n"
                "**!lobbyroles** - Show preferred roles for all 10 current lobby players (requires full lobby).\n"
                "**!setmmr `mmr` `@user`** - Manually set a user's MMR.\n"
                "**!setpreferredroles `1 2 3 4 5` @user** - Set preferred roles for another user.\n"
                "**!setpassword `new_password`** - Change the inhouse lobby password.\n"
                "**!changeprefix `new_prefix`** - Changes the prefix of the bot commands.\n"
                "**!submitmatch `match_id`** - Report match and resolve MMR + bets\n"
                "**!alert** - Mention all 10 players when the lobby is full.\n"
                "**!viewlogs** - View recent lobby or user config logs.\n"
                "**!viewlogs --verbose** - View full detailed logs for this server.\n"
                "**!bindleague `league_id`** - Binds a Steam league ID to the current Discord server for live match tracking.\n"
                "**!setlivechannel** - Sets the current text channel as the destination for live match embed updates.\n"
                "**!startpolling** - Starts live match polling for the bound league in this server.\n"
                "**!stoppolling** - Stops live match polling for this server.\n"
                "**!randompoll** - Starts polling for random live matches in this server.\n"
            )
        else:
            help_text = "❌ Unknown help category. Try `!help` or `!help admin`."
        await ctx.send(f"{help_text}")