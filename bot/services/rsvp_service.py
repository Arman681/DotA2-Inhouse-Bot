import asyncio
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord


RSVP_CAPACITY = 10
RSVP_COLLECTION = "rsvp_events"
RSVP_TIMEZONE = ZoneInfo("America/New_York")
RSVP_CONFIRMATION_LEAD_SECONDS = 60 * 60

STATUS_ACTIVE = "active"
STATUS_CONFIRMED = "confirmed"
STATUS_CANCELLED = "cancelled"
STATUS_CLOSED = "closed"
STATUS_RESET = "reset"
STATUS_STARTING = "starting"

INTERACTIVE_STATUSES = {STATUS_ACTIVE, STATUS_CONFIRMED}

SIGNUP_RSVP = "rsvp"
SIGNUP_FILL = "fill"

_TIME_PATTERN = re.compile(
    r"^(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<period>am|pm)?$",
    re.IGNORECASE,
)


def parse_rsvp_start_time(raw_time: str, *, now: datetime | None = None) -> datetime:
    """Parse the next occurrence of a New York local time."""
    match = _TIME_PATTERN.fullmatch(str(raw_time or "").strip())
    if not match:
        raise ValueError("Use a time such as `8:30pm`, `8 PM`, or `20:30`.")

    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    period = (match.group("period") or "").lower()
    if minute > 59:
        raise ValueError("Minutes must be between 00 and 59.")

    if period:
        if hour < 1 or hour > 12:
            raise ValueError("12-hour times must use an hour from 1 through 12.")
        if hour == 12:
            hour = 0
        if period == "pm":
            hour += 12
    elif hour > 23:
        raise ValueError("24-hour times must use an hour from 0 through 23.")

    local_now = now or datetime.now(RSVP_TIMEZONE)
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=RSVP_TIMEZONE)
    else:
        local_now = local_now.astimezone(RSVP_TIMEZONE)
    event_start = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if event_start <= local_now:
        event_start += timedelta(days=1)
    return event_start


def _ordered_signups(event: dict, signup_status: str) -> list[tuple[str, dict]]:
    signups = event.get("signups", {}) or {}
    rows = [
        (str(user_id), data)
        for user_id, data in signups.items()
        if isinstance(data, dict) and data.get("status") == signup_status
    ]
    return sorted(
        rows,
        key=lambda row: (int(row[1].get("joined_at", 0) or 0), row[0]),
    )


def _is_promoted_fill(signup: dict | None) -> bool:
    if not isinstance(signup, dict):
        return False
    return bool(
        signup.get("promoted_from_fill")
        or signup.get("promoted_at")
        or signup.get("signup_origin") == SIGNUP_FILL
    )


def _format_roster(event: dict, signup_status: str, empty_text: str) -> str:
    rows = _ordered_signups(event, signup_status)
    if not rows:
        return empty_text

    lines = []
    for index, (user_id, data) in enumerate(rows, start=1):
        fallback_name = f"User {user_id}"
        display_name = discord.utils.escape_markdown(str(data.get("display_name") or fallback_name))
        line = f"`{index}.` **{display_name}**"
        if len("\n".join(lines + [line])) > 960:
            remaining = len(rows) - len(lines)
            lines.append(f"*…and {remaining} more*" if remaining > 0 else "*…*")
            break
        lines.append(line)
    return "\n".join(lines)


