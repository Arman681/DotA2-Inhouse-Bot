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
RSVP_LOBBY_OPEN_LEAD_SECONDS = 5 * 60

STATUS_ACTIVE = "active"
STATUS_CONFIRMED = "confirmed"
STATUS_CANCELLED = "cancelled"
STATUS_CLOSED = "closed"
STATUS_RESET = "reset"
STATUS_STARTING = "starting"
STATUS_LOBBY_STARTING = "lobby_starting"
STATUS_LOBBY_OPEN = "lobby_open"
STATUS_START_FAILED = "start_failed"
STATUS_COMPLETED = "completed"

SERIES_WAITING = "waiting_for_match"
SERIES_LIVE = "game_live"
SERIES_BETWEEN_GAMES = "between_games"
SERIES_WAIT_TIMED_OUT = "wait_timed_out"
SERIES_ROSTER_INCOMPLETE = "roster_incomplete"
SERIES_RESULT_PENDING = "result_pending"
SERIES_PREPARATION_FAILED = "preparation_failed"
SERIES_COMPLETED = "completed"

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


def _is_pending_finalization(event: dict | None) -> bool:
    if not isinstance(event, dict):
        return False
    status = event.get("status")
    if status == STATUS_ACTIVE:
        return True
    if status != STATUS_CLOSED:
        return False
    return event.get("closed_from_status", STATUS_ACTIVE) == STATUS_ACTIVE


def _is_pending_lobby_start(event: dict | None) -> bool:
    if not isinstance(event, dict):
        return False
    status = event.get("status")
    if status in {STATUS_CONFIRMED, STATUS_LOBBY_STARTING}:
        return True
    return status == STATUS_CLOSED and event.get("closed_from_status") == STATUS_CONFIRMED


def _lobby_open_at(event: dict) -> int:
    start_at = int(event.get("start_at", 0) or 0)
    return int(
        event.get("lobby_open_at", 0)
        or (start_at - RSVP_LOBBY_OPEN_LEAD_SECONDS if start_at else 0)
    )


def _is_active_series(event: dict | None) -> bool:
    if not isinstance(event, dict) or event.get("status") != STATUS_LOBBY_OPEN:
        return False
    games_planned = max(1, int(event.get("games", 1) or 1))
    games_completed = max(0, int(event.get("games_completed", 0) or 0))
    return games_completed < games_planned


