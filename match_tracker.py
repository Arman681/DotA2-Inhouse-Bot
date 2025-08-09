import os
import requests
import time

STRATZ_TOKEN = os.getenv("STRATZ_TOKEN")

def fetch_match_result(match_id, max_retries=5):
    # ---------- Try STRATZ first ----------
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
            response = requests.post(url, json=query, headers=headers, timeout=6)
            print("[STRATZ] code:", response.status_code)
            if response.status_code == 200:
                data = response.json().get("data", {}).get("match")
                if data:
                    radiant_win = data["didRadiantWin"]
                    players = data.get("players", [])
                    radiantplayers = [str(p["steamAccountId"]) for p in players if p.get("isRadiant")]
                    direplayers    = [str(p["steamAccountId"]) for p in players if not p.get("isRadiant")]
                    print("[match-result] source=STRATZ")
                    return {
                        "radiant_win": radiant_win,
                        "radiantplayers": radiantplayers,
                        "direplayers": direplayers
                    }
                # 200 but no data → likely not indexed yet; fall back to OpenDota
                break
            elif response.status_code == 429:
                time.sleep(2 ** attempt)  # exponential backoff then retry STRATZ
                continue
            else:
                # non-200 and not 429 → fall back to OpenDota
                break
        except Exception as e:
            print("[STRATZ] Exception:", e)
            break  # fall back to OpenDota
    # ---------- OpenDota fallback ----------
    od_url = f"https://api.opendota.com/api/matches/{match_id}"
    for attempt in range(max_retries):
        try:
            r = requests.get(od_url, timeout=7)
            print("[OpenDota] code:", r.status_code)
            if r.status_code == 200:
                j = r.json()
                radiant_win = bool(j.get("radiant_win"))
                players = j.get("players", []) or []
                def is_radiant(p):
                    if "isRadiant" in p:
                        return bool(p["isRadiant"])
                    # Fallback: player_slot < 128 is Radiant
                    try:
                        return int(p.get("player_slot", 0)) < 128
                    except Exception:
                        return False
                radiantplayers = [str(p["account_id"]) for p in players if p.get("account_id") is not None and is_radiant(p)]
                direplayers    = [str(p["account_id"]) for p in players if p.get("account_id") is not None and not is_radiant(p)]
                print("[match-result] source=OpenDota")
                return {
                    "radiant_win": radiant_win,
                    "radiantplayers": radiantplayers,
                    "direplayers": direplayers
                }
            elif r.status_code in (404, 429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            else:
                break
        except Exception as e:
            print("[OpenDota] Exception:", e)
            time.sleep(2 ** attempt)
    return None