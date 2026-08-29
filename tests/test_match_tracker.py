import unittest
from unittest.mock import Mock, patch

from bot.services import match_tracker


class FetchMatchResultTests(unittest.TestCase):
    opendota_result = {
        "radiant_win": False,
        "radiantplayers": ["1", "2", "3", "4", "5"],
        "direplayers": ["6", "7", "8", "9", "10"],
        "player_stats": [],
    }

    def test_stratz_unindexed_match_falls_back_to_opendota(self):
        stratz_response = Mock()
        stratz_response.status_code = 200
        stratz_response.json.return_value = {"data": {"match": None}}

        with (
            patch.object(match_tracker, "reserve_stratz_request_sync", return_value=(False, None, None)),
            patch.object(match_tracker.requests, "post", return_value=stratz_response),
            patch.object(
                match_tracker,
                "_fetch_opendota_result",
                return_value=self.opendota_result,
            ) as fetch_opendota,
        ):
            result = match_tracker.fetch_match_result("8971396570")

        self.assertEqual(result, self.opendota_result)
        fetch_opendota.assert_called_once_with("8971396570")

    def test_open_stratz_circuit_falls_back_to_opendota(self):
        with (
            patch.object(
                match_tracker,
                "reserve_stratz_request_sync",
                return_value=(True, "STRATZ 403 different IP lockout", None),
            ),
            patch.object(match_tracker.requests, "post") as post_stratz,
            patch.object(
                match_tracker,
                "_fetch_opendota_result",
                return_value=self.opendota_result,
            ) as fetch_opendota,
        ):
            result = match_tracker.fetch_match_result("8971396570")

        self.assertEqual(result, self.opendota_result)
        post_stratz.assert_not_called()
        fetch_opendota.assert_called_once_with("8971396570")

    def test_stratz_lockout_responses_fall_back_to_opendota(self):
        for status_code in (403, 429):
            with self.subTest(status_code=status_code):
                stratz_response = Mock(
                    status_code=status_code,
                    text="STRATZ unavailable",
                    headers={},
                )
                with (
                    patch.object(
                        match_tracker,
                        "reserve_stratz_request_sync",
                        return_value=(False, None, None),
                    ),
                    patch.object(
                        match_tracker.requests,
                        "post",
                        return_value=stratz_response,
                    ),
                    patch.object(
                        match_tracker,
                        "note_stratz_response",
                        return_value=True,
                    ) as note_response,
                    patch.object(
                        match_tracker,
                        "_fetch_opendota_result",
                        return_value=self.opendota_result,
                    ) as fetch_opendota,
                ):
                    result = match_tracker.fetch_match_result("8971396570")

                self.assertEqual(result, self.opendota_result)
                note_response.assert_called_once_with(
                    status_code,
                    "STRATZ unavailable",
                    headers={},
                    endpoint="fetch_match_result",
                )
                fetch_opendota.assert_called_once_with("8971396570")

    def test_indexed_stratz_match_does_not_call_opendota(self):
        stratz_response = Mock()
        stratz_response.status_code = 200
        stratz_response.json.return_value = {
            "data": {
                "match": {
                    "didRadiantWin": True,
                    "players": [],
                }
            }
        }

        with (
            patch.object(match_tracker, "reserve_stratz_request_sync", return_value=(False, None, None)),
            patch.object(match_tracker.requests, "post", return_value=stratz_response),
            patch.object(match_tracker, "_fetch_opendota_result") as fetch_opendota,
        ):
            result = match_tracker.fetch_match_result("123")

        self.assertTrue(result["radiant_win"])
        fetch_opendota.assert_not_called()


if __name__ == "__main__":
    unittest.main()
