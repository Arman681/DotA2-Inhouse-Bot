import os, sys, requests
from dotenv import load_dotenv

load_dotenv()

def from_opendota(match_id: int):
    r = requests.get(f"https://api.opendota.com/api/matches/{match_id}", timeout=20)
    r.raise_for_status()
    j = r.json()
    rw = j.get("radiant_win")
    if rw is None:
        raise ValueError("OpenDota response missing radiant_win")
    return "Radiant" if rw else "Dire", {
        "duration": j.get("duration"),
        "radiant_score": j.get("radiant_score"),
        "dire_score": j.get("dire_score")
    }

def main():
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print("Usage: python run_public_match_result.py <match_id>")
        sys.exit(1)
    match_id = int(sys.argv[1])
    winner, extra = from_opendota(match_id)
    print(f"Match {match_id} winner: {winner}")
    print(extra)

if __name__ == "__main__":
    main()