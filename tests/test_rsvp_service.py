import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.services.rsvp_service import (
    RSVP_LOBBY_OPEN_LEAD_SECONDS,
    SERIES_BETWEEN_GAMES,
    SERIES_COMPLETED,
    SERIES_LIVE,
    SERIES_WAITING,
    STATUS_COMPLETED,
    STATUS_CONFIRMED,
    STATUS_LOBBY_OPEN,
    STATUS_START_FAILED,
    RsvpManager,
    _lobby_open_at,
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
        "lobby_open_at": 1_999_999_700,
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
    def test_lobby_open_is_scheduled_five_minutes_before_start(self):
        event = make_event()
        self.assertEqual(5 * 60, RSVP_LOBBY_OPEN_LEAD_SECONDS)
        self.assertEqual(event["start_at"] - (5 * 60), _lobby_open_at(event))

        legacy_event = make_event()
        legacy_event.pop("lobby_open_at")
        self.assertEqual(legacy_event["start_at"] - (5 * 60), _lobby_open_at(legacy_event))

    def test_roster_shows_public_mmr_and_recorded_win_rate(self):
        embed = build_rsvp_embed(make_event())
        roster_field = next(field for field in embed.fields if "Confirmed Players" in field.name)
        self.assertIn("4,000 MMR", roster_field.value)
        self.assertIn("62.5% WR (5-3)", roster_field.value)

    def test_running_series_shows_game_progress(self):
        event = make_event()
        event.update({
            "status": STATUS_LOBBY_OPEN,
            "games_completed": 1,
            "current_game": 2,
            "series_status": SERIES_WAITING,
        })

        embed = build_rsvp_embed(event)

        self.assertIn("Waiting for Game 2/2", embed.title)
        progress_field = next(field for field in embed.fields if field.name == "Series Progress")
        self.assertIn("1/2 complete", progress_field.value)


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
        self.assertEqual(0, event["games_completed"])
        self.assertEqual(1, event["current_game"])
        self.assertEqual(SERIES_WAITING, event["series_status"])
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


class RsvpSeriesTests(unittest.IsolatedAsyncioTestCase):
    def make_manager(self):
        event = make_event()
        event.update({
            "status": STATUS_LOBBY_OPEN,
            "lobby_message_id": "999",
            "games_completed": 0,
            "completed_match_ids": [],
            "current_game": 1,
            "series_status": SERIES_WAITING,
        })
        events = {"123": copy.deepcopy(event)}
        bot = SimpleNamespace(get_guild=lambda guild_id: SimpleNamespace(roles=[]))
        manager = RsvpManager(
            bot=bot,
            db=FakeDatabase(events),
            load_player_config=lambda user_id: {"steam_id": "1", "mmr": 4_000},
            load_guild_prefix=lambda guild_id: "!",
        )
        manager._resolve_message = AsyncMock(return_value=None)
        return manager, events

    async def test_each_unique_match_advances_once_and_final_match_completes_series(self):
        manager, events = self.make_manager()

        first = await manager.record_series_match(123, 111)
        duplicate = await manager.record_series_match(123, 111)
        await manager.mark_series_waiting(123, game_number=2)
        await manager.mark_series_wait_outcome(123, "match_found", game_number=2, match_id=222)
        second = await manager.record_series_match(123, 222, require_current_match=True)

        self.assertTrue(first["counted"])
        self.assertFalse(first["series_complete"])
        self.assertEqual(SERIES_BETWEEN_GAMES, first["event"]["series_status"])
        self.assertFalse(duplicate["counted"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(1, duplicate["games_completed"])
        self.assertTrue(second["series_complete"])
        self.assertEqual(STATUS_COMPLETED, events["123"]["status"])
        self.assertEqual(SERIES_COMPLETED, events["123"]["series_status"])
        self.assertEqual(["111", "222"], events["123"]["completed_match_ids"])

    async def test_reconciliation_requires_the_current_tracked_match(self):
        manager, events = self.make_manager()
        events["123"]["series_status"] = SERIES_LIVE
        events["123"]["current_match_id"] = "444"

        result = await manager.record_series_match(123, 333, require_current_match=True)

        self.assertFalse(result["counted"])
        self.assertEqual("unexpected_match", result["reason"])
        self.assertEqual(0, events["123"]["games_completed"])

    async def test_ordinary_lobby_override_retires_an_unfinished_series(self):
        manager, events = self.make_manager()

        retired = await manager.retire_series_for_lobby_override(123, reset_by="42")
        later_result = await manager.record_series_match(123, 555)

        self.assertEqual("reset", retired["status"])
        self.assertEqual("overridden", retired["series_status"])
        self.assertEqual("42", retired["reset_by"])
        self.assertFalse(later_result["counted"])
        self.assertEqual("no_active_series", later_result["reason"])
        self.assertEqual(0, events["123"]["games_completed"])


if __name__ == "__main__":
    unittest.main()
