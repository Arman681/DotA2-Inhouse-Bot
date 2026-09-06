import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bot.services import embed_service, guild_config_service, lobby_service
from bot.services.immortal_draft import Candidate, ImmortalDraftSession


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
        self.deflated_mmrs = {}

    def collection(self, name):
        if name == "guild_specific_info":
            return ConfigCollection(self.guilds)
        if name == "deflated_mmr":
            return ConfigCollection(self.deflated_mmrs)
        raise AssertionError(f"Unexpected collection: {name}")


class TeamBalanceTests(unittest.TestCase):
    ratings = [2900, 3200, 3300, 3800, 4100, 4900, 5300, 5500, 5600, 6100]

    def setUp(self):
        self.players = [
            (index, f"Player {index}", mmr)
            for index, mmr in enumerate(self.ratings)
        ]
        self.original_db = lobby_service.db
        lobby_service.db = FakePlayerDatabase()
        self.deflated_mmr_patcher = patch.object(lobby_service, "get_deflated_mmr_map", return_value={})
        self.deflated_mmr_patcher.start()

    def tearDown(self):
        self.deflated_mmr_patcher.stop()
        lobby_service.db = self.original_db
        lobby_service.team_rolls.pop(1001, None)
        lobby_service.team_rolls.pop(1002, None)
        lobby_service.team_rolls.pop(1003, None)
        lobby_service.valid_team_combos.pop(1001, None)
        lobby_service.valid_team_combos.pop(1002, None)
        lobby_service.valid_team_combos.pop(1003, None)

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

    def test_deflated_mmr_balances_privately_but_keeps_public_values_in_results(self):
        players = [(index, f"Player {index}", 1000) for index in range(9)]
        players.append((9, "Boosted Player", 10000))

        with (
            patch.object(lobby_service, "get_deflated_mmr_map", return_value={"9": 1000}),
            patch.object(lobby_service, "load_preferred_roles_setting", return_value=False),
            patch.object(lobby_service, "load_mmr_spread_setting", return_value=False),
            patch.object(lobby_service, "get_separated_pairs", return_value=[]),
        ):
            teams, valid_count = lobby_service.calculate_balanced_teams(
                players,
                1003,
                max_mmr_diff=0,
            )

        self.assertGreater(valid_count, 0)
        team1, team2 = teams[0][0], teams[0][1]
        self.assertIn((9, "Boosted Player", 10000), team2)
        self.assertEqual(1000, sum(player[2] for player in team1) / 5)
        self.assertEqual(2800, sum(player[2] for player in team2) / 5)

    def test_deflated_mmr_is_a_cap_and_never_an_inflation(self):
        players = [(1, "Lower Public MMR", 3000), (2, "Higher Public MMR", 6000)]
        with patch.object(
            lobby_service,
            "get_deflated_mmr_map",
            return_value={"1": 4000, "2": 4500},
        ):
            effective = lobby_service.build_effective_mmr_map(players, 1001)

        self.assertEqual(3000, effective["1"])
        self.assertEqual(4500, effective["2"])

    def test_immortal_captain_pairs_use_effective_but_return_public_mmr(self):
        players = [
            (1, "Boosted Player", 6000),
            (2, "Second Player", 5000),
            (3, "Third Player", 4100),
        ]
        with patch.object(lobby_service, "get_deflated_mmr_map", return_value={"1": 4000}):
            pairs = lobby_service.get_all_captain_pairs(players, guild_id=1001)

        captains, _pool, effective_difference = pairs[0]
        self.assertEqual({1, 3}, {captain[0] for captain in captains})
        self.assertEqual(100, effective_difference)
        self.assertIn((1, "Boosted Player", 6000), captains)

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

    def test_team_embed_uses_effective_aggregate_mmr_but_public_player_mmr(self):
        guild = SimpleNamespace(id=2001)
        team1 = tuple(self.players[:5])
        team2 = tuple(self.players[5:])
        effective_mmr_map = {
            str(player_id): mmr
            for player_id, _name, mmr in self.players
        }
        effective_mmr_map["4"] = 1000

        with (
            patch.object(embed_service, "load_preferred_roles_setting", return_value=False),
            patch.object(embed_service, "load_lobby_password_for_guild", return_value="test"),
            patch.object(embed_service, "load_mmr_spread_setting", return_value=True),
        ):
            embed = embed_service.build_team_embed(
                team1,
                team2,
                0,
                0,
                guild=guild,
                mmr_map=effective_mmr_map,
            )

        std_dev1 = lobby_service.calculate_team_mmr_std_dev(team1, effective_mmr_map)
        std_dev2 = lobby_service.calculate_team_mmr_std_dev(team2, effective_mmr_map)
        self.assertIn("T1: 2840, T2: 5480", embed.description)
        self.assertIn(
            f"T1: {std_dev1:.1f}, T2: {std_dev2:.1f}, "
            f"Difference: {abs(std_dev1 - std_dev2):.1f}",
            embed.description,
        )
        self.assertIn("Player 4 (4100)", embed.fields[0].value)


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

    def test_deflated_mmr_overrides_are_audited_updated_and_removed_per_guild(self):
        created, first_entry = guild_config_service.save_deflated_mmr(
            3001,
            42,
            5000,
            guild_name="Test Guild",
            set_by=11,
            name="Player One",
        )
        updated, second_entry = guild_config_service.save_deflated_mmr(
            3001,
            42,
            4800,
            guild_name="Test Guild",
            set_by=22,
            name="Renamed Player",
        )

        self.assertTrue(created)
        self.assertFalse(updated)
        self.assertEqual("11", first_entry["created_by"])
        self.assertEqual("11", second_entry["created_by"])
        self.assertEqual("22", second_entry["updated_by"])
        self.assertEqual({"42": 4800}, guild_config_service.get_deflated_mmr_map(3001))
        self.assertEqual({}, guild_config_service.get_deflated_mmr_map(3002))

        removed, removed_entry = guild_config_service.delete_deflated_mmr(3001, 42)
        self.assertTrue(removed)
        self.assertEqual(4800, removed_entry["mmr"])
        self.assertEqual([], guild_config_service.get_deflated_mmrs(3001))


class ImmortalDraftRatingTests(unittest.TestCase):
    def test_timeout_autopick_uses_effective_mmr_but_displays_public_mmr(self):
        public_low = Candidate(player_id="1", mmr=3000, effective_mmr=3000, name="Public Low")
        privately_lower = Candidate(player_id="2", mmr=5000, effective_mmr=2500, name="Deflated")
        captain_one = SimpleNamespace(id=10, mention="<@10>", display_name="Captain One")
        captain_two = SimpleNamespace(id=20, mention="<@20>", display_name="Captain Two")
        session = ImmortalDraftSession(
            bot=None,
            guild=SimpleNamespace(id=1001),
            channel=None,
            cap1=captain_one,
            cap2=captain_two,
            cap1_mmr=6000,
            cap2_mmr=5900,
            candidates=[privately_lower, public_low],
        )

        self.assertEqual(["1", "2"], [candidate.player_id for candidate in session.candidates])
        self.assertEqual("2", session._autopick_member_id())
        self.assertIn("5000", privately_lower.display())
        self.assertNotIn("2500", privately_lower.display())


if __name__ == "__main__":
    unittest.main()
