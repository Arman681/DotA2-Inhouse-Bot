import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv


LIVE_LEAGUE_GAMES_URL = "https://api.steampowered.com/IDOTA2Match_570/GetLiveLeagueGames/v1/"

CANDIDATE_KEY_FRAGMENTS = (
    "tower",
    "roshan",
    "rosh",
    "first_blood",
    "firstblood",
    "duration",
    "score",
)


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Inspect live Dota 2 league scoreboard payloads from Steam. "
            "Use this locally while a match is live to discover exact fields for "
            "first tower, first Roshan, and related betting markets."
        )
    )
    parser.add_argument("--league-id", help="Only inspect games from this league ID.")
    parser.add_argument("--match-id", help="Only inspect this live match ID.")
    parser.add_argument("--interval", type=int, default=15, help="Seconds between samples in watch mode.")
    parser.add_argument("--samples", type=int, default=1, help="Number of samples to collect.")
    parser.add_argument(
        "--out-dir",
        default="scripts/live_scoreboard_samples",
        help="Directory where raw game and scoreboard JSON files are saved.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all live games returned by Steam and exit.",
    )
    return parser.parse_args()


def fetch_live_games(api_key):
    response = requests.get(LIVE_LEAGUE_GAMES_URL, params={"key": api_key}, timeout=10)
    response.raise_for_status()
    return response.json().get("result", {}).get("games", []) or []


def select_games(games, league_id=None, match_id=None):
    selected = games
    if league_id:
        selected = [game for game in selected if str(game.get("league_id")) == str(league_id)]
    if match_id:
        selected = [game for game in selected if str(game.get("match_id")) == str(match_id)]
    return selected


def short_value(value, limit=120):
    text = repr(value)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def walk_paths(value, path=""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield child_path, key, child
            yield from walk_paths(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            yield child_path, str(index), child
            yield from walk_paths(child, child_path)


def find_candidate_paths(scoreboard):
    matches = []
    for path, key, value in walk_paths(scoreboard):
        key_lower = key.lower()
        if any(fragment in key_lower for fragment in CANDIDATE_KEY_FRAGMENTS):
            matches.append((path, value))
    return matches


def value_shape(value, depth=0, max_depth=4):
    if depth >= max_depth:
        return type(value).__name__
    if isinstance(value, dict):
        shaped = {}
        for key, child in value.items():
            shaped[key] = value_shape(child, depth + 1, max_depth)
        return shaped
    if isinstance(value, list):
        if not value:
            return []
        return [value_shape(value[0], depth + 1, max_depth)]
    return type(value).__name__


def print_game_list(games):
    print(f"Steam returned {len(games)} live games.")
    for game in games:
        scoreboard = game.get("scoreboard") or {}
        duration = scoreboard.get("duration")
        print(
            "match_id={match_id} league_id={league_id} scoreboard={has_scoreboard} duration={duration}".format(
                match_id=game.get("match_id"),
                league_id=game.get("league_id"),
                has_scoreboard=bool(scoreboard),
                duration=duration,
            )
        )


def save_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)


def inspect_game(game, out_dir):
    match_id = str(game.get("match_id", "unknown"))
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    scoreboard = game.get("scoreboard") or {}

    game_path = out_dir / f"{timestamp}_match_{match_id}_game.json"
    scoreboard_path = out_dir / f"{timestamp}_match_{match_id}_scoreboard.json"
    save_json(game_path, game)
    save_json(scoreboard_path, scoreboard)

    print("\n" + "=" * 80)
    print(f"Match ID: {match_id}")
    print(f"League ID: {game.get('league_id')}")
    print(f"Saved game JSON: {game_path}")
    print(f"Saved scoreboard JSON: {scoreboard_path}")

    if not scoreboard:
        print("No scoreboard object is present yet.")
        return

    print("\nTop-level scoreboard keys:")
    print(", ".join(sorted(scoreboard.keys())))

    print("\nScoreboard shape:")
    print(json.dumps(value_shape(scoreboard), indent=2, sort_keys=True))

    candidates = find_candidate_paths(scoreboard)
    print("\nCandidate tower/Roshan/first-blood/duration/score fields:")
    if not candidates:
        print("No candidate fields found by key name.")
    for path, value in candidates:
        print(f"- {path}: {short_value(value)}")


def main():
    load_dotenv()
    args = parse_args()
    api_key = os.getenv("STEAM_API_KEY")
    if not api_key:
        raise SystemExit("Missing STEAM_API_KEY. Add it to .env or your shell environment.")

    out_dir = Path(args.out_dir)
    sample_count = max(1, args.samples)
    interval = max(1, args.interval)

    for sample_number in range(1, sample_count + 1):
        print(f"\nFetching live games sample {sample_number}/{sample_count}...")
        games = fetch_live_games(api_key)
        if args.list:
            print_game_list(games)
            return

        selected = select_games(games, league_id=args.league_id, match_id=args.match_id)
        if not selected:
            print("No matching live games found.")
            print_game_list(games)
        else:
            for game in selected:
                inspect_game(game, out_dir)

        if sample_number < sample_count:
            time.sleep(interval)


if __name__ == "__main__":
    main()
