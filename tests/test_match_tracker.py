import unittest
from unittest.mock import Mock, patch

from bot.services import match_tracker


class FetchMatchResultTests(unittest.TestCase):
    def test_stratz_unindexed_match_falls_back_to_opendota(self):
        stratz_response = Mock()
        stratz_response.status_code = 200
        stratz_response.json.return_value = {"data": {"match": None}}
        opendota_result = {
            "radiant_win": False,
            "radiantplayers": ["1", "2", "3", "4", "5"],
            "direplayers": ["6", "7", "8", "9", "10"],
            "player_stats": [],
        }

        with (
            patch.object(match_tracker, "reserve_stratz_request_sync", return_value=(False, None, None)),
            patch.object(match_tracker.requests, "post", return_value=stratz_response),
            patch.object(
                match_tracker,
                "_fetch_opendota_result",
                return_value=opendota_result,
            ) as fetch_opendota,
        ):
            result = match_tracker.fetch_match_result("8971396570")

        self.assertEqual(result, opendota_result)
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
