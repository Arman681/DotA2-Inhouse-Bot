import os
import requests
import time

STRATZ_TOKEN = os.getenv("STRATZ_TOKEN")

def fetch_match_result(match_id, max_retries=5):
    url = "https://api.stratz.com/graphql"
    headers = {
        "Authorization": f"Bearer {STRATZ_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "STRATZ_API"
    }
    query = {
        "query": f"""
        query {{
            match(id: {match_id}) {{
                id
                didRadiantWin
                players {{
                    steamAccountId
                    isRadiant
                }}
            }}
        }}
        """
    }

    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=query, headers=headers, timeout=5)

            print("STRATZ response code:", response.status_code)
            print("STRATZ response body:", response.text)

            if response.status_code == 200:
                data = response.json()["data"]["match"]
                radiant_win = data["didRadiantWin"]
                players = data["players"]

                radiant = [str(p["steamAccountId"]) for p in players if p["isRadiant"]]
                dire = [str(p["steamAccountId"]) for p in players if not p["isRadiant"]]

                return {
                    "radiant_win": radiant_win,
                    "radiant": radiant,
                    "dire": dire
                }

            elif response.status_code == 429:
                time.sleep(2 ** attempt)  # exponential backoff
            else:
                return None
        except Exception as e:
            print("Exception occurred while fetching match:", e)
            return None

    return None
