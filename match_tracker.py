import os, requests

STRATZ_TOKEN = os.getenv("STRATZ_TOKEN")

def fetch_match_result(match_id):
    # ---- STRATZ (single try; no sleeps) ----
    try:
        url = "https://api.stratz.com/graphql"
        headers = {
            "Authorization": f"Bearer {STRATZ_TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "STRATZ_API",
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
        resp = requests.post(url, json=query, headers=headers, timeout=6)
        print("[STRATZ] code:", resp.status_code)
        if resp.status_code == 200:
            m = resp.json().get("data", {}).get("match")
            if m:  # indexed
                radiant_win = bool(m["didRadiantWin"])
                players = m.get("players", []) or []
                radiantplayers = [str(p["steamAccountId"]) for p in players if p.get("isRadiant")]
                direplayers    = [str(p["steamAccountId"]) for p in players if not p.get("isRadiant")]
                print("[match-result] source=STRATZ")
                return {"radiant_win": radiant_win, "radiantplayers": radiantplayers, "direplayers": direplayers}
            # 200 but no data → not indexed yet; let outer loop retry later
            print("[STRATZ] 200 but no data → not indexed yet")
            return None
        if resp.status_code == 429:
            # rate-limited; let outer loop wait and retry later
            print("[STRATZ] 429 rate-limited → defer to outer retry")
            return None
        # Non-200 (not 429) → try OpenDota once
        print(f"[STRATZ] HTTP {resp.status_code} → will try OpenDota")
    except Exception as e:
        print("[STRATZ] Exception:", e)
    # ---- OpenDota (single try; no sleeps) ----
    try:
        r = requests.get(f"https://api.opendota.com/api/matches/{match_id}", timeout=7)
        print("[OpenDota] code:", r.status_code)
        if r.status_code == 200:
            j = r.json()
            radiant_win = bool(j.get("radiant_win"))
            players = j.get("players", []) or []
            def is_radiant(p):
                return bool(p.get("isRadiant")) if "isRadiant" in p else int(p.get("player_slot", 0)) < 128
            radiantplayers = [str(p["account_id"]) for p in players if p.get("account_id") is not None and is_radiant(p)]
            direplayers    = [str(p["account_id"]) for p in players if p.get("account_id") is not None and not is_radiant(p)]
            print("[match-result] source=OpenDota")
            return {"radiant_win": radiant_win, "radiantplayers": radiantplayers, "direplayers": direplayers}
    except Exception as e:
        print("[OpenDota] Exception:", e)
    # 404/429/5xx or any failure → let outer loop retry later
    return None