import os
import requests

STRATZ_TOKEN = os.getenv("STRATZ_TOKEN")

def fetch_match_result(match_id):
    url = f"https://api.stratz.com/api/v1/match/{match_id}"
    headers = {
        "Authorization": f"Bearer {STRATZ_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "STRATZ_API"
    }
    resp = requests.get(url, headers=headers)
    if resp.status_code == 200:
        data = resp.json()
        radiant_win = data["didRadiantWin"]
        players = data["players"]
        radiant = [str(p["steamAccountId"]) for p in players if p["isRadiant"]]
        dire = [str(p["steamAccountId"]) for p in players if not p["isRadiant"]]
        return {"radiant_win": radiant_win, "radiant": radiant, "dire": dire}
    return None
