import os
import requests

STRATZ_TOKEN = os.getenv("STRATZ_TOKEN")


def _is_radiant_opendota_player(player):
    return bool(player.get("isRadiant")) if "isRadiant" in player else int(player.get("player_slot", 0)) < 128


def _average_int(values):
    cleaned = [int(v) for v in (values or []) if v is not None]
    if not cleaned:
        return None
    return round(sum(cleaned) / len(cleaned))


def _build_stratz_result(match_json):
    players = match_json.get("players", []) or []
    radiantplayers = [str(p["steamAccountId"]) for p in players if p.get("isRadiant")]
    direplayers = [str(p["steamAccountId"]) for p in players if not p.get("isRadiant")]
    player_stats = []

    for player in players:
        steam_account_id = player.get("steamAccountId")
        if steam_account_id is None:
            continue

        actions_per_minute = (
            (player.get("stats") or {}).get("actionsPerMinute") or []
        )
        player_stats.append({
            "steam_id": str(steam_account_id),
            "is_radiant": bool(player.get("isRadiant")),
            "kills": int(player.get("kills", 0) or 0),
            "deaths": int(player.get("deaths", 0) or 0),
            "assists": int(player.get("assists", 0) or 0),
            "last_hits": int(player.get("numLastHits", 0) or 0),
            "denies": int(player.get("numDenies", 0) or 0),
            "net_worth": int(player.get("networth", 0) or 0),
            "gpm": int(player.get("goldPerMinute", 0) or 0),
            "xpm": int(player.get("experiencePerMinute", 0) or 0),
            "hero_damage": int(player.get("heroDamage", 0) or 0),
            "building_damage": int(player.get("towerDamage", 0) or 0),
            "actions_per_minute_timeline": [int(v) for v in actions_per_minute if v is not None],
            "avg_apm": _average_int(actions_per_minute),
            "position": None,
            "imp": None,
        })

    return {
        "radiant_win": bool(match_json.get("didRadiantWin")),
        "radiantplayers": radiantplayers,
        "direplayers": direplayers,
        "player_stats": player_stats,
        "total_kills": sum(int(p.get("kills", 0) or 0) for p in players),
    }


def _build_opendota_result(match_json):
    players = match_json.get("players", []) or []
    radiantplayers = [
        str(p["account_id"])
        for p in players
        if p.get("account_id") is not None and _is_radiant_opendota_player(p)
    ]
    direplayers = [
        str(p["account_id"])
        for p in players
        if p.get("account_id") is not None and not _is_radiant_opendota_player(p)
    ]
    player_stats = []
    for player in players:
        account_id = player.get("account_id")
        if account_id is None:
            continue
        player_stats.append({
            "steam_id": str(account_id),
            "is_radiant": _is_radiant_opendota_player(player),
            "kills": int(player.get("kills", 0) or 0),
            "deaths": int(player.get("deaths", 0) or 0),
            "assists": int(player.get("assists", 0) or 0),
            "last_hits": int(player.get("last_hits", 0) or 0),
            "denies": int(player.get("denies", 0) or 0),
            "net_worth": int(player.get("net_worth", player.get("total_gold", 0)) or 0),
            "gpm": int(player.get("gold_per_min", 0) or 0),
            "xpm": int(player.get("xp_per_min", 0) or 0),
            "hero_damage": int(player.get("hero_damage", 0) or 0),
            "building_damage": int(player.get("tower_damage", 0) or 0),
            "total_dead_time": int(player.get("total_dead_time", 0) or 0),
            "actions_per_minute_timeline": [],
            "avg_apm": None,
            "position": None,
            "imp": None,
        })
    return {
        "radiant_win": bool(match_json.get("radiant_win")),
        "radiantplayers": radiantplayers,
        "direplayers": direplayers,
        "player_stats": player_stats,
        "duration": int(match_json.get("duration", 0) or 0),
        "total_kills": sum(int(p.get("kills", 0) or 0) for p in players),
    }


def _fetch_opendota_result(match_id):
    try:
        r = requests.get(f"https://api.opendota.com/api/matches/{match_id}", timeout=7)
        print("[OpenDota] code:", r.status_code)
        if r.status_code == 200:
            print("[match-result] source=OpenDota")
            return _build_opendota_result(r.json())
    except Exception as e:
        print("[OpenDota] Exception:", e)
    return None


def fetch_match_result(match_id):
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
                  kills
                  deaths
                  assists
                  numLastHits
                  numDenies
                  networth
                  goldPerMinute
                  experiencePerMinute
                  heroDamage
                  towerDamage
                  stats {{
                    actionsPerMinute
                  }}
                }}
              }}
            }}
            """
        }
        resp = requests.post(url, json=query, headers=headers, timeout=8)
        print("[STRATZ] code:", resp.status_code)
        if resp.status_code == 200:
            match_data = resp.json().get("data", {}).get("match")
            if match_data:
                print("[match-result] source=STRATZ")
                return _build_stratz_result(match_data)
            print("[STRATZ] 200 but no data -> not indexed yet")
            return None
        if resp.status_code == 429:
            print("[STRATZ] 429 rate-limited -> defer to outer retry")
            return None
        print(f"[STRATZ] HTTP {resp.status_code} -> will try OpenDota")
    except Exception as e:
        print("[STRATZ] Exception:", e)

    return _fetch_opendota_result(match_id)


def _normalize_position(raw_position):
    if not raw_position:
        return None
    raw_position = str(raw_position).strip().upper()
    mapping = {
        "POSITION_1": "pos1",
        "POSITION_2": "pos2",
        "POSITION_3": "pos3",
        "POSITION_4": "pos4",
        "POSITION_5": "pos5",
    }
    return mapping.get(raw_position)


def fetch_match_imp_data(match_id):
    try:
        url = "https://api.stratz.com/graphql"
        headers = {
            "Authorization": f"Bearer {STRATZ_TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "STRATZ_API",
        }
        query = {
            "query": """
            query CheckMatchImp($matchId: Long!) {
              match(id: $matchId) {
                id
                players {
                  steamAccount {
                    id
                    name
                  }
                  hero {
                    id
                    displayName
                  }
                  position
                  imp
                }
              }
            }
            """,
            "variables": {"matchId": int(match_id)},
        }
        resp = requests.post(url, json=query, headers=headers, timeout=8)
        print("[STRATZ IMP] code:", resp.status_code)
        if resp.status_code != 200:
            return None
        match_data = resp.json().get("data", {}).get("match")
        if not match_data:
            return None
        players = match_data.get("players", []) or []
        if not players:
            return None

        imp_players = []
        for player in players:
            steam_account = player.get("steamAccount") or {}
            steam_id = steam_account.get("id")
            position = _normalize_position(player.get("position"))
            imp_value = player.get("imp")
            hero = player.get("hero") or {}
            imp_players.append({
                "steam_id": str(steam_id) if steam_id is not None else None,
                "steam_name": steam_account.get("name"),
                "hero_id": hero.get("id"),
                "hero_name": hero.get("displayName"),
                "position": position,
                "imp": int(imp_value) if imp_value is not None else None,
            })

        ready_players = [
            player for player in imp_players
            if player.get("steam_id") and player.get("position") is not None and player.get("imp") is not None
        ]
        if len(ready_players) != len(imp_players):
            return None
        return imp_players
    except Exception as e:
        print("[STRATZ IMP] Exception:", e)
        return None
