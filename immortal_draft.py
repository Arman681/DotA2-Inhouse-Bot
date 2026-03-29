# immortal_draft.py
import asyncio
import discord
from discord import ui
from typing import List, Dict, Optional, Tuple

PICK_ORDER = [("cap1", 1), ("cap2", 2), ("cap1", 2), ("cap2", 2), ("cap1", 1)]
_cancel_callback = None
def set_cancel_callback(callback):
    global _cancel_callback
    _cancel_callback = callback

class Candidate:
    def __init__(self, player_id: str, mmr: int, member: Optional[discord.Member] = None, name: Optional[str] = None):
        self.player_id = str(player_id)
        self.member = member
        self.name = name or (member.display_name if member else "Unknown")
        self.mmr = mmr
    def display(self) -> str:
        return f"{self.name} · {self.mmr}"
    def mention_or_name(self) -> str:
        return self.member.mention if self.member else f"**{self.name}**"

class ImmortalDraftSession:
    def __init__(
        self,
        bot,
        guild: discord.Guild,
        channel: discord.TextChannel,
        cap1: discord.Member,
        cap2: discord.Member,
        cap1_mmr: int,
        cap2_mmr: int,
        candidates: List[Candidate],
        per_pick_seconds: int = 50,  # 20 + 30 reserve per pick
        header_message: Optional[discord.Message] = None,
    ):
        self.bot = bot
        self.guild = guild
        self.channel = channel
        self.cap1 = cap1
        self.cap2 = cap2
        self.cap1_mmr = cap1_mmr
        self.cap2_mmr = cap2_mmr
        # sort low -> high like in Immortal Draft UI
        self.candidates: List[Candidate] = sorted(candidates, key=lambda c: c.mmr)
        self.available_ids = [c.player_id for c in self.candidates]
        self.teams: Dict[str, List[str]] = {"cap1": [], "cap2": []}
        self.per_pick_seconds = per_pick_seconds
        self.current_turn_index = 0
        self.message: Optional[discord.Message] = None
        self.header_message: Optional[discord.Message] = header_message
        self.view: Optional["ImmortalDraftView"] = None
        self.timer_task: Optional[asyncio.Task] = None
        self.locked = False
        self.cancelled = False
        self._finalized = False
        # 5s per pick + 60s reserve per captain (cumulative across the whole draft)
        self.turn_base_seconds = 5
        self.reserve = {"cap1": 60, "cap2": 60}  # each captain has their own pool
        self.turn_remaining = self.turn_base_seconds

    async def _refresh_ui(self):
        if self.message:
            await self.message.edit(embed=self.make_embed(), view=self.view)

    def _turn_info(self) -> Tuple[str, int]:
        who, count = PICK_ORDER[self.current_turn_index]
        return who, count

    def _is_turn_over(self) -> bool:
        who, count = self._turn_info()
        picked = len(self.teams[who])
        # how many already picked by this captain this "chunk"?
        chunk_start = sum(ct for i, (_, ct) in enumerate(PICK_ORDER[:self.current_turn_index]) )
        chunk_end   = chunk_start + count
        total_for_who_so_far = picked
        # total expected by this point:
        expected_for_who = sum(ct for (_, ct) in PICK_ORDER[:self.current_turn_index] if _ == who)
        picked_this_chunk = total_for_who_so_far - expected_for_who
        return picked_this_chunk >= count

    def _next_turn(self):
        if self.current_turn_index < len(PICK_ORDER) - 1:
            self.current_turn_index += 1
            self.turn_remaining = self.turn_base_seconds  # new pick window
        else:
            self.locked = True

    def current_captain_member(self) -> discord.Member:
        who, _ = self._turn_info()
        return self.cap1 if who == "cap1" else self.cap2

    def captain_key_for_user(self, user_id: int) -> Optional[str]:
        if user_id == self.cap1.id:
            return "cap1"
        if user_id == self.cap2.id:
            return "cap2"
        return None

    def is_user_current_captain(self, user_id: int) -> bool:
        cap = self.current_captain_member()
        return user_id == cap.id

    def candidate_line(self) -> str:
        # Left to right low->high MMR; strike-through when taken
        names = []
        for c in self.candidates:
            tag = c.mention_or_name()
            if c.player_id not in self.available_ids:
                names.append(f"~~{tag}~~")
            else:
                names.append(tag)
        return " · ".join(names)

    def team_lines(self) -> Tuple[str, str, int, int]:
        def display_name_for_player_id(pid: str) -> str:
            cand = next((c for c in self.candidates if c.player_id == pid), None)
            if not cand:
                return str(pid)
            return cand.mention_or_name()
        def fmt(captain, ids):
            drafted = ", ".join(display_name_for_player_id(pid) for pid in ids) if ids else "—"
            return f"{captain.mention}, {drafted}" if drafted != "—" else f"{captain.mention}"
        total1 = self.cap1_mmr + sum(
            next((c.mmr for c in self.candidates if c.player_id == pid), 0)
            for pid in self.teams["cap1"]
        )
        total2 = self.cap2_mmr + sum(
            next((c.mmr for c in self.candidates if c.player_id == pid), 0)
            for pid in self.teams["cap2"]
        )
        return (
            fmt(self.cap1, self.teams["cap1"]),
            fmt(self.cap2, self.teams["cap2"]),
            total1,
            total2,
        )

    def pickable_for(self, user_id: int) -> bool:
        return self.is_user_current_captain(user_id) and not self.locked

    def make_embed(self) -> discord.Embed:
        who, need = self._turn_info()
        now_captain = self.cap1 if who == "cap1" else self.cap2
        team1, team2, total1, total2 = self.team_lines()
        e = discord.Embed(title="Immortal Draft",
                          description=self.candidate_line(),
                          color=discord.Color.blurple())
        e.add_field(name="Captain #1", value=f"{self.cap1.mention}", inline=True)
        e.add_field(name="Captain #2", value=f"{self.cap2.mention}", inline=True)
        e.add_field(name="\u200b", value="\u200b", inline=True)
        e.add_field(name="Team #1", value=f"{team1}\n**Team MMR:** {total1}", inline=True)
        e.add_field(name="Team #2", value=f"{team2}\n**Team MMR:** {total2}", inline=True)
        e.add_field(name="\u200b", value="\u200b", inline=True)
        status = "Draft complete" if self.locked else f"Turn: {now_captain.mention} (needs **{need}**)"
        e.add_field(name="Status", value=status, inline=False)
        if not self.locked:
            reserve_left = max(0, int(self.reserve[who]))
            e.add_field(
                name="Pick Timer",
                value=(
                    f"Turn time: **{int(self.turn_remaining):02d}s**  |  "
                    f"Reserve ({now_captain.display_name}): **{reserve_left:02d}s**"
                ),
                inline=False
            )
        e.set_footer(text="Order: 1–2–2–2–1 · Low→High MMR display")
        return e

    def _autopick_member_id(self) -> Optional[str]:
        # Auto-pick lowest remaining MMR (leftmost)
        for c in self.candidates:            # candidates are sorted low→high at init
            if c.player_id in self.available_ids:
                return c.player_id
        return None
    
    async def cancel_draft(self):
        self.cancelled = True
        self.locked = True
        if self.timer_task and not self.timer_task.done():
            self.timer_task.cancel()
        if self.view:
            self.view.disable_all()
        try:
            if _cancel_callback is not None:
                await _cancel_callback(self.guild.id)
        except Exception:
            pass
        try:
            if self.message:
                await self.message.delete()
        except Exception:
            pass
        try:
            if self.header_message:
                await self.header_message.delete()
        except Exception:
            pass

    async def apply_pick(self, picker_id: int, target_id: str) -> Tuple[bool, str]:
        if not self.pickable_for(picker_id):
            return False, "It's not your turn."
        if target_id not in self.available_ids:
            return False, "That player is already taken."
        who, _ = self._turn_info()
        self.teams[who].append(target_id)
        self.available_ids.remove(target_id)
        # disable & relabel the picked player’s button
        if self.view:
            self.view.mark_picked(target_id)
        await self._refresh_ui()
        # if this captain still has another pick in this chunk, refresh the 5s window
        if not self._is_turn_over():
            self.turn_remaining = self.turn_base_seconds
        else:
            self._next_turn()
        # If that was the final pick, finalize immediately instead of waiting on timer loop
        if self.locked:
            await self.finalize_draft()
        return True, "Picked!"

    async def start(self):
        self.view = ImmortalDraftView(self)
        self.message = await self.channel.send(embed=self.make_embed(), view=self.view)
        self.timer_task = asyncio.create_task(self._run_timer())
        try:
            await self.channel.send(
                embed=discord.Embed(
                    title="Draft Results",
                    description=(
                        f"**Team #1 (Captain {self.cap1.display_name})**\n"
                        f"{t1}\n"
                        f"**MMR Total:** {total1}\n"
                        f"**Average MMR:** {avg1}\n\n"
                        f"**Team #2 (Captain {self.cap2.display_name})**\n"
                        f"{t2}\n"
                        f"**MMR Total:** {total2}\n"
                        f"**Average MMR:** {avg2}\n\n"
                        f"Move to your in-game lobby teams and begin Captains Mode."
                    ),
                    color=discord.Color.green()
                )
            )
        except Exception as e:
            print(f"[ImmortalDraftSession.finalize_draft] Failed to send draft results: {e}")

    async def _run_timer(self):
        try:
            while not self.locked:
                await asyncio.sleep(1)
                if self.locked:
                    break
                who, _ = self._turn_info()
                if self.turn_remaining > 0:
                    self.turn_remaining -= 1
                else:
                    # consume this captain's reserve if any
                    if self.reserve[who] > 0:
                        self.reserve[who] -= 1
                    else:
                        # out of time completely -> autopick one player
                        await self._timeout_autopick()
                # push UI update
                if self.message and not self._finalized:
                    await self.message.edit(embed=self.make_embed(), view=self.view)
            await self.finalize_draft()
        except asyncio.CancelledError:
            return
        except Exception as e:
            print(f"[ImmortalDraftSession._run_timer] Timer task crashed: {e}")
    
    async def _timeout_autopick(self):
        # autopick the lowest remaining MMR
        target_id = self._autopick_member_id()
        if target_id is None:
            self.locked = True
            await self.finalize_draft()
            return
        who, _ = self._turn_info()
        self.teams[who].append(target_id)
        self.available_ids.remove(target_id)
        if self.view:
            self.view.mark_picked(target_id)
        await self._refresh_ui()
        # if the captain still has another pick in this chunk, reset the 5s turn timer;
        # otherwise advance to the next captain (reserve does NOT reset).
        if not self._is_turn_over():
            self.turn_remaining = self.turn_base_seconds
        else:
            self._next_turn()
        if self.locked:
            await self.finalize_draft()

