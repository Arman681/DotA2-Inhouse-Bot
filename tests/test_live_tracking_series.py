import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot.services import live_tracking_service as live_tracking


class FakeAsyncResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return ""


class StratzMmrFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_stratz_circuit_falls_back_to_opendota(self):
        opendota_response = FakeAsyncResponse(200, {"rank_tier": 52})
        session = SimpleNamespace(
            post=Mock(),
            get=Mock(return_value=opendota_response),
        )

        with (
            patch.object(
                live_tracking,
                "reserve_stratz_request",
                AsyncMock(return_value=(True, "STRATZ 403 different IP lockout", None)),
            ),
            patch.object(live_tracking, "get_http_session", return_value=session),
        ):
            result = await live_tracking.fetch_mmr("123")

        self.assertEqual(result, (3311, 52, "OpenDota"))
        session.post.assert_not_called()
        session.get.assert_called_once_with(
            "https://api.opendota.com/api/players/123",
            timeout=8,
        )


class SeriesMatchWaitTests(unittest.IsolatedAsyncioTestCase):
    guild_id = 987654

    async def asyncSetUp(self):
        self.guild = SimpleNamespace(id=self.guild_id, name="Series Test Guild")
        self.channel = SimpleNamespace(send=AsyncMock())
        live_tracking.lobby_players[self.guild_id] = [(index, str(index), 4_000) for index in range(10)]

    async def asyncTearDown(self):
        task = live_tracking.polling_tasks.pop(self.guild_id, None)
        if task and not task.done():
            task.cancel()
        live_tracking.match_wait_tasks.pop(self.guild_id, None)
        live_tracking.active_match_ids.pop(self.guild_id, None)
        live_tracking.lobby_players.pop(self.guild_id, None)

    async def test_new_series_game_excludes_already_completed_match_ids(self):
        current_task = asyncio.current_task()
        live_tracking.match_wait_tasks[self.guild_id] = current_task
        match = {"match_id": 222}
        outcome_callback = AsyncMock()

        with (
            patch.object(live_tracking, "fetch_live_match_for_guild", AsyncMock(return_value=match)) as fetch,
            patch.object(live_tracking, "poll_live_match", AsyncMock()) as poll,
            patch.object(live_tracking, "on_match_wait_outcome", outcome_callback),
        ):
            await live_tracking.wait_for_match_then_start_polling(
                self.guild_id,
                self.guild,
                self.channel,
                excluded_match_ids=["111"],
                game_number=2,
                scheduled_series=True,
            )
            await asyncio.sleep(0)

        fetch.assert_awaited_once_with(self.guild_id, excluded_match_ids=["111"])
        outcome_callback.assert_awaited_once_with(
            self.guild,
            self.channel,
            "match_found",
            game_number=2,
            match_id=222,
        )
        poll.assert_awaited_once_with(222, self.guild)
        self.assertNotIn(self.guild_id, live_tracking.match_wait_tasks)

    async def test_series_timeout_is_reported_without_advancing_the_game(self):
        current_task = asyncio.current_task()
        live_tracking.match_wait_tasks[self.guild_id] = current_task
        outcome_callback = AsyncMock()

        with (
            patch.object(live_tracking, "fetch_live_match_for_guild", AsyncMock(return_value=None)),
            patch.object(live_tracking.asyncio, "sleep", AsyncMock()),
            patch.object(live_tracking, "on_match_wait_outcome", outcome_callback),
        ):
            await live_tracking.wait_for_match_then_start_polling(
                self.guild_id,
                self.guild,
                self.channel,
                timeout_seconds=1,
                game_number=2,
                scheduled_series=True,
            )

        outcome_callback.assert_awaited_once_with(
            self.guild,
            self.channel,
            "timeout",
            game_number=2,
            match_id=None,
        )
        sent_messages = [call.args[0] for call in self.channel.send.await_args_list]
        self.assertTrue(any("same scheduled game is still pending" in message for message in sent_messages))
        self.assertNotIn(self.guild_id, live_tracking.match_wait_tasks)


if __name__ == "__main__":
    unittest.main()
