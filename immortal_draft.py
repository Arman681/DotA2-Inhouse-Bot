# immortal_draft.py
import asyncio, datetime
import discord
from discord import ui
from typing import List, Dict, Optional, Tuple

PICK_ORDER = [("cap1", 1), ("cap2", 2), ("cap1", 2), ("cap2", 2), ("cap1", 1)]

class Candidate:
    def __init__(self, member: discord.Member, mmr: int):
        self.member = member
        self.mmr = mmr

    def display(self) -> str:
        return f"{self.member.display_name} · {self.mmr}"

class ImmortalDraftSession:
    def __init__(
        self,
        bot,
        guild: discord.Guild,
        channel: discord.TextChannel,
        cap1: discord.Member,
        cap2: discord.Member,
        candidates: List[Candidate],
        per_pick_seconds: int = 50,  # 20 + 30 reserve per pick
    ):
        self.bot = bot
        self.guild = guild
        self.channel = channel
        self.cap1 = cap1
        self.cap2 = cap2
        # sort low -> high like in Immortal Draft UI
        self.candidates: List[Candidate] = sorted(candidates, key=lambda c: c.mmr)
        self.available_ids = [c.member.id for c in self.candidates]
        self.teams: Dict[str, List[int]] = {"cap1": [], "cap2": []}
        self.per_pick_seconds = per_pick_seconds
        self.current_turn_index = 0
        self.remaining_this_turn = self.per_pick_seconds
        self.message: Optional[discord.Message] = None
        self.view: Optional["ImmortalDraftView"] = None
        self.timer_task: Optional[asyncio.Task] = None
        self.locked = False

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
            self.remaining_this_turn = self.per_pick_seconds
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
            tag = f"<@{c.member.id}>"
            if c.member.id not in self.available_ids:
                names.append(f"~~{tag}~~")
            else:
                names.append(tag)
        return " · ".join(names)

    def team_lines(self) -> Tuple[str, str, int, int]:
        def fmt(ids):
            return ", ".join(f"<@{i}>" for i in ids) if ids else "—"
        total1 = sum(next((c.mmr for c in self.candidates if c.member.id == pid), 0) for pid in self.teams["cap1"])
        total2 = sum(next((c.mmr for c in self.candidates if c.member.id == pid), 0) for pid in self.teams["cap2"])
        return fmt(self.teams["cap1"]), fmt(self.teams["cap2"]), total1, total2

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

        status = "Draft complete" if self.locked else f"🪪 Turn: {now_captain.mention} (needs **{need}**)"
        e.add_field(name="Status", value=status, inline=False)
        if not self.locked:
            e.add_field(name="Pick Timer",
                        value=f"Time left: **{self.remaining_this_turn:02d}s** (20s + 30s reserve)",
                        inline=False)
        e.set_footer(text="Order: 1–2–2–2–1 · Low→High MMR display")
        return e

    def _autopick_member_id(self) -> Optional[int]:
        if not self.available_ids:
            return None
        # Auto-pick highest remaining MMR (rightmost)
        rightmost = None
        max_mmr = -1
        for c in self.candidates:
            if c.member.id in self.available_ids and c.mmr >= max_mmr:
                rightmost = c.member.id
                max_mmr = c.mmr
        return rightmost

    async def apply_pick(self, picker_id: int, target_id: int) -> Tuple[bool, str]:
        if not self.pickable_for(picker_id):
            return False, "It's not your turn."
        if target_id not in self.available_ids:
            return False, "That player is already taken."
        who, _ = self._turn_info()
        self.teams[who].append(target_id)
        self.available_ids.remove(target_id)
        if self._is_turn_over():
            self._next_turn()
        return True, "Picked!"

    async def autopick_if_needed(self) -> bool:
        if self.locked:
            return False
        target_id = self._autopick_member_id()
        if target_id is None:
            self.locked = True
            return False
        who, _ = self._turn_info()
        self.teams[who].append(target_id)
        self.available_ids.remove(target_id)
        if self._is_turn_over():
            self._next_turn()
        return True

    async def start(self):
        self.view = ImmortalDraftView(self)
        self.message = await self.channel.send(embed=self.make_embed(), view=self.view)
        self.timer_task = asyncio.create_task(self._run_timer())

    async def _run_timer(self):
        try:
            while not self.locked:
                await asyncio.sleep(1)
                self.remaining_this_turn -= 1
                if self.remaining_this_turn <= 0:
                    # timeout -> autopick
                    changed = await self.autopick_if_needed()
                    self.remaining_this_turn = self.per_pick_seconds
                if self.message:
                    await self.message.edit(embed=self.make_embed(), view=self.view)
            # lock view when done
            if self.view:
                self.view.disable_all()
                if self.message:
                    await self.message.edit(embed=self.make_embed(), view=self.view)
            # announce final teams
            t1, t2, total1, total2 = self.team_lines()
            await self.channel.send(
                embed=discord.Embed(
                    title="Draft Results",
                    description=f"**Team #1 (Captain {self.cap1.display_name})**\n{t1}\n**MMR Total:** {total1}\n\n"
                                f"**Team #2 (Captain {self.cap2.display_name})**\n{t2}\n**MMR Total:** {total2}\n\n"
                                f"Move to your in-game lobby teams and begin Captains Mode.",
                    color=discord.Color.green()
                )
            )
        except asyncio.CancelledError:
            pass

class ImmortalDraftView(ui.View):
    def __init__(self, session: ImmortalDraftSession):
        super().__init__(timeout=None)
        self.session = session
        # Add one button per candidate (8 total)
        for cand in self.session.candidates:
            self.add_item(PickButton(cand.member.id, cand.display()))

    def disable_all(self):
        for child in self.children:
            if isinstance(child, ui.Button):
                child.disabled = True

class PickButton(ui.Button):
    def __init__(self, target_id: int, label_text: str):
        super().__init__(label=label_text, style=discord.ButtonStyle.secondary)
        self.target_id = target_id

    async def callback(self, interaction: discord.Interaction):
        s = self.view.session  # type: ignore
        # only current captain can pick
        if not s.pickable_for(interaction.user.id):
            return await interaction.response.send_message("Not your turn.", ephemeral=True)
        if self.target_id not in s.available_ids:
            return await interaction.response.send_message("Already taken.", ephemeral=True)

        ok, msg = await s.apply_pick(interaction.user.id, self.target_id)
        if not ok:
            return await interaction.response.send_message(msg, ephemeral=True)

        # If taken, disable this button's label visually by prefixing ✓ and strike in embed
        self.disabled = True
        await interaction.response.edit_message(embed=s.make_embed(), view=self.view)
