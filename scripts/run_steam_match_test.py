import os
import requests
import sys
from dotenv import load_dotenv

# Load API keys from .env
load_dotenv()
STEAM_API_KEY = os.getenv("STEAM_API_KEY")

# Steam API endpoints
HISTORY_URL = "https://api.steampowered.com/IDOTA2Match_570/GetMatchHistory/v1"
DETAILS_URL = "https://api.steampowered.com/IDOTA2Match_570/GetMatchDetails/v1"


def get_recent_matches():
    params = {
        "key": STEAM_API_KEY,
        "matches_requested": 20,  # Get 20 matches
    }
    resp = requests.get(HISTORY_URL, params=params)
    if resp.status_code != 200:
        print(f"Error fetching match history: {resp.status_code}")
        print(resp.text)
        sys.exit(1)
    matches = resp.json().get("result", {}).get("matches", [])
    return [m["match_id"] for m in matches]


def get_match_winner(match_id):
    params = {
        "key": STEAM_API_KEY,
        "match_id": match_id
    }
    resp = requests.get(DETAILS_URL, params=params)
    if resp.status_code != 200:
        print(f"[{match_id}] ❌ Error {resp.status_code}")
        return None
    data = resp.json().get("result", {})
    radiant_win = data.get("radiant_win")
    if radiant_win is None:
        print(f"[{match_id}] ❌ No winner info")
        return None
    return radiant_win


if __name__ == "__main__":
    matches = get_recent_matches()
    if not matches:
        print("No matches found.")
        sys.exit(1)

    print(f"Fetched {len(matches)} recent matches.\n")

    for match_id in matches:
        winner = get_match_winner(match_id)
        if winner is not None:
            print(f"[{match_id}] ✅ Winner: {'Radiant' if winner else 'Dire'}")
        else:
            print(f"[{match_id}] ❌ Failed to fetch details")
