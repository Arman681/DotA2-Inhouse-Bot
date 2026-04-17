import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()


def from_opendota(steam_id: int):
    # Convert to 32-bit only if it's a 64-bit SteamID
    if steam_id > 76561197960265728:
        account_id = steam_id - 76561197960265728
    else:
        account_id = steam_id  # Already 32-bit

    url = f"https://api.opendota.com/api/players/{account_id}"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    j = r.json()

    rank_tier = j.get("rank_tier")
    leaderboard_rank = j.get("leaderboard_rank")

    if rank_tier is None:
        raise ValueError("OpenDota response missing rank_tier — player may be private or unranked.")

    return {
        "rank_tier": rank_tier,
        "leaderboard_rank": leaderboard_rank
    }


def main():
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print("Usage: python scripts/run_public_player_rank.py <steam_id_64_or_account_id>")
        sys.exit(1)

    steam_id = int(sys.argv[1])
    try:
        data = from_opendota(steam_id)
        print(f"Player {steam_id} → rank tier: {data['rank_tier']}, leaderboard rank: {data['leaderboard_rank']}")
    except Exception as e:
        print(f"Error fetching player rank: {e}")


if __name__ == "__main__":
    main()
