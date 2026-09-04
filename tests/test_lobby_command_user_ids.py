import unittest
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from bot.commands.commands import attach_commands


class FakeRegisteredCommand:
    def __init__(self, callback):
        self.callback = callback
        self.error_handler = None

    def error(self, handler):
        self.error_handler = handler
        return handler


class FakeBot:
    def __init__(self):
        self.commands = {}

    def command(self, name=None, **_kwargs):
        def decorator(callback):
            command = FakeRegisteredCommand(callback)
            self.commands[name or callback.__name__] = command
            return command

        return decorator


class FakeGuild:
    def __init__(self, members):
        self.id = 123
        self.name = "Test Guild"
        self._members = {member.id: member for member in members}

    def get_member(self, user_id):
        return self._members.get(user_id)

    async def fetch_member(self, user_id):
        member = self.get_member(user_id)
        if member is None:
            raise AssertionError(f"Unexpected missing member: {user_id}")
        return member


class FakeContext:
    def __init__(self, guild):
        self.guild = guild
        self.channel = SimpleNamespace()
        self.message = SimpleNamespace(mentions=[])
        self.reply = AsyncMock()


def identity_check():
    return lambda callback: callback


class LobbyCommandUserIdTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.old_member = SimpleNamespace(id=111111111111111111, display_name="Old Player")
        self.new_member = SimpleNamespace(id=222222222222222222, display_name="New Player")
        self.guild = FakeGuild([self.old_member, self.new_member])
        self.ctx = FakeContext(self.guild)
        self.lobby_players = {self.guild.id: []}
        self.save_lobby_players = Mock()
        self.full_post_rocket_reset = AsyncMock()
        self.update_lobby_embed = AsyncMock()
        self.bot = FakeBot()
        deps = defaultdict(Mock)
        deps.update({
            "is_admin_or_has_role": identity_check,
            "is_global_admin": identity_check,
            "lobby_players": self.lobby_players,
            "lobby_message": {},
            "get_mmr": lambda member: 4000 if member.id == self.old_member.id else 4500,
            "full_post_rocket_reset": self.full_post_rocket_reset,
            "save_lobby_players": self.save_lobby_players,
            "update_lobby_embed": self.update_lobby_embed,
            "is_placeholder_player": lambda user_id: str(user_id).startswith("placeholder:"),
        })
        attach_commands(self.bot, deps)

    async def test_add_accepts_a_raw_discord_user_id(self):
        await self.bot.commands["add"].callback(self.ctx, str(self.old_member.id))

        self.assertEqual(
            [(self.old_member.id, self.old_member.display_name, 4000)],
            self.lobby_players[self.guild.id],
        )
        self.save_lobby_players.assert_called_once()
        self.update_lobby_embed.assert_awaited_once_with(self.guild)

    async def test_add_preserves_placeholder_name_and_mmr_mode(self):
        await self.bot.commands["add"].callback(self.ctx, "StandIn", "4200")

        self.assertEqual(
            [("placeholder:standin", "StandIn", 4200)],
            self.lobby_players[self.guild.id],
        )

    async def test_remove_accepts_raw_ids_without_resolving_members(self):
        departed_user_id = 333333333333333333
        self.lobby_players[self.guild.id] = [(departed_user_id, "Departed Player", 3900)]

        await self.bot.commands["remove"].callback(self.ctx, str(departed_user_id))

        self.assertEqual([], self.lobby_players[self.guild.id])
        self.save_lobby_players.assert_called_once()

    async def test_replace_accepts_raw_ids_for_old_and_new_users(self):
        self.lobby_players[self.guild.id] = [
            (self.old_member.id, self.old_member.display_name, 4000),
        ]

        await self.bot.commands["replace"].callback(
            self.ctx,
            str(self.old_member.id),
            str(self.new_member.id),
        )

        self.assertEqual(
            [(self.new_member.id, self.new_member.display_name, 4500)],
            self.lobby_players[self.guild.id],
        )
        self.ctx.reply.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