def build_rsvp_embed(event: dict) -> discord.Embed:
    status = event.get("status", STATUS_ACTIVE)
    title = "FeederBot Inhouse RSVP"
    color = discord.Color.blurple()
    description = str(event.get("notes") or "All ranks are welcome.")

    rsvp_rows = _ordered_signups(event, SIGNUP_RSVP)
    fill_rows = _ordered_signups(event, SIGNUP_FILL)

    if status == STATUS_CONFIRMED:
        if len(rsvp_rows) >= RSVP_CAPACITY:
            title += " — Confirmed"
            color = discord.Color.green()
            status_text = "This inhouse is confirmed. Please be ready 10 minutes early."
        else:
            title += " — Replacement Needed"
            color = discord.Color.orange()
            status_text = f"The confirmed roster needs {RSVP_CAPACITY - len(rsvp_rows)} replacement player(s)."
        description = f"{description}\n\n{status_text}"
    elif status == STATUS_CANCELLED:
        title += " — Cancelled"
        color = discord.Color.red()
        cancellation_reason = str(event.get("cancellation_reason") or "").strip()
        if event.get("cancelled_by"):
            description = "This inhouse was called off by an admin."
        else:
            description = "This inhouse was called off because fewer than 10 players were available when it was finalized."
        if cancellation_reason:
            description += f"\n\n**Reason:** {cancellation_reason}"
    elif status == STATUS_CLOSED:
        title += " — Closed"
        color = discord.Color.dark_grey()
        description = "Signups are closed. The final roster is shown below."
    elif status == STATUS_RESET:
        title += " — Reset"
        color = discord.Color.dark_grey()
        description = "This RSVP event was reset by an admin."

    embed = discord.Embed(title=title, description=description, color=color)
    start_at = int(event.get("start_at", 0) or 0)
    checkpoint_at = int(
        event.get("checkpoint_at", 0)
        or (start_at - RSVP_CONFIRMATION_LEAD_SECONDS if start_at else 0)
    )
    start_value = f"<t:{start_at}:F>\n<t:{start_at}:R>" if start_at else "Not set"
    checkpoint_value = (
        f"<t:{checkpoint_at}:F>\n<t:{checkpoint_at}:R>"
        if checkpoint_at
        else "Not set"
    )
    games = int(event.get("games", 1) or 1)
    game_label = "game" if games == 1 else "games"

    embed.add_field(name="Start Time", value=start_value, inline=True)
    embed.add_field(name="Confirmation Deadline", value=checkpoint_value, inline=True)
    embed.add_field(name="Format", value=f"**{games} {game_label}**\n5v5 inhouse", inline=True)
    embed.add_field(name="Eligibility", value="**All ranks**\nConfigured players", inline=True)
    embed.add_field(
        name="Go / No-Go Policy",
        value=(
            "FeederBot confirms the inhouse only if at least **10 total RSVPs and fills** are available one hour before start.\n"
            "Once confirmed, RSVP players cannot withdraw during the final 60 minutes; fills may still withdraw."
        ),
        inline=False,
    )
    embed.add_field(
        name="Rules",
        value="• No unnecessary pausing.\n• No fake GG calls.",
        inline=False,
    )

    rsvp_heading = "Confirmed Players" if status == STATUS_CONFIRMED else "RSVP"
    embed.add_field(
        name=f"✅ {rsvp_heading} — {len(rsvp_rows)}/{RSVP_CAPACITY}",
        value=_format_roster(event, SIGNUP_RSVP, "*No players yet.*"),
        inline=True,
    )
    embed.add_field(
        name=f"🟨 Fills — {len(fill_rows)}",
        value=_format_roster(event, SIGNUP_FILL, "*No fills yet.*"),
        inline=True,
    )

    updated_at = int(event.get("updated_at", time.time()) or time.time())
    embed.timestamp = datetime.fromtimestamp(updated_at, tz=timezone.utc)
    if status == STATUS_ACTIVE:
        embed.set_footer(text="RSVP = committed • Fill = available if promoted • Last updated")
    elif status == STATUS_CONFIRMED:
        embed.set_footer(text="Confirmed RSVPs are locked • Fills remain flexible • Last updated")
    else:
        embed.set_footer(text="This event is no longer accepting signups • Last updated")
    return embed


class RsvpView(discord.ui.View):
    def __init__(
        self,
        manager: "RsvpManager",
        *,
        rsvp_full: bool = False,
        disabled: bool = False,
    ):
        super().__init__(timeout=None)
        self.manager = manager
        self.rsvp_button.disabled = disabled or rsvp_full
        self.fill_button.disabled = disabled
        self.withdraw_button.disabled = disabled

    @discord.ui.button(
        label="RSVP",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id="feederbot:rsvp:join",
    )
    async def rsvp_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.manager.handle_signup(interaction, SIGNUP_RSVP)

    @discord.ui.button(
        label="Fill",
        emoji="🟨",
        style=discord.ButtonStyle.primary,
        custom_id="feederbot:rsvp:fill",
    )
    async def fill_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.manager.handle_signup(interaction, SIGNUP_FILL)

    @discord.ui.button(
        label="Withdraw",
        emoji="❌",
        style=discord.ButtonStyle.danger,
        custom_id="feederbot:rsvp:withdraw",
    )
    async def withdraw_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.manager.handle_signup(interaction, "withdraw")


