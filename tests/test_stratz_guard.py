import unittest
from unittest.mock import patch

from bot.services import stratz_guard


class ReserveStratzRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_reservation_reports_existing_block(self):
        block_state = (True, "STRATZ 403 different IP lockout", None)

        with (
            patch.object(stratz_guard, "get_stratz_block_state", return_value=block_state),
            patch.object(stratz_guard, "_reserve_rate_slot") as reserve_rate_slot,
        ):
            result = await stratz_guard.reserve_stratz_request()

        self.assertEqual(result, block_state)
        reserve_rate_slot.assert_not_called()

    async def test_sync_reservation_reports_existing_block(self):
        block_state = (True, "STRATZ 429 rate limit", None)

        with (
            patch.object(stratz_guard, "get_stratz_block_state", return_value=block_state),
            patch.object(stratz_guard, "_reserve_rate_slot") as reserve_rate_slot,
        ):
            result = stratz_guard.reserve_stratz_request_sync()

        self.assertEqual(result, block_state)
        reserve_rate_slot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
