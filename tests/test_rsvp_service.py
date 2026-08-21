import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.services.rsvp_service import (
    STATUS_CONFIRMED,
    STATUS_LOBBY_OPEN,
    STATUS_START_FAILED,
    RsvpManager,
    build_rsvp_embed,
)


class FakeSnapshot:
    def __init__(self, data):
        self._data = copy.deepcopy(data)
        self.exists = data is not None

    def to_dict(self):
        return copy.deepcopy(self._data)


class FakeDocument:
    def __init__(self, store, key):
        self.store = store
        self.key = key

    def get(self):
        return FakeSnapshot(self.store.get(self.key))

    def set(self, data):
        self.store[self.key] = copy.deepcopy(data)


class FakeCollection:
    def __init__(self, store):
        self.store = store

    def document(self, key):
        return FakeDocument(self.store, str(key))


class FakeDatabase:
    def __init__(self, events):
        self.events = events

    def collection(self, name):
        if name != "rsvp_events":
            raise AssertionError(f"Unexpected collection: {name}")
        return FakeCollection(self.events)


def make_event(player_count=10):
    return {
        "guild_id": "123",
        "channel_id": "456",
        "message_id": "789",
        "status": STATUS_CONFIRMED,
        "start_at": 2_000_000_000,
        "checkpoint_at": 1_999_996_400,
        "games": 2,
        "signups": {
            str(index): {
                "status": "rsvp",
                "display_name": f"Player {index}",
                "joined_at": index,
                "mmr": 4_000 + index,
                "inhouse_games": 8,
                "inhouse_wins": 5,
                "inhouse_losses": 3,
                "inhouse_win_rate": 62.5,
            }
            for index in range(player_count)
        },
    }


class RsvpEmbedTests(unittest.TestCase):
    def test_roster_shows_public_mmr_and_recorded_win_rate(self):
        embed = build_rsvp_embed(make_event())
        roster_field = next(field for field in embed.fields if "Confirmed Players" in field.name)
        self.assertIn("4,000 MMR", roster_field.value)
        self.assertIn("62.5% WR (5-3)", roster_field.value)


class RsvpHandoffTests(unittest.IsolatedAsyncioTestCase):
    def make_manager(self, event):
        events = {"123": copy.deepcopy(event)}
        bot = SimpleNamespace(get_guild=lambda guild_id: SimpleNamespace(roles=[]))
        manager = RsvpManager(
            bot=bot,
            db=FakeDatabase(events),
            load_player_config=lambda user_id: {"steam_id": "1", "mmr": 4_000},
            load_guild_prefix=lambda guild_id: "!",
        )
        manager._resolve_message = AsyncMock(return_value=None)
        manager._resolve_channel = AsyncMock(return_value=None)
        manager._send_channel_announcement = AsyncMock()
        return manager, events

    async def test_confirmed_ten_are_handed_to_lobby_and_event_records_open_lobby(self):
        manager, events = self.make_manager(make_event())
        manager.lobby_handoff = AsyncMock(
            return_value={
                "message_id": 999,
                "jump_url": "https://discord.com/channels/1/2/999",
                "mode": "immortal",
                "auto_rolled": True,
            }
        )

        event, result = await manager.open_confirmed_lobby(123)

        self.assertEqual(STATUS_LOBBY_OPEN, event["status"])
        self.assertEqual("999", event["lobby_message_id"])
        self.assertEqual("immortal", event["lobby_mode"])
        self.assertTrue(event["handoff_auto_rolled"])
        self.assertEqual(999, result["message_id"])
        self.assertEqual(STATUS_LOBBY_OPEN, events["123"]["status"])
        manager.lobby_handoff.assert_awaited_once()

    async def test_short_confirmed_roster_is_blocked_without_mutating_lobby(self):
        manager, events = self.make_manager(make_event(player_count=9))
        manager.lobby_handoff = AsyncMock()

        event, result = await manager.open_confirmed_lobby(123)

        self.assertIsNone(result)
        self.assertEqual(STATUS_START_FAILED, event["status"])
        self.assertIn("9/10", event["handoff_error"])
        self.assertEqual(STATUS_START_FAILED, events["123"]["status"])
        manager.lobby_handoff.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