def _format_roster(event: dict, signup_status: str, empty_text: str) -> str:
    rows = _ordered_signups(event, signup_status)
    if not rows:
        return empty_text

    lines = []
    for index, (user_id, data) in enumerate(rows, start=1):
        fallback_name = f"User {user_id}"
        display_name = discord.utils.escape_markdown(str(data.get("display_name") or fallback_name))
        line = f"`{index}.` **{display_name}**"
        details = []
        mmr = data.get("mmr")
        if isinstance(mmr, (int, float)) and not isinstance(mmr, bool):
            details.append(f"{int(mmr):,} MMR")
        games = int(data.get("inhouse_games", 0) or 0)
        win_rate = data.get("inhouse_win_rate")
        if games > 0 and isinstance(win_rate, (int, float)) and not isinstance(win_rate, bool):
            wins = int(data.get("inhouse_wins", 0) or 0)
            losses = int(data.get("inhouse_losses", 0) or 0)
            details.append(f"{float(win_rate):.1f}% WR ({wins}-{losses})")
        if details:
            line += " — " + " • ".join(details)
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
    elif status == STATUS_LOBBY_STARTING:
        title += " — Opening Lobby"
        color = discord.Color.gold()
        description = "The five-minute pre-game window has arrived. FeederBot is preparing the confirmed roster's lobby."
    elif status == STATUS_LOBBY_OPEN:
        games_planned = max(1, int(event.get("games", 1) or 1))
        games_completed = max(0, int(event.get("games_completed", 0) or 0))
        current_game = min(games_planned, max(1, int(event.get("current_game", games_completed + 1) or 1)))
        series_status = str(event.get("series_status") or SERIES_WAITING)
        series_labels = {
            SERIES_WAITING: (f"Waiting for Game {current_game}/{games_planned}", discord.Color.green()),
            SERIES_LIVE: (f"Game {current_game}/{games_planned} Live", discord.Color.green()),
            SERIES_BETWEEN_GAMES: (f"Preparing Game {current_game}/{games_planned}", discord.Color.gold()),
            SERIES_WAIT_TIMED_OUT: (f"Game {current_game}/{games_planned} Wait Timed Out", discord.Color.orange()),
            SERIES_ROSTER_INCOMPLETE: (f"Game {current_game}/{games_planned} Roster Incomplete", discord.Color.orange()),
            SERIES_RESULT_PENDING: (f"Game {current_game}/{games_planned} Result Pending", discord.Color.orange()),
            SERIES_PREPARATION_FAILED: (f"Game {current_game}/{games_planned} Setup Blocked", discord.Color.red()),
        }
        status_label, color = series_labels.get(
            series_status,
            (f"Lobby Open — Game {current_game}/{games_planned}", discord.Color.green()),
        )
        title += f" — {status_label}"
        lobby_link = str(event.get("lobby_jump_url") or "").strip()
        mode = str(event.get("lobby_mode") or "regular").capitalize()
        if series_status == SERIES_LIVE:
            match_id = str(event.get("current_match_id") or "").strip()
            match_text = f" `{match_id}`" if match_id else ""
            description = f"Game **{current_game}/{games_planned}**{match_text} is live and being tracked."
        elif series_status == SERIES_BETWEEN_GAMES:
            description = f"Game **{games_completed}/{games_planned}** is recorded. FeederBot is preparing the next game."
        elif series_status == SERIES_WAIT_TIMED_OUT:
            description = (
                f"No new Steam match appeared for game **{current_game}/{games_planned}** within 15 minutes. "
                "An Inhouse Admin can press 🚀 on the locked lobby to retry the same game."
            )
        elif series_status == SERIES_ROSTER_INCOMPLETE:
            description = (
                f"The locked roster fell below 10 players while waiting for game **{current_game}/{games_planned}**. "
                "An Inhouse Admin must restore the roster and press 🚀 to retry."
            )
        elif series_status == SERIES_RESULT_PENDING:
            match_id = str(event.get("current_match_id") or "unknown")
            description = (
                f"Game **{current_game}/{games_planned}** ended, but result `{match_id}` is not available yet. "
                "The series is paused so the game is not skipped; an admin can resolve it with `!submitmatch`."
            )
        elif series_status == SERIES_PREPARATION_FAILED:
            reason = str(event.get("series_error") or "The next lobby setup could not be generated.")
            description = f"The series is paused before game **{current_game}/{games_planned}**.\n\n**Reason:** {reason}"
        elif event.get("handoff_auto_rolled", True):
            description = (
                f"The confirmed roster is in a locked **{mode}** lobby. FeederBot is waiting for game "
                f"**{current_game}/{games_planned}** to appear on Steam."
            )
        else:
            description = (
                f"The confirmed roster has been moved into a locked **{mode}** lobby, but an admin must press 🚀 after resolving the team-generation warning."
            )
        if lobby_link:
            description += f"\n\n[Open the playable lobby]({lobby_link})"
    elif status == STATUS_COMPLETED:
        games_planned = max(1, int(event.get("games", 1) or 1))
        games_completed = max(0, int(event.get("games_completed", 0) or 0))
        title += f" — Series Complete ({games_completed}/{games_planned})"
        color = discord.Color.green()
        description = "All scheduled games have been recorded. This RSVP series is complete."
        lobby_link = str(event.get("lobby_jump_url") or "").strip()
        if lobby_link:
            description += f"\n\n[Open the final lobby]({lobby_link})"
    elif status == STATUS_START_FAILED:
        title += " — Lobby Start Blocked"
        color = discord.Color.red()
        reason = str(event.get("handoff_error") or "The playable lobby could not be prepared.")
        description = f"The RSVP was confirmed, but FeederBot could not open its lobby automatically.\n\n**Reason:** {reason}"
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
        if _is_pending_finalization(event):
            description = (
                "Signups are temporarily closed. The one-hour go/no-go decision remains scheduled, "
                "and the current roster is shown below."
            )
        else:
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
    embed.add_field(name="Format", value=f"**{games} {game_label}**\nCaptain's Mode", inline=True)
    if status in {STATUS_LOBBY_OPEN, STATUS_COMPLETED}:
        games_completed = max(0, int(event.get("games_completed", 0) or 0))
        current_game = min(games, max(1, int(event.get("current_game", games_completed + 1) or 1)))
        progress_value = f"**{games_completed}/{games} complete**"
        if status == STATUS_LOBBY_OPEN:
            progress_value += f"\nCurrent: game {current_game}/{games}"
        embed.add_field(name="Series Progress", value=progress_value, inline=True)
    embed.add_field(
        name="Go / No-Go Policy",
        value=(
            "FeederBot confirms the inhouse only if at least **10 total RSVPs and fills** are available one hour before start, "
            "or calls off the inhouse if there are not enough players in order to respect everyone’s time.\n"
            "Once confirmed, RSVP players cannot withdraw during the final 60 minutes; fills may still withdraw."
        ),
        inline=False,
    )
    embed.add_field(
        name="Rules",
        value="• No unnecessary pausing.\n• No fake GG calls.",
        inline=False,
    )

    rsvp_heading = (
        "Confirmed Players"
        if status in {STATUS_CONFIRMED, STATUS_LOBBY_STARTING, STATUS_LOBBY_OPEN, STATUS_START_FAILED, STATUS_COMPLETED}
        else "RSVP"
    )
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
    elif status == STATUS_LOBBY_OPEN:
        embed.set_footer(text="Scheduled series is active • Roster remains locked between games • Last updated")
    elif status == STATUS_COMPLETED:
        embed.set_footer(text="Scheduled series complete • Last updated")
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

    def __init__(
        self,
        *,
        bot,
        db,
        load_player_config,
        load_guild_prefix,
        load_inhouse_record=None,
    ):
        self.bot = bot
        self.db = db
        self.load_player_config = load_player_config
        self.load_guild_prefix = load_guild_prefix
        self.load_inhouse_record = load_inhouse_record
        self.lobby_handoff = None
        self.series_resume = None
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._persistent_view_registered = False
        self._ephemeral_delete_tasks: set[asyncio.Task] = set()
        self._checkpoint_tasks: dict[int, asyncio.Task] = {}
        self._lobby_start_tasks: dict[int, asyncio.Task] = {}

    def configure_lobby_handoff(self, callback) -> None:
        self.lobby_handoff = callback

    def configure_series_resume(self, callback) -> None:
        self.series_resume = callback

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

    async def _refresh_event_message(self, event: dict) -> None:
        message = await self._resolve_message(event)
        if message is None:
            return
        try:
            await message.edit(
                embed=build_rsvp_embed(event),
                view=self.make_view(event, disabled=event.get("status") not in INTERACTIVE_STATUSES),
            )
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            print(f"[rsvp] Failed to refresh RSVP series for guild {event.get('guild_id')}: {exc}")

    def _cancel_checkpoint_task(self, guild_id: int) -> None:
        task = self._checkpoint_tasks.pop(guild_id, None)
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()

    def _cancel_lobby_start_task(self, guild_id: int) -> None:
        task = self._lobby_start_tasks.pop(guild_id, None)
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()

    def _schedule_checkpoint(self, event: dict) -> None:
        guild_id = int(event.get("guild_id", 0) or 0)
        if not guild_id or not _is_pending_finalization(event):
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

    def _schedule_lobby_start(self, event: dict) -> None:
        guild_id = int(event.get("guild_id", 0) or 0)
        if not guild_id or not _is_pending_lobby_start(event):
            return
        self._cancel_lobby_start_task(guild_id)
        lobby_open_at = _lobby_open_at(event)
        if not event.get("lobby_open_at"):
            event["lobby_open_at"] = lobby_open_at
            self._save_event(guild_id, event)
        delay = max(0.0, lobby_open_at - time.time())
        task = asyncio.create_task(self._run_lobby_start(guild_id, delay))
        self._lobby_start_tasks[guild_id] = task

    async def _run_lobby_start(self, guild_id: int, delay: int) -> None:
        current_task = asyncio.current_task()
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            await self.open_confirmed_lobby(guild_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[rsvp] Automatic lobby start failed for guild {guild_id}: {exc}")
        finally:
            if self._lobby_start_tasks.get(guild_id) is current_task:
                self._lobby_start_tasks.pop(guild_id, None)

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
                STATUS_CLOSED,
                STATUS_STARTING,
                STATUS_LOBBY_STARTING,
                STATUS_LOBBY_OPEN,
            }:
                raise ValueError(
                    "This server already has a running RSVP event or scheduled series. Finish or reset it first, "
                    "or finalize it if it is still awaiting the go/no-go decision."
                )

            self._cancel_checkpoint_task(guild_id)
            self._cancel_lobby_start_task(guild_id)

            now_epoch = int(time.time())
            start_at = int(start_time.astimezone(timezone.utc).timestamp())
            checkpoint_at = start_at - RSVP_CONFIRMATION_LEAD_SECONDS
            lobby_open_at = start_at - RSVP_LOBBY_OPEN_LEAD_SECONDS
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
                "lobby_open_at": lobby_open_at,
                "games": int(games),
                "games_completed": 0,
                "completed_match_ids": [],
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
            if not _is_pending_finalization(event):
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
            if outcome == STATUS_CONFIRMED:
                self._schedule_lobby_start(event)
            else:
                self._cancel_lobby_start_task(guild_id)

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
                        "Confirmed RSVP players cannot withdraw during the final 60 minutes; fills may still withdraw. "
                        "Five minutes before the scheduled start, FeederBot will automatically open the locked playable lobby and generate teams or captains."
                        f"{promoted_text}"
                    ),
                    user_ids=confirmed_ids,
                )
            return event, outcome, promoted

    async def _announce_handoff_failure(self, event: dict, reason: str) -> None:
        guild_id = int(event.get("guild_id", 0) or 0)
        guild = self.bot.get_guild(guild_id)
        admin_role = discord.utils.get(getattr(guild, "roles", []), name="Inhouse Admin")
        role_ids = [admin_role.id] if admin_role else []
        role_prefix = f"{admin_role.mention} " if admin_role else "Admins: "
        channel = await self._resolve_channel(event)
        await self._send_channel_announcement(
            channel,
            (
                f"⚠️ {role_prefix}the confirmed RSVP reached its five-minute pre-game lobby window, "
                f"but FeederBot could not open the playable lobby automatically. **{reason}**"
            ),
            role_ids=role_ids,
        )

    async def open_confirmed_lobby(self, guild_id: int) -> tuple[dict | None, dict | None]:
        handoff_event = None
        failure_reason = None
        async with self._locks[guild_id]:
            event = self.get_event(guild_id)
            if not _is_pending_lobby_start(event):
                return event, None
            confirmed_rows = _ordered_signups(event, SIGNUP_RSVP)[:RSVP_CAPACITY]
            if len(confirmed_rows) != RSVP_CAPACITY:
                failure_reason = (
                    f"The confirmed roster is only {len(confirmed_rows)}/{RSVP_CAPACITY}; "
                    "a full roster is required for automatic team generation."
                )
            elif self.lobby_handoff is None:
                failure_reason = "The RSVP-to-lobby handoff is not configured."

            event["handoff_attempted_at"] = int(time.time())
            event["updated_at"] = int(time.time())
            if failure_reason:
                event["status"] = STATUS_START_FAILED
                event["handoff_error"] = failure_reason
            else:
                event["status"] = STATUS_LOBBY_STARTING
                event.pop("handoff_error", None)
                handoff_event = {
                    **event,
                    "signups": dict(event.get("signups", {}) or {}),
                }
            self._save_event(guild_id, event)

        message = await self._resolve_message(event)
        if message:
            try:
                await message.edit(
                    embed=build_rsvp_embed(event),
                    view=self.make_view(event, disabled=True),
                )
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                print(f"[rsvp] Failed to show lobby handoff state for guild {guild_id}: {exc}")

        if failure_reason:
            await self._announce_handoff_failure(event, failure_reason)
            return event, None

        try:
            result = await self.lobby_handoff(handoff_event)
            if not isinstance(result, dict) or not result.get("message_id"):
                raise RuntimeError("The playable lobby was not created successfully.")
        except Exception as exc:
            failure_reason = str(exc).strip() or "The playable lobby could not be created."
            async with self._locks[guild_id]:
                event = self.get_event(guild_id) or handoff_event
                event["status"] = STATUS_START_FAILED
                event["handoff_error"] = failure_reason[:500]
                event["updated_at"] = int(time.time())
                self._save_event(guild_id, event)
            message = await self._resolve_message(event)
            if message:
                try:
                    await message.edit(
                        embed=build_rsvp_embed(event),
                        view=self.make_view(event, disabled=True),
                    )
                except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                    pass
            await self._announce_handoff_failure(event, failure_reason)
            return event, None

        async with self._locks[guild_id]:
            event = self.get_event(guild_id) or handoff_event
            now_epoch = int(time.time())
            event["status"] = STATUS_LOBBY_OPEN
            event["lobby_message_id"] = str(result["message_id"])
            event["lobby_jump_url"] = str(result.get("jump_url") or "")
            event["lobby_mode"] = str(result.get("mode") or "regular")
            event["handoff_auto_rolled"] = bool(result.get("auto_rolled", False))
            event["games_completed"] = 0
            event["completed_match_ids"] = []
            event["current_game"] = 1
            event["series_status"] = (
                SERIES_WAITING if event["handoff_auto_rolled"] else SERIES_PREPARATION_FAILED
            )
            if event["handoff_auto_rolled"]:
                event["wait_started_at"] = now_epoch
                event["wait_deadline_at"] = now_epoch + (15 * 60)
            else:
                event["series_error"] = "Automatic team or captain generation did not complete."
            event["lobby_opened_at"] = now_epoch
            event["updated_at"] = now_epoch
            self._save_event(guild_id, event)
            self._cancel_lobby_start_task(guild_id)

        message = await self._resolve_message(event)
        if message:
            try:
                await message.edit(
                    embed=build_rsvp_embed(event),
                    view=self.make_view(event, disabled=True),
                )
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                print(f"[rsvp] Failed to show opened lobby for guild {guild_id}: {exc}")

        confirmed_ids = [user_id for user_id, _ in _ordered_signups(event, SIGNUP_RSVP)[:RSVP_CAPACITY]]
        lobby_link = str(result.get("jump_url") or "").strip()
        link_text = f" [Open the lobby]({lobby_link})" if lobby_link else ""
        await self._send_channel_announcement(
            await self._resolve_channel(event),
            (
                f"🚀 **The scheduled inhouse lobby is open in {str(result.get('mode') or 'regular').capitalize()} mode.**"
                f"{link_text} The inhouse starts at <t:{int(event.get('start_at', 0) or 0)}:t>. "
                "The confirmed roster is locked; use an Inhouse Admin for emergency replacements."
            ),
            user_ids=confirmed_ids,
        )
        return event, result

    async def mark_series_waiting(
        self,
        guild_id: int,
        *,
        game_number: int | None = None,
        timeout_seconds: int = 15 * 60,
    ) -> dict | None:
        async with self._locks[guild_id]:
            event = self.get_event(guild_id)
            if not _is_active_series(event):
                return None
            current_game = max(1, int(event.get("current_game", 1) or 1))
            if game_number is not None and int(game_number) != current_game:
                return None
            now_epoch = int(time.time())
            event["series_status"] = SERIES_WAITING
            event["wait_started_at"] = now_epoch
            event["wait_deadline_at"] = now_epoch + max(1, int(timeout_seconds))
            event.pop("current_match_id", None)
            event.pop("series_error", None)
            event["updated_at"] = now_epoch
            self._save_event(guild_id, event)
        await self._refresh_event_message(event)
        return event

    async def mark_series_wait_outcome(
        self,
        guild_id: int,
        outcome: str,
        *,
        game_number: int | None = None,
        match_id=None,
    ) -> dict | None:
        async with self._locks[guild_id]:
            event = self.get_event(guild_id)
            if not _is_active_series(event):
                return None
            current_game = max(1, int(event.get("current_game", 1) or 1))
            if game_number is not None and int(game_number) != current_game:
                return None
            now_epoch = int(time.time())
            if outcome == "match_found":
                event["series_status"] = SERIES_LIVE
                event["current_match_id"] = str(match_id)
                event["match_found_at"] = now_epoch
            elif outcome == "timeout":
                event["series_status"] = SERIES_WAIT_TIMED_OUT
                event["wait_timed_out_at"] = now_epoch
                event.pop("current_match_id", None)
            elif outcome == "underfilled":
                event["series_status"] = SERIES_ROSTER_INCOMPLETE
                event["roster_incomplete_at"] = now_epoch
                event.pop("current_match_id", None)
            else:
                return None
            event["updated_at"] = now_epoch
            self._save_event(guild_id, event)
        await self._refresh_event_message(event)
        return event

    async def mark_series_result_pending(self, guild_id: int, match_id) -> dict | None:
        async with self._locks[guild_id]:
            event = self.get_event(guild_id)
            if not _is_active_series(event):
                return None
            expected_match_id = str(event.get("current_match_id") or "")
            if expected_match_id and expected_match_id != str(match_id):
                return None
            now_epoch = int(time.time())
            event["series_status"] = SERIES_RESULT_PENDING
            event["current_match_id"] = str(match_id)
            event["result_pending_at"] = now_epoch
            event["updated_at"] = now_epoch
            self._save_event(guild_id, event)
        await self._refresh_event_message(event)
        return event

    async def mark_series_preparation_failed(self, guild_id: int, reason: str) -> dict | None:
        async with self._locks[guild_id]:
            event = self.get_event(guild_id)
            if not _is_active_series(event):
                return None
            event["series_status"] = SERIES_PREPARATION_FAILED
            event["series_error"] = str(reason or "The next game setup could not be generated.")[:500]
            event["updated_at"] = int(time.time())
            self._save_event(guild_id, event)
        await self._refresh_event_message(event)
        return event

    async def record_series_match(
        self,
        guild_id: int,
        match_id,
        *,
        require_current_match: bool = False,
    ) -> dict:
        normalized_match_id = str(match_id)
        async with self._locks[guild_id]:
            event = self.get_event(guild_id)
            if not event:
                return {"event": None, "counted": False, "reason": "no_event"}
            completed_match_ids = [str(value) for value in (event.get("completed_match_ids", []) or [])]
            games_planned = max(1, int(event.get("games", 1) or 1))
            games_completed = max(0, int(event.get("games_completed", 0) or 0))
            if normalized_match_id in completed_match_ids:
                return {
                    "event": event,
                    "counted": False,
                    "duplicate": True,
                    "series_complete": games_completed >= games_planned,
                    "games_completed": games_completed,
                    "games_planned": games_planned,
                }
            if not _is_active_series(event):
                return {"event": event, "counted": False, "reason": "no_active_series"}
            current_match_id = str(event.get("current_match_id") or "")
            if current_match_id and current_match_id != normalized_match_id:
                return {"event": event, "counted": False, "reason": "unexpected_match"}
            if require_current_match and current_match_id != normalized_match_id:
                return {"event": event, "counted": False, "reason": "untracked_match"}

            completed_match_ids.append(normalized_match_id)
            games_completed = min(games_planned, games_completed + 1)
            now_epoch = int(time.time())
            event["completed_match_ids"] = completed_match_ids
            event["games_completed"] = games_completed
            event["last_completed_match_id"] = normalized_match_id
            event["last_game_completed_at"] = now_epoch
            event.pop("current_match_id", None)
            event.pop("series_error", None)
            series_complete = games_completed >= games_planned
            if series_complete:
                event["status"] = STATUS_COMPLETED
                event["series_status"] = SERIES_COMPLETED
                event["current_game"] = games_planned
                event["series_completed_at"] = now_epoch
            else:
                event["series_status"] = SERIES_BETWEEN_GAMES
                event["current_game"] = games_completed + 1
            event["updated_at"] = now_epoch
            self._save_event(guild_id, event)
        await self._refresh_event_message(event)
        return {
            "event": event,
            "counted": True,
            "duplicate": False,
            "series_complete": series_complete,
            "games_completed": games_completed,
            "games_planned": games_planned,
            "next_game": None if series_complete else games_completed + 1,
        }

    async def retire_series_for_lobby_override(
        self,
        guild_id: int,
        *,
        reset_by: str | None = None,
    ) -> dict | None:
        async with self._locks[guild_id]:
            event = self.get_event(guild_id)
            if not _is_active_series(event):
                return None
            now_epoch = int(time.time())
            event["status"] = STATUS_RESET
            event["series_status"] = "overridden"
            event["reset_by"] = str(reset_by) if reset_by is not None else None
            event["reset_at"] = now_epoch
            event["updated_at"] = now_epoch
            self._save_event(guild_id, event)
        await self._refresh_event_message(event)
        return event

    async def cancel_event(
        self,
        guild_id: int,
        *,
        reason: str = "",
        cancelled_by: str | None = None,
    ) -> dict:
        async with self._locks[guild_id]:
            event = self.get_event(guild_id)
            if not event or event.get("status") not in (INTERACTIVE_STATUSES | {STATUS_CLOSED}):
                raise ValueError("There is no active, closed, or confirmed RSVP event to cancel.")
            event["status"] = STATUS_CANCELLED
            event["cancellation_reason"] = str(reason or "").strip()
            event["cancelled_by"] = str(cancelled_by) if cancelled_by is not None else None
            event["cancelled_at"] = int(time.time())
            event["updated_at"] = int(time.time())
            self._save_event(guild_id, event)
            self._cancel_checkpoint_task(guild_id)
            self._cancel_lobby_start_task(guild_id)

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
            previous_status = event.get("status")
            event["status"] = STATUS_CLOSED
            event["closed_from_status"] = previous_status
            event["closed_at"] = int(time.time())
            event["updated_at"] = int(time.time())
            self._save_event(guild_id, event)
            if previous_status == STATUS_ACTIVE:
                self._schedule_checkpoint(event)
            else:
                self._cancel_checkpoint_task(guild_id)
                self._schedule_lobby_start(event)
            message = await self._resolve_message(event)
            if message:
                await message.edit(
                    embed=build_rsvp_embed(event),
                    view=self.make_view(event, disabled=True),
                )
            return event

    async def reset_event(
        self,
        guild_id: int,
        *,
        reset_by: str | None = None,
    ) -> tuple[dict, discord.Message | None, bool]:
        async with self._locks[guild_id]:
            event = self.get_event(guild_id)
            if not event or event.get("status") == STATUS_RESET:
                raise ValueError("There is no RSVP event to reset.")

            self._cancel_lobby_start_task(guild_id)

            original_event = {
                **event,
                "signups": dict(event.get("signups", {}) or {}),
            }
            old_message = await self._resolve_message(event)
            now_epoch = int(time.time())
            checkpoint_at = int(
                event.get("checkpoint_at", 0)
                or (int(event.get("start_at", 0) or 0) - RSVP_CONFIRMATION_LEAD_SECONDS)
            )

            if checkpoint_at <= now_epoch:
                self._cancel_checkpoint_task(guild_id)
                event["status"] = STATUS_RESET
                event["signups"] = {}
                event["message_id"] = None
                event["reset_by"] = str(reset_by) if reset_by is not None else None
                event["reset_at"] = now_epoch
                event["updated_at"] = now_epoch
                self._save_event(guild_id, event)
                if old_message:
                    try:
                        await old_message.delete()
                    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                        try:
                            await old_message.edit(
                                embed=build_rsvp_embed(event),
                                view=self.make_view(event, disabled=True),
                            )
                        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                            pass
                return event, None, False

            channel = await self._resolve_channel(event)
            if channel is None:
                raise ValueError("I could not access the RSVP channel to post the reset event.")

            self._cancel_checkpoint_task(guild_id)
            for key in (
                "finalized_at",
                "finalized_automatically",
                "cancellation_reason",
                "cancelled_by",
                "cancelled_at",
                "closed_from_status",
                "closed_at",
                "last_admin_removal_by",
            ):
                event.pop(key, None)
            event["status"] = STATUS_STARTING
            event["signups"] = {}
            event["message_id"] = None
            event["reset_by"] = str(reset_by) if reset_by is not None else None
            event["reset_at"] = now_epoch
            event["updated_at"] = now_epoch
            self._save_event(guild_id, event)

            active_preview = dict(event)
            active_preview["status"] = STATUS_ACTIVE
            try:
                new_message = await channel.send(
                    embed=build_rsvp_embed(active_preview),
                    view=self.make_view(active_preview),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                self._save_event(guild_id, original_event)
                self._schedule_checkpoint(original_event)
                raise

            event["message_id"] = str(new_message.id)
            event["status"] = STATUS_ACTIVE
            event["updated_at"] = int(time.time())
            self._save_event(guild_id, event)
            self._schedule_checkpoint(event)

            if old_message:
                try:
                    await old_message.delete()
                except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                    print(f"[rsvp] Failed to delete old RSVP message for guild {guild_id}: {exc}")
                    retired_event = dict(event)
                    retired_event["status"] = STATUS_RESET
                    try:
                        await old_message.edit(
                            embed=build_rsvp_embed(retired_event),
                            view=self.make_view(retired_event, disabled=True),
                        )
                    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                        pass
            return event, new_message, True

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

    def _configuration_error(self, guild_id: int, user_id: int, config: dict | None = None) -> str | None:
        config = config if isinstance(config, dict) else (self.load_player_config(str(user_id)) or {})
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

    def _signup_profile(self, guild_id: int, user_id: str, config: dict | None = None) -> dict:
        config = config if isinstance(config, dict) else (self.load_player_config(str(user_id)) or {})
        profile = {}
        mmr = config.get("mmr")
        if isinstance(mmr, (int, float)) and not isinstance(mmr, bool):
            profile["mmr"] = int(mmr)
        if self.load_inhouse_record is None:
            return profile
        try:
            record = self.load_inhouse_record(guild_id, user_id) or {}
        except Exception as exc:
            print(f"[rsvp] Failed to load inhouse record for {user_id} in guild {guild_id}: {exc}")
            return profile
        games = int(record.get("games", 0) or 0)
        if games <= 0:
            return profile
        profile.update({
            "inhouse_wins": int(record.get("wins", 0) or 0),
            "inhouse_losses": int(record.get("losses", 0) or 0),
            "inhouse_games": games,
            "inhouse_win_rate": float(record.get("win_rate", 0) or 0),
        })
        return profile

    def _refresh_signup_profiles(self, event: dict) -> bool:
        guild_id = int(event.get("guild_id", 0) or 0)
        signups = dict(event.get("signups", {}) or {})
        changed = False
        for user_id, data in list(signups.items()):
            if not isinstance(data, dict):
                continue
            updated = dict(data)
            updated.update(self._signup_profile(guild_id, str(user_id)))
            if updated != data:
                signups[str(user_id)] = updated
                changed = True
        if changed:
            event["signups"] = signups
            event["updated_at"] = int(time.time())
        return changed

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

                signup_config = None
                if action in {SIGNUP_RSVP, SIGNUP_FILL}:
                    signup_config = self.load_player_config(str(interaction.user.id)) or {}
                    configuration_error = self._configuration_error(
                        guild_id,
                        interaction.user.id,
                        signup_config,
                    )
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
                        **self._signup_profile(guild_id, user_id, signup_config),
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
                if (
                    event.get("status") not in INTERACTIVE_STATUSES
                    and event.get("status") != STATUS_COMPLETED
                    and not _is_pending_finalization(event)
                    and not _is_pending_lobby_start(event)
                    and not _is_active_series(event)
                ):
                    continue
                try:
                    guild_id = int(event.get("guild_id") or snapshot.id)
                except (TypeError, ValueError):
                    continue
                if self.bot.get_guild(guild_id) is None:
                    continue
                if self._refresh_signup_profiles(event):
                    self._save_event(guild_id, event)
                if _is_pending_finalization(event):
                    checkpoint_at = int(
                        event.get("checkpoint_at", 0)
                        or (int(event.get("start_at", 0) or 0) - RSVP_CONFIRMATION_LEAD_SECONDS)
                    )
                    if not event.get("checkpoint_at"):
                        event["checkpoint_at"] = checkpoint_at
                        self._save_event(guild_id, event)
                    if checkpoint_at <= int(time.time()):
                        event, outcome, _ = await self.finalize_event(guild_id, automatic=True)
                        print(f"[rsvp] Finalized overdue RSVP event for guild {guild_id}")
                        if outcome != STATUS_CONFIRMED:
                            continue
                    else:
                        self._schedule_checkpoint(event)
                if _is_pending_lobby_start(event):
                    self._schedule_lobby_start(event)
                message = await self._resolve_message(event)
                if message is None:
                    print(f"[rsvp] Could not restore RSVP message for guild {guild_id}")
                else:
                    try:
                        await message.edit(
                            embed=build_rsvp_embed(event),
                            view=self.make_view(event),
                        )
                    except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                        print(f"[rsvp] Failed to refresh RSVP message for guild {guild_id}: {exc}")
                    else:
                        print(f"[rsvp] Restored RSVP event for guild {guild_id}")
                if _is_active_series(event) and self.series_resume is not None:
                    try:
                        await self.series_resume(event)
                    except Exception as exc:
                        print(f"[rsvp] Failed to resume RSVP series for guild {guild_id}: {exc}")
        except Exception as exc:
            print(f"[rsvp] Failed to restore active RSVP events: {exc}")