class ImmortalDraftView(ui.View):
    def __init__(self, session: ImmortalDraftSession):
        super().__init__(timeout=None)
        self.session = session
        self.button_by_id: dict[str, "PickButton"] = {}
        for idx, cand in enumerate(self.session.candidates):
            row = idx // 4
            btn = PickButton(cand.player_id, cand.display(), row=row)
            self.button_by_id[cand.player_id] = btn
            self.add_item(btn)
        self.add_item(CancelDraftButton())
    def disable_all(self):
        for child in self.children:
            if isinstance(child, ui.Button):
                child.disabled = True
    # mark a specific button as picked
    def mark_picked(self, target_id: str):
        btn = self.button_by_id.get(target_id)
        if btn:
            btn.mark_picked()

class CancelDraftButton(ui.Button):
    def __init__(self):
        super().__init__(
            label="Cancel Immortal Draft",
            style=discord.ButtonStyle.danger,
            row=2
        )
    async def callback(self, interaction: discord.Interaction):
        s = self.view.session  # type: ignore
        member = interaction.guild.get_member(interaction.user.id) if interaction.guild else None
        is_admin = False
        if member:
            is_admin = member.guild_permissions.administrator or any(
                role.name == "Inhouse Admin" for role in member.roles
            )
        allowed_ids = {s.cap1.id, s.cap2.id}
        if interaction.user.id not in allowed_ids and not is_admin:
            await interaction.response.send_message(
                "Only the captains or an admin can cancel this draft.",
                ephemeral=True
            )
            return
        await interaction.response.defer()
        await s.cancel_draft()
        await s.channel.send("Immortal Draft has been cancelled.")

class PickButton(ui.Button):
    def __init__(self, target_id: str, label_text: str, row: int | None = None):
        super().__init__(label=label_text, style=discord.ButtonStyle.secondary, row=row)
        self.target_id = target_id
        self.base_label = label_text
    # when this player is picked (manually or auto), disable & relabel the button
    def mark_picked(self):
        self.disabled = True
        # buttons don’t render ~~strike~~ markdown; a plain suffix is clearest
        self.label = f"{self.base_label} (picked)"
        self.style = discord.ButtonStyle.danger
    async def callback(self, interaction: discord.Interaction):
        s = self.view.session  # type: ignore
        if not s.pickable_for(interaction.user.id):
            return await interaction.response.send_message("Not your turn.", ephemeral=True)
        if self.target_id not in s.available_ids:
            return await interaction.response.send_message("Already taken.", ephemeral=True)
        ok, msg = await s.apply_pick(interaction.user.id, self.target_id)
        if not ok:
            return await interaction.response.send_message(msg, ephemeral=True)
        # refresh the message
        await interaction.response.edit_message(embed=s.make_embed(), view=self.view)