import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bot.services import embed_service, guild_config_service, lobby_service


class MissingPlayerSnapshot:
    exists = False

    @staticmethod
    def to_dict():
        return None


class FakePlayerDocument:
    @staticmethod
    def get():
        return MissingPlayerSnapshot()


class FakePlayerCollection:
    @staticmethod
    def document(_key):
        return FakePlayerDocument()


class FakePlayerDatabase:
    @staticmethod
    def collection(name):
        if name != "players":
            raise AssertionError(f"Unexpected collection: {name}")
        return FakePlayerCollection()


class ConfigSnapshot:
    def __init__(self, data):
        self.data = data
        self.exists = data is not None

    def to_dict(self):
        return self.data


class ConfigDocument:
    def __init__(self, store, key):
        self.store = store
        self.key = key

    def get(self):
        return ConfigSnapshot(self.store.get(self.key))

    def set(self, data, merge=False):
        if merge:
            self.store.setdefault(self.key, {}).update(data)
        else:
            self.store[self.key] = data


class ConfigCollection:
    def __init__(self, store):
        self.store = store

    def document(self, key):
        return ConfigDocument(self.store, str(key))


class ConfigDatabase:
    def __init__(self):
        self.guilds = {}

    def collection(self, name):
        if name != "guild_specific_info":
            raise AssertionError(f"Unexpected collection: {name}")
        return ConfigCollection(self.guilds)


class TeamBalanceTests(unittest.TestCase):
    ratings = [2900, 3200, 3300, 3800, 4100, 4900, 5300, 5500, 5600, 6100]

    def setUp(self):
        self.players = [
            (index, f"Player {index}", mmr)
            for index, mmr in enumerate(self.ratings)
        ]
        self.original_db = lobby_service.db
        lobby_service.db = FakePlayerDatabase()

    def tearDown(self):
        lobby_service.db = self.original_db
        lobby_service.team_rolls.pop(1001, None)
        lobby_service.team_rolls.pop(1002, None)
        lobby_service.valid_team_combos.pop(1001, None)
        lobby_service.valid_team_combos.pop(1002, None)

    def test_spread_toggle_changes_ranking_after_mmr_filter(self):
        common_patches = (
            patch.object(lobby_service, "load_preferred_roles_setting", return_value=False),
            patch.object(lobby_service, "get_separated_pairs", return_value=[]),
        )
        with common_patches[0], common_patches[1], patch.object(
            lobby_service,
            "load_mmr_spread_setting",
            return_value=False,
        ):
            mean_only, _ = lobby_service.calculate_balanced_teams(
                self.players,
                1001,
                max_mmr_diff=100,
            )

        with (
            patch.object(lobby_service, "load_preferred_roles_setting", return_value=False),
            patch.object(lobby_service, "get_separated_pairs", return_value=[]),
            patch.object(lobby_service, "load_mmr_spread_setting", return_value=True),
        ):
            spread_ranked, _ = lobby_service.calculate_balanced_teams(
                self.players,
                1002,
                max_mmr_diff=100,
            )

        mean_only_team = {player[2] for player in mean_only[0][0]}
        spread_ranked_team = {player[2] for player in spread_ranked[0][0]}
        self.assertEqual({2900, 3200, 4900, 5300, 6100}, mean_only_team)
        self.assertEqual({2900, 3300, 4900, 5500, 5600}, spread_ranked_team)

        for result in spread_ranked:
            team1, team2 = result[0], result[1]
            avg1 = sum(player[2] for player in team1) / 5
            avg2 = sum(player[2] for player in team2) / 5
            self.assertLessEqual(abs(avg1 - avg2), 100)

    def test_team_embed_only_displays_std_dev_when_enabled(self):
        guild = SimpleNamespace(id=2001)
        team1 = tuple(self.players[:5])
        team2 = tuple(self.players[5:])

        with (
            patch.object(embed_service, "load_preferred_roles_setting", return_value=False),
            patch.object(embed_service, "load_lobby_password_for_guild", return_value="test"),
            patch.object(embed_service, "load_mmr_spread_setting", return_value=False),
        ):
            disabled_embed = embed_service.build_team_embed(team1, team2, 0, 0, guild=guild)

        with (
            patch.object(embed_service, "load_preferred_roles_setting", return_value=False),
            patch.object(embed_service, "load_lobby_password_for_guild", return_value="test"),
            patch.object(embed_service, "load_mmr_spread_setting", return_value=True),
        ):
            enabled_embed = embed_service.build_team_embed(team1, team2, 0, 0, guild=guild)

        self.assertNotIn("standard deviation", disabled_embed.description)
        self.assertIn("MMR standard deviation", enabled_embed.description)
        self.assertIn("Difference:", enabled_embed.description)


class TeamBalanceSettingTests(unittest.TestCase):
    def setUp(self):
        self.original_db = guild_config_service.db
        self.original_firestore = guild_config_service.firestore
        self.database = ConfigDatabase()
        guild_config_service.db = self.database
        guild_config_service.firestore = SimpleNamespace(SERVER_TIMESTAMP="timestamp")

    def tearDown(self):
        guild_config_service.db = self.original_db
        guild_config_service.firestore = self.original_firestore

    def test_new_guild_defaults_both_debugging_features_off(self):
        self.assertFalse(guild_config_service.load_mmr_spread_setting(3001))
        self.assertFalse(guild_config_service.load_debug_mode_setting(3001))

    def test_settings_are_persisted_per_guild(self):
        guild_config_service.save_mmr_spread_setting(3001, True, set_by="Admin")
        guild_config_service.save_debug_mode_setting(3001, True, set_by="Admin")

        self.assertTrue(guild_config_service.load_mmr_spread_setting(3001))
        self.assertTrue(guild_config_service.load_debug_mode_setting(3001))
        self.assertFalse(guild_config_service.load_mmr_spread_setting(3002))
        self.assertFalse(guild_config_service.load_debug_mode_setting(3002))


if __name__ == "__main__":
    unittest.main()