class RsvpManager:
    parse_start_time = staticmethod(parse_rsvp_start_time)

    def __init__(self, *, bot, db, load_player_config, load_guild_prefix):
        self.bot = bot
        self.db = db
        self.load_player_config = load_player_config
        self.load_guild_prefix = load_guild_prefix
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._persistent_view_registered = False
        self._ephemeral_delete_tasks: set[asyncio.Task] = set()
        self._checkpoint_tasks: dict[int, asyncio.Task] = {}

    def _event_ref(self, guild_id: int):
        return self.db.collection(RSVP_COLLECTION).document(str(guild_id))

    def get_event(self, guild_id: int) -> dict | None:
        snapshot = self._event_ref(guild_id).get()
        if not snapshot.exists:
            return None
        event = snapshot.to_dict() or {}
        event.setdefault("guild_id", str(guild_id))
        return event

    def _save_event(self, guild_id: int, event: dict) -> None:
        self._event_ref(guild_id).set(event)

    def make_view(self, event: dict, *, disabled: bool = False) -> RsvpView:
        is_full = len(_ordered_signups(event, SIGNUP_RSVP)) >= RSVP_CAPACITY
        status = event.get("status", STATUS_ACTIVE)
        buttons_disabled = disabled or status not in INTERACTIVE_STATUSES
        rsvp_locked = is_full or status == STATUS_CONFIRMED
        return RsvpView(self, rsvp_full=rsvp_locked, disabled=buttons_disabled)

    def register_persistent_view(self) -> None:
        if self._persistent_view_registered:
            return
        self.bot.add_view(RsvpView(self))
        self._persistent_view_registered = True

    async def _resolve_channel(self, event: dict):
        channel_id = int(event.get("channel_id", 0) or 0)
        if not channel_id:
            return None
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                return None
        return channel

    async def _resolve_message(self, event: dict) -> discord.Message | None:
        message_id = int(event.get("message_id", 0) or 0)
        if not message_id:
            return None
        channel = await self._resolve_channel(event)
        if channel is None:
            return None
        try:
            return await channel.fetch_message(message_id)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException, AttributeError):
            return None

    def _cancel_checkpoint_task(self, guild_id: int) -> None:
        task = self._checkpoint_tasks.pop(guild_id, None)
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()

    def _schedule_checkpoint(self, event: dict) -> None:
        guild_id = int(event.get("guild_id", 0) or 0)
        if not guild_id or event.get("status") != STATUS_ACTIVE:
            return
        self._cancel_checkpoint_task(guild_id)
        checkpoint_at = int(
            event.get("checkpoint_at", 0)
            or (int(event.get("start_at", 0) or 0) - RSVP_CONFIRMATION_LEAD_SECONDS)
        )
        delay = max(0, checkpoint_at - int(time.time()))
        task = asyncio.create_task(self._run_checkpoint(guild_id, delay))
        self._checkpoint_tasks[guild_id] = task

    async def _run_checkpoint(self, guild_id: int, delay: int) -> None:
        current_task = asyncio.current_task()
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            await self.finalize_event(guild_id, automatic=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[rsvp] Automatic finalization failed for guild {guild_id}: {exc}")
        finally:
            if self._checkpoint_tasks.get(guild_id) is current_task:
                self._checkpoint_tasks.pop(guild_id, None)

    async def start_event(
        self,
        *,
        guild: discord.Guild,
        channel,
        author: discord.Member,
        start_time: datetime,
        games: int,
        notes: str = "",
    ) -> tuple[dict, discord.Message]:
        guild_id = guild.id
        async with self._locks[guild_id]:
            existing = self.get_event(guild_id)
            if existing and existing.get("status") in {
                STATUS_ACTIVE,
                STATUS_CONFIRMED,
                STATUS_STARTING,
            }:
                raise ValueError(
                    "This server already has an active RSVP event. Close or reset it before starting another one."
                )

            now_epoch = int(time.time())
            start_at = int(start_time.astimezone(timezone.utc).timestamp())
            checkpoint_at = start_at - RSVP_CONFIRMATION_LEAD_SECONDS
            if checkpoint_at <= now_epoch:
                raise ValueError(
                    "The inhouse start time must be more than one hour away because the go/no-go decision happens one hour before start."
                )
            event = {
                "guild_id": str(guild_id),
                "guild_name": guild.name,
                "channel_id": str(channel.id),
                "message_id": None,
                "status": STATUS_STARTING,
                "start_at": start_at,
                "checkpoint_at": checkpoint_at,
                "games": int(games),
                "capacity": RSVP_CAPACITY,
                "notes": str(notes or "").strip(),
                "signups": {},
                "created_by": str(author.id),
                "created_by_name": str(author),
                "created_at": now_epoch,
                "updated_at": now_epoch,
            }
            self._save_event(guild_id, event)

            active_preview = dict(event)
            active_preview["status"] = STATUS_ACTIVE
            try:
                message = await channel.send(
                    embed=build_rsvp_embed(active_preview),
                    view=self.make_view(active_preview),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                event["status"] = STATUS_RESET
                event["updated_at"] = int(time.time())
                self._save_event(guild_id, event)
                raise

            event["message_id"] = str(message.id)
            event["status"] = STATUS_ACTIVE
            event["updated_at"] = int(time.time())
            self._save_event(guild_id, event)
            self._schedule_checkpoint(event)
            return event, message

    def _promote_fills(self, event: dict, slots_needed: int) -> list[str]:
        if slots_needed <= 0:
            return []
        signups = dict(event.get("signups", {}) or {})
        promoted = []
        for user_id, data in _ordered_signups(event, SIGNUP_FILL)[:slots_needed]:
            updated = dict(data)
            updated["status"] = SIGNUP_RSVP
            updated["promoted_at"] = int(time.time())
            updated["promoted_from_fill"] = True
            updated.setdefault("signup_origin", SIGNUP_FILL)
            signups[user_id] = updated
            promoted.append(user_id)
        event["signups"] = signups
        return promoted

    async def _send_channel_announcement(
        self,
        channel,
        content: str,
        *,
        user_ids: list[str] | None = None,
        role_ids: list[int] | None = None,
    ) -> None:
        if channel is None:
            return
        mentions = [
            f"<@{user_id}>"
            for user_id in (user_ids or [])
            if str(user_id).isdigit() and f"<@{user_id}>" not in content
        ]
        mentions.extend(
            f"<@&{role_id}>"
            for role_id in (role_ids or [])
            if f"<@&{role_id}>" not in content
        )
        chunks = []
        current = content
        for mention in mentions:
            candidate = f"{current}\n{mention}" if current else mention
            if len(candidate) > 1900:
                chunks.append(current)
                current = mention
            else:
                current = candidate
        if current:
            chunks.append(current)
        allowed_user_ids = sorted({int(user_id) for user_id in (user_ids or []) if str(user_id).isdigit()})
        allowed_role_ids = sorted({int(role_id) for role_id in (role_ids or [])})
        allowed_mentions = discord.AllowedMentions(
            everyone=False,
            users=[discord.Object(id=user_id) for user_id in allowed_user_ids],
            roles=[discord.Object(id=role_id) for role_id in allowed_role_ids],
            replied_user=False,
        )
        for chunk in chunks:
            try:
                await channel.send(chunk, allowed_mentions=allowed_mentions)
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"[rsvp] Failed to send RSVP announcement: {exc}")
                return

    async def finalize_event(self, guild_id: int, *, automatic: bool = False) -> tuple[dict | None, str, list[str]]:
        async with self._locks[guild_id]:
            event = self.get_event(guild_id)
            if not event or event.get("status") != STATUS_ACTIVE:
                if automatic:
                    return event, "skipped", []
                raise ValueError("There is no open RSVP event to finalize.")

            rsvp_count = len(_ordered_signups(event, SIGNUP_RSVP))
            fill_count = len(_ordered_signups(event, SIGNUP_FILL))
            total_available = rsvp_count + fill_count
            promoted = []
            if total_available < RSVP_CAPACITY:
                outcome = STATUS_CANCELLED
                event["status"] = STATUS_CANCELLED
            else:
                outcome = STATUS_CONFIRMED
                promoted = self._promote_fills(event, RSVP_CAPACITY - rsvp_count)
                event["status"] = STATUS_CONFIRMED

            event["finalized_at"] = int(time.time())
            event["finalized_automatically"] = bool(automatic)
            event["updated_at"] = int(time.time())
            self._save_event(guild_id, event)
            self._cancel_checkpoint_task(guild_id)

            message = await self._resolve_message(event)
            if message:
                try:
                    await message.edit(
                        embed=build_rsvp_embed(event),
                        view=self.make_view(event),
                    )
                except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                    print(f"[rsvp] Failed to refresh finalized RSVP for guild {guild_id}: {exc}")

            channel = await self._resolve_channel(event)
            if outcome == STATUS_CANCELLED:
                decision_context = (
                    "At the one-hour confirmation deadline"
                    if automatic
                    else "When an admin finalized the RSVP event"
                )
                participant_ids = [
                    user_id
                    for user_id, _ in (
                        _ordered_signups(event, SIGNUP_RSVP)
                        + _ordered_signups(event, SIGNUP_FILL)
                    )
                ]
                await self._send_channel_announcement(
                    channel,
                    (
                        f"❌ **This inhouse has been called off.** {decision_context}, only "
                        f"**{total_available}/{RSVP_CAPACITY}** players had signed up "
                        f"({rsvp_count} RSVP, {fill_count} fills). We're cancelling now so nobody has to wait around."
                    ),
                    user_ids=participant_ids,
                )
            else:
                confirmed_ids = [user_id for user_id, _ in _ordered_signups(event, SIGNUP_RSVP)]
                promoted_text = ""
                if promoted:
                    promoted_text = "\nFills promoted into the confirmed roster: " + " ".join(
                        f"<@{user_id}>" for user_id in promoted
                    )
                await self._send_channel_announcement(
                    channel,
                    (
                        f"✅ **The inhouse is confirmed for <t:{int(event['start_at'])}:t>.** "
                        f"We have {RSVP_CAPACITY} confirmed players. Please be ready 10 minutes early. "
                        "Confirmed RSVP players cannot withdraw during the final 60 minutes; fills may still withdraw."
                        f"{promoted_text}"
                    ),
                    user_ids=confirmed_ids,
                )
            return event, outcome, promoted

    async def cancel_event(
        self,
        guild_id: int,
        *,
        reason: str = "",
        cancelled_by: str | None = None,
    ) -> dict:
        async with self._locks[guild_id]:
            event = self.get_event(guild_id)
            if not event or event.get("status") not in INTERACTIVE_STATUSES:
                raise ValueError("There is no active or confirmed RSVP event to cancel.")
            event["status"] = STATUS_CANCELLED
            event["cancellation_reason"] = str(reason or "").strip()
            event["cancelled_by"] = str(cancelled_by) if cancelled_by is not None else None
            event["cancelled_at"] = int(time.time())
            event["updated_at"] = int(time.time())
            self._save_event(guild_id, event)
            self._cancel_checkpoint_task(guild_id)

            message = await self._resolve_message(event)
            if message:
                try:
                    await message.edit(
                        embed=build_rsvp_embed(event),
                        view=self.make_view(event),
                    )
                except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                    print(f"[rsvp] Failed to refresh cancelled RSVP for guild {guild_id}: {exc}")

            participant_ids = [
                user_id
                for user_id, _ in (
                    _ordered_signups(event, SIGNUP_RSVP)
                    + _ordered_signups(event, SIGNUP_FILL)
                )
            ]
            reason_text = f" Reason: **{event['cancellation_reason']}**" if event["cancellation_reason"] else ""
            channel = await self._resolve_channel(event)
            await self._send_channel_announcement(
                channel,
                f"❌ **This inhouse has been called off by an admin.**{reason_text}",
                user_ids=participant_ids,
            )
            return event

    async def remove_signup(
        self,
        guild_id: int,
        user_id: int,
        *,
        removed_by: str | None = None,
    ) -> tuple[dict, dict, list[str]]:
        async with self._locks[guild_id]:
            event = self.get_event(guild_id)
            if not event or event.get("status") not in INTERACTIVE_STATUSES:
                raise ValueError("There is no active or confirmed RSVP event to update.")

            signup_key = str(user_id)
            signups = dict(event.get("signups", {}) or {})
            removed = signups.pop(signup_key, None)
            if not removed:
                raise ValueError("That player is not on the RSVP or fill list.")

            event["signups"] = signups
            event["last_admin_removal_by"] = str(removed_by) if removed_by is not None else None
            event["updated_at"] = int(time.time())
            promoted = []
            announcement_user_ids = [signup_key]
            announcement_role_ids = []
            if event.get("status") == STATUS_CONFIRMED and removed.get("status") == SIGNUP_RSVP:
                promoted = self._promote_fills(event, 1)
                if promoted:
                    replacement_id = promoted[0]
                    confirmed_count = len(_ordered_signups(event, SIGNUP_RSVP))
                    announcement = (
                        f"🔄 <@{signup_key}> was removed from the confirmed roster by an admin. "
                        f"<@{replacement_id}> has been promoted from the fill list. "
                        f"The roster is **{confirmed_count}/{RSVP_CAPACITY}**."
                    )
                    announcement_user_ids.append(replacement_id)
                else:
                    confirmed_count = len(_ordered_signups(event, SIGNUP_RSVP))
                    guild = self.bot.get_guild(guild_id)
                    admin_role = discord.utils.get(
                        getattr(guild, "roles", []),
                        name="Inhouse Admin",
                    )
                    role_prefix = f"{admin_role.mention} " if admin_role else "Admins: "
                    announcement = (
                        f"⚠️ {role_prefix}<@{signup_key}> was removed from the confirmed roster and no fill is available. "
                        f"The roster is now **{confirmed_count}/{RSVP_CAPACITY}**. "
                        f"Please find a replacement or use `{self.load_guild_prefix(guild_id)}cancelrsvp [reason]` to call it off."
                    )
                    if admin_role:
                        announcement_role_ids.append(admin_role.id)
            else:
                list_name = "RSVP" if removed.get("status") == SIGNUP_RSVP else "fill"
                announcement = f"📝 <@{signup_key}> was removed from the {list_name} list by an admin."

            self._save_event(guild_id, event)
            message = await self._resolve_message(event)
            if message:
                try:
                    await message.edit(
                        embed=build_rsvp_embed(event),
                        view=self.make_view(event),
                    )
                except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                    print(f"[rsvp] Failed to refresh RSVP after admin removal in guild {guild_id}: {exc}")

            channel = await self._resolve_channel(event)
            await self._send_channel_announcement(
                channel,
                announcement,
                user_ids=announcement_user_ids,
                role_ids=announcement_role_ids,
            )
            return event, removed, promoted

    async def close_event(self, guild_id: int) -> dict:
        async with self._locks[guild_id]:
            event = self.get_event(guild_id)
            if not event or event.get("status") not in INTERACTIVE_STATUSES:
                raise ValueError("There is no active RSVP event to close.")
            event["status"] = STATUS_CLOSED
            event["updated_at"] = int(time.time())
            self._save_event(guild_id, event)
            self._cancel_checkpoint_task(guild_id)
            message = await self._resolve_message(event)
            if message:
                await message.edit(
                    embed=build_rsvp_embed(event),
                    view=self.make_view(event, disabled=True),
                )
            return event

    async def reset_event(self, guild_id: int) -> dict:
        async with self._locks[guild_id]:
            event = self.get_event(guild_id)
            if not event or event.get("status") == STATUS_RESET:
                raise ValueError("There is no RSVP event to reset.")
            event["status"] = STATUS_RESET
            event["signups"] = {}
            event["updated_at"] = int(time.time())
            self._save_event(guild_id, event)
            self._cancel_checkpoint_task(guild_id)
            message = await self._resolve_message(event)
            if message:
                await message.edit(
                    embed=build_rsvp_embed(event),
                    view=self.make_view(event, disabled=True),
                )
            return event

    async def _send_ephemeral(self, interaction: discord.Interaction, content: str) -> None:
        try:
            message = await interaction.followup.send(
                content,
                ephemeral=True,
                wait=True,
            )
        except discord.HTTPException:
            return
        if message is None:
            return
        task = asyncio.create_task(self._delete_ephemeral_after(message))
        self._ephemeral_delete_tasks.add(task)
        task.add_done_callback(self._ephemeral_delete_tasks.discard)

    async def _delete_ephemeral_after(self, message) -> None:
        await asyncio.sleep(12)
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass

    def _configuration_error(self, guild_id: int, user_id: int) -> str | None:
        config = self.load_player_config(str(user_id)) or {}
        steam_id = config.get("steam_id")
        mmr = config.get("mmr")
        if steam_id and isinstance(mmr, (int, float)) and not isinstance(mmr, bool):
            return None
        prefix = self.load_guild_prefix(guild_id)
        return (
            "You must configure your Steam account and MMR before signing up. "
            f"Run `{prefix}cfg <Steam friend code>` (example: `{prefix}cfg 123456789`). "
            "If your Steam account is already linked but has no MMR, rerun the command or ask an admin for help."
        )

    async def handle_signup(self, interaction: discord.Interaction, action: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=False)
        if interaction.guild is None or interaction.message is None:
            await self._send_ephemeral(interaction, "RSVP buttons can only be used in the event's server.")
            return

        guild_id = interaction.guild.id
        announcement = None
        announcement_user_ids = []
        announcement_role_ids = []
        try:
            async with self._locks[guild_id]:
                event = self.get_event(guild_id)
                if not event or event.get("status") not in INTERACTIVE_STATUSES:
                    await self._send_ephemeral(interaction, "This RSVP event is no longer active.")
                    return
                if str(interaction.message.id) != str(event.get("message_id")):
                    await self._send_ephemeral(interaction, "This is not the server's current RSVP event.")
                    return

                event_status = event.get("status")
                user_id = str(interaction.user.id)
                signups = dict(event.get("signups", {}) or {})
                current = signups.get(user_id)

                if action in {SIGNUP_RSVP, SIGNUP_FILL}:
                    configuration_error = self._configuration_error(guild_id, interaction.user.id)
                    if configuration_error:
                        await self._send_ephemeral(interaction, configuration_error)
                        return

                if action == SIGNUP_RSVP:
                    if event_status == STATUS_CONFIRMED:
                        await self._send_ephemeral(
                            interaction,
                            "The confirmed roster is locked. Use **Fill** if you want to be available as a replacement.",
                        )
                        return
                    if current and current.get("status") == SIGNUP_RSVP:
                        await self._send_ephemeral(interaction, "You are already RSVP'd for this inhouse.")
                        return
                    if len(_ordered_signups(event, SIGNUP_RSVP)) >= RSVP_CAPACITY:
                        await self._send_ephemeral(
                            interaction,
                            "The RSVP list is full (10/10). Use **Fill** if you want to be a standby.",
                        )
                        return
                    confirmation = "You're RSVP'd for this inhouse."
                elif action == SIGNUP_FILL:
                    if event_status == STATUS_CONFIRMED and current and current.get("status") == SIGNUP_RSVP:
                        await self._send_ephemeral(
                            interaction,
                            "You are already a confirmed player. Use **Withdraw** if you can no longer play.",
                        )
                        return
                    if current and current.get("status") == SIGNUP_FILL:
                        await self._send_ephemeral(interaction, "You are already listed as a fill.")
                        return
                    confirmation = (
                        "You're listed as a fill. If needed, FeederBot may promote you into the confirmed roster."
                    )
                elif action == "withdraw":
                    if not current:
                        await self._send_ephemeral(interaction, "You are not currently signed up for this inhouse.")
                        return
                    if (
                        event_status == STATUS_CONFIRMED
                        and current.get("status") == SIGNUP_RSVP
                        and not _is_promoted_fill(current)
                    ):
                        await self._send_ephemeral(
                            interaction,
                            "Your confirmed RSVP is locked and cannot be withdrawn. Contact an Inhouse Admin if an emergency prevents you from playing.",
                        )
                        return
                    withdrawn_status = current.get("status")
                    signups.pop(user_id, None)
                    confirmation = "You have withdrawn from this inhouse."
                else:
                    await self._send_ephemeral(interaction, "Unknown RSVP action.")
                    return

                if action in {SIGNUP_RSVP, SIGNUP_FILL}:
                    signups[user_id] = {
                        "status": action,
                        "signup_origin": action,
                        "display_name": interaction.user.display_name,
                        "joined_at": int(time.time()),
                    }

                event["signups"] = signups
                if event_status == STATUS_CONFIRMED:
                    confirmed_count = len(_ordered_signups(event, SIGNUP_RSVP))
                    if action == SIGNUP_FILL and confirmed_count < RSVP_CAPACITY:
                        promoted = self._promote_fills(event, RSVP_CAPACITY - confirmed_count)
                        if promoted:
                            new_count = len(_ordered_signups(event, SIGNUP_RSVP))
                            announcement = (
                                "✅ "
                                + " ".join(f"<@{promoted_id}>" for promoted_id in promoted)
                                + f" filled the open confirmed slot. The roster is now **{new_count}/{RSVP_CAPACITY}**."
                            )
                            announcement_user_ids = promoted
                            if user_id in promoted:
                                confirmation = "A confirmed slot was open, so you have been added to the confirmed roster."
                    elif action == "withdraw" and withdrawn_status == SIGNUP_RSVP:
                        promoted = self._promote_fills(event, 1)
                        if promoted:
                            replacement_id = promoted[0]
                            new_count = len(_ordered_signups(event, SIGNUP_RSVP))
                            announcement = (
                                f"🔄 <@{user_id}> withdrew from the confirmed roster. "
                                f"<@{replacement_id}> has been promoted from the fill list. "
                                f"The roster is **{new_count}/{RSVP_CAPACITY}**."
                            )
                            announcement_user_ids = [replacement_id]
                        else:
                            new_count = len(_ordered_signups(event, SIGNUP_RSVP))
                            admin_role = discord.utils.get(
                                getattr(interaction.guild, "roles", []),
                                name="Inhouse Admin",
                            )
                            role_prefix = f"{admin_role.mention} " if admin_role else "Admins: "
                            announcement = (
                                f"⚠️ {role_prefix}a confirmed player withdrew and no fill is available. "
                                f"The roster is now **{new_count}/{RSVP_CAPACITY}**. "
                                f"Please find a replacement or use `{self.load_guild_prefix(guild_id)}cancelrsvp [reason]` to call it off."
                            )
                            if admin_role:
                                announcement_role_ids = [admin_role.id]

                event["updated_at"] = int(time.time())
                self._save_event(guild_id, event)
                await interaction.message.edit(
                    embed=build_rsvp_embed(event),
                    view=self.make_view(event),
                )
            if announcement:
                await self._send_channel_announcement(
                    interaction.channel,
                    announcement,
                    user_ids=announcement_user_ids,
                    role_ids=announcement_role_ids,
                )
            await self._send_ephemeral(interaction, confirmation)
        except Exception as exc:
            print(f"[rsvp] Failed to process {action} for guild {guild_id}: {exc}")
            await self._send_ephemeral(
                interaction,
                "I couldn't update the RSVP list. Please try again in a moment.",
            )

    async def restore_active_events(self) -> None:
        self.register_persistent_view()
        try:
            snapshots = self.db.collection(RSVP_COLLECTION).stream()
            for snapshot in snapshots:
                event = snapshot.to_dict() or {}
                if event.get("status") not in INTERACTIVE_STATUSES:
                    continue
                try:
                    guild_id = int(event.get("guild_id") or snapshot.id)
                except (TypeError, ValueError):
                    continue
                if self.bot.get_guild(guild_id) is None:
                    continue
                if event.get("status") == STATUS_ACTIVE:
                    checkpoint_at = int(
                        event.get("checkpoint_at", 0)
                        or (int(event.get("start_at", 0) or 0) - RSVP_CONFIRMATION_LEAD_SECONDS)
                    )
                    if not event.get("checkpoint_at"):
                        event["checkpoint_at"] = checkpoint_at
                        self._save_event(guild_id, event)
                    if checkpoint_at <= int(time.time()):
                        await self.finalize_event(guild_id, automatic=True)
                        print(f"[rsvp] Finalized overdue RSVP event for guild {guild_id}")
                        continue
                    self._schedule_checkpoint(event)
                message = await self._resolve_message(event)
                if message is None:
                    print(f"[rsvp] Could not restore RSVP message for guild {guild_id}")
                    continue
                try:
                    await message.edit(
                        embed=build_rsvp_embed(event),
                        view=self.make_view(event),
                    )
                except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                    print(f"[rsvp] Failed to refresh RSVP message for guild {guild_id}: {exc}")
                    continue
                print(f"[rsvp] Restored interactive RSVP event for guild {guild_id}")
        except Exception as exc:
            print(f"[rsvp] Failed to restore active RSVP events: {exc}")
