import math
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import bot.storage.firebase_setup  # ensures Firebase is initialized before anything else
from firebase_admin import firestore

db = firestore.client()

DD_TOKEN_COST = 1000
MIN_BET_AMOUNT = 100

BETTING_MODE_CLASSIC = "classic"
BETTING_MODE_POOL = "pool"
ALLOWED_BETTING_MODES = {BETTING_MODE_CLASSIC, BETTING_MODE_POOL}

DEFAULT_MAIN_POOL_SEED = 1000
DEFAULT_SIDE_POOL_SEED = 250
BETTING_LOCK_SECONDS_AFTER_HEROES = 60

MARKET_MATCH_WINNER = "match"
MARKET_FIRST_BLOOD = "firstblood"
MARKET_FIRST_TO_10 = "first10"
MARKET_FIRST_TOWER = "firsttower"
MARKET_FIRST_ROSHAN = "firstroshan"
MARKET_DURATION_35 = "duration35"
MARKET_TOTAL_KILLS_50 = "totalkills50"
FIRST_BLOOD_AMBIGUOUS = "ambiguous_first_blood_trade"
FIRST_TOWER_AMBIGUOUS = "ambiguous_tower_trade"

BASE_MARKET_ORDER = [MARKET_MATCH_WINNER, MARKET_FIRST_BLOOD, MARKET_FIRST_TO_10]
PROP_MARKET_ORDER = [
    MARKET_FIRST_TOWER,
    MARKET_DURATION_35,
    MARKET_TOTAL_KILLS_50,
]
MARKET_ORDER = BASE_MARKET_ORDER + PROP_MARKET_ORDER
DEPRECATED_MARKET_IDS = {MARKET_FIRST_ROSHAN}

MARKET_DEFINITIONS = {
    MARKET_MATCH_WINNER: {
        "id": MARKET_MATCH_WINNER,
        "index": 1,
        "label": "Match Winner",
        "short_label": "Match Winner",
        "seed": DEFAULT_MAIN_POOL_SEED,
        "side_market": False,
        "prop_market": False,
        "options": ["radiant", "dire"],
    },
    MARKET_FIRST_BLOOD: {
        "id": MARKET_FIRST_BLOOD,
        "index": 2,
        "label": "First Blood",
        "short_label": "First Blood",
        "seed": DEFAULT_SIDE_POOL_SEED,
        "side_market": True,
        "prop_market": False,
        "options": ["radiant", "dire"],
    },
    MARKET_FIRST_TO_10: {
        "id": MARKET_FIRST_TO_10,
        "index": 3,
        "label": "First to 10 Kills",
        "short_label": "First to 10",
        "seed": DEFAULT_SIDE_POOL_SEED,
        "side_market": True,
        "prop_market": False,
        "options": ["radiant", "dire"],
    },
    MARKET_FIRST_TOWER: {
        "id": MARKET_FIRST_TOWER,
        "index": 4,
        "label": "First Tower",
        "short_label": "First Tower",
        "seed": DEFAULT_SIDE_POOL_SEED,
        "side_market": True,
        "prop_market": True,
        "options": ["radiant", "dire"],
    },
    MARKET_DURATION_35: {
        "id": MARKET_DURATION_35,
        "index": 5,
        "label": "Game Duration O/U 35:00",
        "short_label": "Duration 35:00",
        "seed": DEFAULT_SIDE_POOL_SEED,
        "side_market": True,
        "prop_market": True,
        "options": ["over", "under"],
    },
    MARKET_TOTAL_KILLS_50: {
        "id": MARKET_TOTAL_KILLS_50,
        "index": 6,
        "label": "Total Kills O/U 50",
        "short_label": "Total Kills 50",
        "seed": DEFAULT_SIDE_POOL_SEED,
        "side_market": True,
        "prop_market": True,
        "options": ["over", "under"],
    },
}

MARKET_ALIASES = {
    "1": MARKET_MATCH_WINNER,
    "match": MARKET_MATCH_WINNER,
    "winner": MARKET_MATCH_WINNER,
    "matchwinner": MARKET_MATCH_WINNER,
    "match_winner": MARKET_MATCH_WINNER,
    "main": MARKET_MATCH_WINNER,
    "2": MARKET_FIRST_BLOOD,
    "fb": MARKET_FIRST_BLOOD,
    "firstblood": MARKET_FIRST_BLOOD,
    "first_blood": MARKET_FIRST_BLOOD,
    "blood": MARKET_FIRST_BLOOD,
    "3": MARKET_FIRST_TO_10,
    "first10": MARKET_FIRST_TO_10,
    "first_to_10": MARKET_FIRST_TO_10,
    "firstto10": MARKET_FIRST_TO_10,
    "10kills": MARKET_FIRST_TO_10,
    "first10kills": MARKET_FIRST_TO_10,
    "4": MARKET_FIRST_TOWER,
    "tower": MARKET_FIRST_TOWER,
    "firsttower": MARKET_FIRST_TOWER,
    "first_tower": MARKET_FIRST_TOWER,
    "firsttowerkill": MARKET_FIRST_TOWER,
    "first_tower_kill": MARKET_FIRST_TOWER,
    "5": MARKET_DURATION_35,
    "duration": MARKET_DURATION_35,
    "duration35": MARKET_DURATION_35,
    "duration_35": MARKET_DURATION_35,
    "gameduration": MARKET_DURATION_35,
    "game_duration": MARKET_DURATION_35,
    "overunder35": MARKET_DURATION_35,
    "ou35": MARKET_DURATION_35,
    "6": MARKET_TOTAL_KILLS_50,
    "7": MARKET_TOTAL_KILLS_50,
    "kills": MARKET_TOTAL_KILLS_50,
    "totalkills": MARKET_TOTAL_KILLS_50,
    "total_kills": MARKET_TOTAL_KILLS_50,
    "totalkills50": MARKET_TOTAL_KILLS_50,
    "total_kills_50": MARKET_TOTAL_KILLS_50,
    "overunder50": MARKET_TOTAL_KILLS_50,
    "ou50": MARKET_TOTAL_KILLS_50,
}

TEAM_OPTIONS = ("radiant", "dire")
TOTAL_OPTIONS = ("over", "under")


def _now_utc():
    return datetime.now(timezone.utc)


def _safe_int(value, default=0):
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _guild_info_ref(guild_id):
    return db.collection("guild_specific_info").document(str(guild_id))


def _betting_matches_collection(guild_id):
    return db.collection("betting_markets").document(str(guild_id)).collection("matches")


def _match_betting_ref(guild_id, match_id):
    return _betting_matches_collection(guild_id).document(str(match_id))


def _normalize_token(value):
    return str(value or "").lower().strip().replace("-", "_").replace(" ", "_")


def normalize_market_id(value):
    token = _normalize_token(value)
    compact = token.replace("_", "")
    return MARKET_ALIASES.get(token) or MARKET_ALIASES.get(compact)


def get_betting_mode_label(mode):
    return "Prize Pool" if mode == BETTING_MODE_POOL else "Classic"


def get_enabled_market_ids(prop_markets_enabled=False):
    if prop_markets_enabled:
        return list(MARKET_ORDER)
    return list(BASE_MARKET_ORDER)


def get_market_options(market):
    market_id = market.get("id")
    options = market.get("options")
    if options:
        return [str(option).lower() for option in options]
    definition = MARKET_DEFINITIONS.get(market_id, {})
    return [str(option).lower() for option in definition.get("options", TEAM_OPTIONS)]


def get_betting_settings(guild_id):
    doc = _guild_info_ref(guild_id).get()
    raw = {}
    if doc.exists:
        raw = (doc.to_dict() or {}).get("betting_settings", {}) or {}
    mode = str(raw.get("mode", BETTING_MODE_CLASSIC)).lower()
    if mode not in ALLOWED_BETTING_MODES:
        mode = BETTING_MODE_CLASSIC
    carryover = max(0, _safe_int(raw.get("carryover_jackpot"), 0))
    return {
        "mode": mode,
        "mode_set_by": raw.get("mode_set_by", "Unknown"),
        "mode_timestamp": raw.get("mode_timestamp", "Unknown"),
        "prop_markets_enabled": bool(raw.get("prop_markets_enabled", False)),
        "prop_markets_set_by": raw.get("prop_markets_set_by", "Unknown"),
        "prop_markets_timestamp": raw.get("prop_markets_timestamp", "Unknown"),
        "carryover_jackpot": carryover,
        "full_doc": raw,
    }


def save_betting_mode_for_guild(guild_id, mode, server_name=None, set_by=None):
    mode = str(mode or "").lower().strip()
    if mode not in ALLOWED_BETTING_MODES:
        raise ValueError(f"Invalid betting mode: {mode}")
    data = {
        "mode": mode,
        "mode_set_by": str(set_by) if set_by is not None else "Unknown",
        "mode_timestamp": firestore.SERVER_TIMESTAMP,
        "server_name": server_name,
    }
    _guild_info_ref(guild_id).set({"betting_settings": data}, merge=True)


def save_prop_markets_setting(guild_id, enabled, server_name=None, set_by=None):
    data = {
        "prop_markets_enabled": bool(enabled),
        "prop_markets_set_by": str(set_by) if set_by is not None else "Unknown",
        "prop_markets_timestamp": firestore.SERVER_TIMESTAMP,
        "server_name": server_name,
    }
    _guild_info_ref(guild_id).set({"betting_settings": data}, merge=True)


def set_carryover_jackpot(guild_id, amount):
    amount = max(0, _safe_int(amount, 0))
    _guild_info_ref(guild_id).set(
        {"betting_settings": {"carryover_jackpot": amount}},
        merge=True,
    )
    return amount


def add_carryover_jackpot(guild_id, amount):
    amount = max(0, _safe_int(amount, 0))
    settings = get_betting_settings(guild_id)
    return set_carryover_jackpot(guild_id, settings["carryover_jackpot"] + amount)


# ====================================
# Wallet Functions
# ====================================

def get_balance(guild_id, user_id, nickname=None):
    ref = db.collection("wallets").document(str(guild_id)) \
            .collection("users").document(str(user_id))
    snap = ref.get()
    if snap.exists:
        return snap.to_dict().get("balance", 1000)
    payload = {"balance": 1000}
    if nickname:
        payload["nickname"] = nickname
    ref.set(payload, merge=True)
    return 1000


def update_balance(guild_id, user_id, delta, nickname=None):
    ref = db.collection("wallets").document(str(guild_id)) \
           .collection("users").document(str(user_id))
    snap = ref.get()
    if snap.exists and isinstance(snap.to_dict(), dict):
        current = snap.to_dict().get("balance", 1000)
    else:
        current = 1000
    new_balance = int(current) + int(delta)
    payload = {"balance": new_balance}
    if nickname:
        payload["nickname"] = nickname
    ref.set(payload, merge=True)
    return new_balance


# ====================================
# Double Down Token Functions
# ====================================

def get_dd_token_balance(guild_id, user_id, nickname=None):
    ref = db.collection("dd_tokens").document(str(guild_id)) \
            .collection("users").document(str(user_id))
    snap = ref.get()
    if snap.exists:
        return snap.to_dict().get("count", 0)
    payload = {"count": 0}
    if nickname:
        payload["nickname"] = nickname
    ref.set(payload, merge=True)
    return 0


def update_dd_token_balance(guild_id, user_id, delta, nickname=None):
    ref = db.collection("dd_tokens").document(str(guild_id)) \
            .collection("users").document(str(user_id))
    snap = ref.get()
    if snap.exists and isinstance(snap.to_dict(), dict):
        current = snap.to_dict().get("count", 0)
    else:
        current = 0
    new_count = current + int(delta)
    if new_count < 0:
        new_count = 0
    payload = {"count": new_count}
    if nickname:
        payload["nickname"] = nickname
    ref.set(payload, merge=True)


def has_active_double_down(guild_id, user_id):
    ref = db.collection("double_downs").document(str(guild_id)) \
            .collection("users").document(str(user_id))
    return ref.get().exists


def activate_double_down(guild_id, user_id, nickname=None):
    ref = db.collection("double_downs").document(str(guild_id)) \
            .collection("users").document(str(user_id))
    payload = {
        "user_id": str(user_id),
        "timestamp": firestore.SERVER_TIMESTAMP
    }
    if nickname:
        payload["nickname"] = nickname
    ref.set(payload, merge=True)


def get_active_double_down_users(guild_id):
    docs = db.collection("double_downs").document(str(guild_id)) \
             .collection("users").stream()
    return [doc.id for doc in docs]


def clear_active_double_downs(guild_id):
    docs = db.collection("double_downs").document(str(guild_id)) \
             .collection("users").stream()
    batch = db.batch()
    for doc in docs:
        batch.delete(doc.reference)
    batch.commit()
    print(f"[CLEAR] Deleted all active double downs for guild {guild_id}")


# ====================================
# Market State Helpers
# ====================================

def _initial_market_state(market_id, betting_mode, carryover_seed=0):
    definition = MARKET_DEFINITIONS[market_id]
    base_seed = _safe_int(definition.get("seed"), 0) if betting_mode == BETTING_MODE_POOL else 0
    carryover_seed = max(0, _safe_int(carryover_seed, 0)) if market_id == MARKET_MATCH_WINNER else 0
    return {
        "id": market_id,
        "index": definition["index"],
        "label": definition["label"],
        "short_label": definition["short_label"],
        "side_market": bool(definition.get("side_market", False)),
        "prop_market": bool(definition.get("prop_market", False)),
        "options": list(definition.get("options", TEAM_OPTIONS)),
        "status": "open",
        "winner": None,
        "paid": False,
        "seed": base_seed + carryover_seed,
        "base_seed": base_seed,
        "carryover_seed": carryover_seed,
        "pools": {"radiant": 0, "dire": 0},
        "bets": {},
        "created_at": _now_utc(),
    }


def ensure_match_betting_state(guild_id, match_id, random_mode=False):
    if not match_id:
        return None
    ref = _match_betting_ref(guild_id, match_id)
    snap = ref.get()
    if snap.exists:
        data = snap.to_dict() or {}
        mode = data.get("betting_mode", BETTING_MODE_CLASSIC)
        if mode not in ALLOWED_BETTING_MODES:
            mode = BETTING_MODE_CLASSIC
        markets = data.get("markets") or {}
        changed = False
        enabled_market_ids = get_enabled_market_ids(bool(data.get("prop_markets_enabled", False)))
        for market_id in enabled_market_ids:
            if market_id not in markets:
                markets[market_id] = _initial_market_state(market_id, mode, 0)
                changed = True
        if changed:
            data["markets"] = markets
            data["updated_at"] = _now_utc()
            ref.set({"markets": markets, "updated_at": data["updated_at"]}, merge=True)
        return data

    settings = get_betting_settings(guild_id)
    mode = settings["mode"]
    carryover_seed = (
        settings["carryover_jackpot"]
        if mode == BETTING_MODE_POOL and not random_mode
        else 0
    )
    enabled_market_ids = get_enabled_market_ids(settings["prop_markets_enabled"])
    markets = {
        market_id: _initial_market_state(
            market_id,
            mode,
            carryover_seed if market_id == MARKET_MATCH_WINNER else 0,
        )
        for market_id in enabled_market_ids
    }
    now = _now_utc()
    payload = {
        "guild_id": str(guild_id),
        "match_id": str(match_id),
        "betting_mode": mode,
        "prop_markets_enabled": bool(settings["prop_markets_enabled"]),
        "random_mode": bool(random_mode),
        "status": "open",
        "markets": markets,
        "created_at": now,
        "updated_at": now,
        "heroes_fetched_at": None,
        "bets_lock_at": None,
        "bets_locked_at": None,
        "lock_reason": None,
        "live_observations": {},
    }
    ref.set(payload, merge=True)
    if carryover_seed > 0:
        set_carryover_jackpot(guild_id, 0)
    print(
        f"[BETTING_STATE] Created {mode} betting state for guild={guild_id} "
        f"match={match_id} carryover_seed={carryover_seed}"
    )
    return payload


def get_match_betting_state(guild_id, match_id):
    if not match_id:
        return None
    snap = _match_betting_ref(guild_id, match_id).get()
    return snap.to_dict() if snap.exists else None


def _market_bets(market, *, include_voided=False):
    bets = market.get("bets") or {}
    if not isinstance(bets, dict):
        return {}
    if include_voided:
        return bets
    return {
        user_id: bet
        for user_id, bet in bets.items()
        if isinstance(bet, dict) and not bool(bet.get("voided", False))
    }


def get_market_pools(market):
    pools = {option: 0 for option in get_market_options(market)}
    for bet in _market_bets(market).values():
        team = str(bet.get("team", "")).lower()
        if team in pools:
            pools[team] += max(0, _safe_int(bet.get("amount"), 0))
    return pools


def get_market_total_pool(market, betting_mode):
    pools = get_market_pools(market)
    seed = _safe_int(market.get("seed"), 0) if betting_mode == BETTING_MODE_POOL else 0
    return seed + sum(pools.values())


def get_market_multiplier(market, betting_mode, team):
    team = str(team or "").lower()
    if team not in get_market_options(market):
        return None
    if betting_mode == BETTING_MODE_CLASSIC:
        return 2.0
    pools = get_market_pools(market)
    team_pool = pools.get(team, 0)
    if team_pool <= 0:
        return None
    return get_market_total_pool(market, betting_mode) / team_pool


def get_public_market_snapshots(guild_id, match_id):
    state = get_match_betting_state(guild_id, match_id)
    if not state:
        return []
    mode = state.get("betting_mode", BETTING_MODE_CLASSIC)
    markets = state.get("markets") or {}
    snapshots = []
    for market_id in MARKET_ORDER:
        market = markets.get(market_id)
        if not market:
            continue
        pools = get_market_pools(market)
        total_pool = get_market_total_pool(market, mode)
        options = get_market_options(market)
        option_multipliers = {
            option: get_market_multiplier(market, mode, option)
            for option in options
        }
        snapshots.append({
            "id": market_id,
            "index": market.get("index", MARKET_DEFINITIONS[market_id]["index"]),
            "label": market.get("label", MARKET_DEFINITIONS[market_id]["label"]),
            "short_label": market.get("short_label", MARKET_DEFINITIONS[market_id]["short_label"]),
            "status": market.get("status", "open"),
            "winner": market.get("winner"),
            "paid": bool(market.get("paid", False)),
            "side_market": bool(market.get("side_market", False)),
            "prop_market": bool(market.get("prop_market", False)),
            "options": options,
            "seed": _safe_int(market.get("seed"), 0) if mode == BETTING_MODE_POOL else 0,
            "base_seed": _safe_int(market.get("base_seed"), 0),
            "carryover_seed": _safe_int(market.get("carryover_seed"), 0),
            "pools": pools,
            "total_pool": total_pool,
            "option_multipliers": option_multipliers,
            "radiant_multiplier": get_market_multiplier(market, mode, "radiant"),
            "dire_multiplier": get_market_multiplier(market, mode, "dire"),
            "bet_count": len(_market_bets(market)),
        })
    return snapshots


def get_betting_summary(guild_id, match_id):
    state = get_match_betting_state(guild_id, match_id)
    if not state:
        return None
    mode = state.get("betting_mode", BETTING_MODE_CLASSIC)
    snapshots = get_public_market_snapshots(guild_id, match_id)
    return {
        "match_id": str(match_id),
        "mode": mode,
        "mode_label": get_betting_mode_label(mode),
        "status": state.get("status", "open"),
        "heroes_fetched_at": state.get("heroes_fetched_at"),
        "bets_lock_at": state.get("bets_lock_at"),
        "bets_locked_at": state.get("bets_locked_at"),
        "lock_reason": state.get("lock_reason"),
        "markets": snapshots,
    }


def get_existing_market_bet(guild_id, match_id, market_id, user_id):
    state = get_match_betting_state(guild_id, match_id)
    if not state:
        return None
    market = (state.get("markets") or {}).get(market_id) or {}
    return (_market_bets(market).get(str(user_id)) or None)


def is_market_open_for_betting(guild_id, match_id, market_id):
    state = get_match_betting_state(guild_id, match_id)
    if not state or state.get("status") in {"paid", "cancelled"}:
        return False
    market = (state.get("markets") or {}).get(market_id) or {}
    return market.get("status") == "open" and not bool(market.get("paid", False))


# ====================================
# Live Match Market Updates
# ====================================

def _as_aware_utc(value):
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _scoreboard_from_match(match):
    if not isinstance(match, dict):
        return {}
    return match.get("scoreboard") or {}


def get_live_kill_scores(match):
    scoreboard = _scoreboard_from_match(match)
    radiant = scoreboard.get("radiant") or {}
    dire = scoreboard.get("dire") or {}
    radiant_kills = _safe_int(radiant.get("score"), None)
    dire_kills = _safe_int(dire.get("score"), None)
    if radiant_kills is None:
        radiant_players = radiant.get("players") or []
        radiant_kills = sum(_safe_int(p.get("kills"), 0) for p in radiant_players)
    if dire_kills is None:
        dire_players = dire.get("players") or []
        dire_kills = sum(_safe_int(p.get("kills"), 0) for p in dire_players)
    return radiant_kills, dire_kills


def _scoreboard_duration(match):
    return _safe_int(_scoreboard_from_match(match).get("duration"), 0)


def _has_all_heroes(match):
    players = [
        p for p in (match.get("players") or [])
        if p.get("team") in (0, 1, "0", "1")
    ]
    if len(players) < 10:
        return False
    return all(_safe_int(p.get("hero_id"), 0) > 0 for p in players[:10])


def update_market_lock_state(guild_id, match_id, match):
    data = ensure_match_betting_state(guild_id, match_id)
    if not data or data.get("status") in {"paid", "cancelled"}:
        return data
    ref = _match_betting_ref(guild_id, match_id)
    markets = deepcopy(data.get("markets") or {})
    now = _now_utc()
    updates = {}

    heroes_fetched_at = _as_aware_utc(data.get("heroes_fetched_at"))
    bets_lock_at = _as_aware_utc(data.get("bets_lock_at"))
    if _has_all_heroes(match) and heroes_fetched_at is None:
        heroes_fetched_at = now
        bets_lock_at = now + timedelta(seconds=BETTING_LOCK_SECONDS_AFTER_HEROES)
        updates["heroes_fetched_at"] = heroes_fetched_at
        updates["bets_lock_at"] = bets_lock_at

    radiant_kills, dire_kills = get_live_kill_scores(match)
    duration = _scoreboard_duration(match)
    lock_reason = None
    random_mode = bool(data.get("random_mode", False))
    if not random_mode and duration > 0:
        lock_reason = "game_started"
    elif not random_mode and radiant_kills + dire_kills > 0:
        lock_reason = "score_changed"
    elif bets_lock_at is not None and now >= bets_lock_at:
        lock_reason = "hero_lock_timer"

    if lock_reason:
        changed = False
        for market in markets.values():
            if market.get("status") == "open":
                market["status"] = "locked"
                changed = True
        if changed:
            updates["markets"] = markets
            updates["status"] = "locked"
            updates["bets_locked_at"] = now
            updates["lock_reason"] = lock_reason

    if updates:
        updates["updated_at"] = now
        ref.set(updates, merge=True)
        data.update(updates)
    return data


def _team_from_live_value(value, total_kills):
    if isinstance(value, str):
        lowered = value.lower().strip()
        if lowered in TEAM_OPTIONS:
            return lowered
        if lowered in {"goodguys", "good_guys", "radiant_team"}:
            return "radiant"
        if lowered in {"badguys", "bad_guys", "dire_team"}:
            return "dire"
    number = _safe_int(value, None)
    if number is None or total_kills <= 0:
        return None
    if number in (0, 2):
        return "radiant"
    if number in (1, 3):
        return "dire"
    return None


def _option_from_over_under(value):
    if isinstance(value, str):
        lowered = value.lower().strip()
        if lowered in TOTAL_OPTIONS:
            return lowered
    return None


def _first_matching_team_field(scoreboard, keys):
    total_kills = sum(get_live_kill_scores({"scoreboard": scoreboard}))
    for key in keys:
        if key in scoreboard:
            team = _team_from_live_value(scoreboard.get(key), total_kills)
            if team:
                return team
    return None


def _extract_team_stat(scoreboard, team, keys):
    team_data = scoreboard.get(team) or {}
    for key in keys:
        if key in team_data:
            return _safe_int(team_data.get(key), None)
    for key in keys:
        full_key = f"{team}_{key}"
        if full_key in scoreboard:
            return _safe_int(scoreboard.get(full_key), None)
    return None


def _detect_first_blood_winner(match, state):
    scoreboard = _scoreboard_from_match(match)
    radiant_kills, dire_kills = get_live_kill_scores(match)
    total_kills = radiant_kills + dire_kills
    for key in ("first_blood_team", "firstblood_team", "first_blood", "firstblood"):
        if key in scoreboard:
            team = _team_from_live_value(scoreboard.get(key), total_kills)
            if team:
                return team
    if total_kills <= 0:
        return None
    previous = state.get("live_observations") or {}
    prev_radiant = _safe_int(previous.get("radiant_kills"), None)
    prev_dire = _safe_int(previous.get("dire_kills"), None)
    if (
        prev_radiant == 0
        and prev_dire == 0
        and radiant_kills > 0
        and dire_kills > 0
    ):
        return FIRST_BLOOD_AMBIGUOUS
    if radiant_kills > 0 and dire_kills == 0:
        return "radiant"
    if dire_kills > 0 and radiant_kills == 0:
        return "dire"
    return None


def _detect_first_to_10_winner(match, state):
    radiant_kills, dire_kills = get_live_kill_scores(match)
    if radiant_kills >= 10 and dire_kills < 10:
        return "radiant"
    if dire_kills >= 10 and radiant_kills < 10:
        return "dire"
    return None


def _detect_first_tower_winner(match, state):
    scoreboard = _scoreboard_from_match(match)
    radiant_tower_state = _extract_team_stat(scoreboard, "radiant", ("tower_state", "towerState"))
    dire_tower_state = _extract_team_stat(scoreboard, "dire", ("tower_state", "towerState"))
    previous = (state.get("live_observations") or {}).get("tower_states") or {}
    prev_radiant = _safe_int(previous.get("radiant"), None)
    prev_dire = _safe_int(previous.get("dire"), None)
    if None not in (radiant_tower_state, dire_tower_state, prev_radiant, prev_dire):
        radiant_destroyed = int(prev_radiant) & ~int(radiant_tower_state)
        dire_destroyed = int(prev_dire) & ~int(dire_tower_state)
        radiant_lost = radiant_destroyed != 0
        dire_lost = dire_destroyed != 0
        if radiant_lost and dire_lost:
            return FIRST_TOWER_AMBIGUOUS
        if radiant_lost:
            return "dire"
        if dire_lost:
            return "radiant"

    direct = _first_matching_team_field(scoreboard, (
        "first_tower_team",
        "firsttower_team",
        "first_tower",
        "firsttower",
        "first_tower_kill_team",
        "first_tower_destroyed_by",
    ))
    if direct:
        return direct
    return None


def _duration_market_winner(duration):
    duration = _safe_int(duration, None)
    if duration is None or duration <= 0:
        return None
    return "over" if duration >= 35 * 60 else "under"


def _total_kills_market_winner(total_kills):
    total_kills = _safe_int(total_kills, None)
    if total_kills is None:
        return None
    return "over" if total_kills >= 50 else "under"


def _final_market_winners_from_state(state, match_result=None):
    match_result = match_result or {}
    live_observations = state.get("live_observations") or {}
    winners = {}

    duration = match_result.get("duration")
    if duration is None:
        duration = live_observations.get("duration")
    duration_winner = _duration_market_winner(duration)
    if duration_winner:
        winners[MARKET_DURATION_35] = duration_winner

    total_kills = match_result.get("total_kills")
    if total_kills is None:
        player_stats = match_result.get("player_stats") or []
        if player_stats:
            total_kills = sum(_safe_int(player.get("kills"), 0) for player in player_stats)
    if total_kills is None and (
        "radiant_kills" in live_observations or "dire_kills" in live_observations
    ):
        total_kills = _safe_int(live_observations.get("radiant_kills"), 0) + _safe_int(live_observations.get("dire_kills"), 0)
    total_kills_winner = _total_kills_market_winner(total_kills)
    if total_kills_winner:
        winners[MARKET_TOTAL_KILLS_50] = total_kills_winner

    return winners


def _void_market_state(guild_id, market, reason=None, voided_by=None):
    if market.get("status") == "voided":
        return {
            "market_id": market.get("id"),
            "market_label": market.get("label", market.get("id", "Market")),
            "bet_count": len(_market_bets(market, include_voided=True)),
            "refunded_total": _safe_int(market.get("refunded_total"), 0),
            "already_voided": True,
        }

    refunded_total = 0
    net_adjustment = 0
    bet_count = 0
    bets = _market_bets(market, include_voided=True)
    for user_id, bet in bets.items():
        if not isinstance(bet, dict) or bool(bet.get("voided", False)):
            continue
        amount = _safe_int(bet.get("amount"), 0)
        gross_payout = _safe_int(bet.get("gross_payout"), 0) if market.get("paid") else 0
        refund_delta = amount - gross_payout
        nickname = bet.get("nickname")
        before = int(get_balance(guild_id, user_id, nickname) or 0)
        after = update_balance(guild_id, user_id, refund_delta, nickname)
        bet_count += 1
        refunded_total += amount
        net_adjustment += refund_delta
        bet["voided"] = True
        bet["void_reason"] = reason or "No reason provided"
        bet["voided_by"] = str(voided_by) if voided_by else "Unknown"
        bet["voided_at"] = _now_utc()
        bet["refund_delta"] = refund_delta
        bet["refunded_amount"] = amount
        bet["balance_before_refund"] = before
        bet["balance_after_refund"] = after

    market["bets"] = bets
    market["status"] = "voided"
    market["voided"] = True
    market["paid"] = True
    market["winner"] = None
    market["voided_by"] = str(voided_by) if voided_by else "Unknown"
    market["void_reason"] = reason or "No reason provided"
    market["voided_at"] = _now_utc()
    market["refunded_total"] = refunded_total
    market["net_refund_adjustment"] = net_adjustment
    return {
        "market_id": market.get("id"),
        "market_label": market.get("label", market.get("id", "Market")),
        "bet_count": bet_count,
        "refunded_total": refunded_total,
        "net_adjustment": net_adjustment,
        "already_voided": False,
    }


def process_live_betting_markets(guild_id, match_id, match):
    data = update_market_lock_state(guild_id, match_id, match)
    if not data or data.get("status") in {"paid", "cancelled"}:
        return data
    ref = _match_betting_ref(guild_id, match_id)
    markets = deepcopy(data.get("markets") or {})
    now = _now_utc()
    changed = False

    for deprecated_market_id in DEPRECATED_MARKET_IDS:
        deprecated_market = markets.get(deprecated_market_id)
        if deprecated_market and not deprecated_market.get("paid"):
            _void_market_state(
                guild_id,
                deprecated_market,
                reason="Market removed because Steam live data does not identify the result reliably.",
                voided_by="system",
            )
            markets[deprecated_market_id] = deprecated_market
            changed = True

    first_blood = markets.get(MARKET_FIRST_BLOOD)
    if first_blood and first_blood.get("status") in {"open", "locked"} and not first_blood.get("winner"):
        winner = _detect_first_blood_winner(match, data)
        if winner == FIRST_BLOOD_AMBIGUOUS:
            _void_market_state(
                guild_id,
                first_blood,
                reason="Both teams got kills between Steam API polls.",
                voided_by="system",
            )
            markets[MARKET_FIRST_BLOOD] = first_blood
            changed = True
        elif winner:
            first_blood["status"] = "resolved"
            first_blood["winner"] = winner
            first_blood["resolved_at"] = now
            first_blood["result_source"] = "live_scoreboard"
            changed = True

    first_to_10 = markets.get(MARKET_FIRST_TO_10)
    if first_to_10 and first_to_10.get("status") in {"open", "locked"} and not first_to_10.get("winner"):
        winner = _detect_first_to_10_winner(match, data)
        if winner:
            first_to_10["status"] = "resolved"
            first_to_10["winner"] = winner
            first_to_10["resolved_at"] = now
            first_to_10["result_source"] = "live_scoreboard"
            changed = True

    first_tower = markets.get(MARKET_FIRST_TOWER)
    if first_tower and first_tower.get("status") in {"open", "locked"} and not first_tower.get("winner"):
        winner = _detect_first_tower_winner(match, data)
        if winner == FIRST_TOWER_AMBIGUOUS:
            _void_market_state(
                guild_id,
                first_tower,
                reason="Both teams lost a tower between Steam API polls.",
                voided_by="system",
            )
            markets[MARKET_FIRST_TOWER] = first_tower
            changed = True
        elif winner:
            first_tower["status"] = "resolved"
            first_tower["winner"] = winner
            first_tower["resolved_at"] = now
            first_tower["result_source"] = "live_scoreboard"
            changed = True

    radiant_kills, dire_kills = get_live_kill_scores(match)
    scoreboard = _scoreboard_from_match(match)
    previous_observations = data.get("live_observations") or {}
    previous_tower_states = previous_observations.get("tower_states") or {}
    tower_states = {
        "radiant": _extract_team_stat(scoreboard, "radiant", ("tower_state", "towerState")),
        "dire": _extract_team_stat(scoreboard, "dire", ("tower_state", "towerState")),
    }
    tower_states = {
        team: value if value is not None else previous_tower_states.get(team)
        for team, value in tower_states.items()
    }
    updates = {
        "live_observations": {
            "radiant_kills": radiant_kills,
            "dire_kills": dire_kills,
            "duration": _scoreboard_duration(match),
            "tower_states": tower_states,
            "updated_at": now,
        },
        "updated_at": now,
    }
    if changed:
        updates["markets"] = markets
    ref.set(updates, merge=True)
    data.update(updates)
    if changed:
        data["markets"] = markets
    return data


# ====================================
# Betting Functions
# ====================================

def place_market_bet(user_id, team, amount, delta, guild_id, match_id, market_id, nickname):
    team = str(team or "").lower().strip()
    market_id = normalize_market_id(market_id) or MARKET_MATCH_WINNER
    if market_id not in MARKET_DEFINITIONS:
        raise ValueError("Invalid market")
    state = ensure_match_betting_state(guild_id, match_id)
    if not state:
        raise ValueError("Missing match betting state")
    if not is_market_open_for_betting(guild_id, match_id, market_id):
        raise ValueError("Market is not open")

    ref = _match_betting_ref(guild_id, match_id)
    state = get_match_betting_state(guild_id, match_id) or state
    markets = deepcopy(state.get("markets") or {})
    market = markets.get(market_id)
    if not market:
        raise ValueError("Market not found")
    if team not in get_market_options(market):
        raise ValueError("Invalid option for this market")
    bets = _market_bets(market, include_voided=True)

    user_id = str(user_id)
    amount = _safe_int(amount, 0)
    wager_delta = _safe_int(delta, 0)
    if amount <= 0 or wager_delta <= 0:
        raise ValueError("Bet amount must be positive")
    if amount < MIN_BET_AMOUNT:
        raise ValueError(f"Minimum bet amount is {MIN_BET_AMOUNT} Feederbucks")

    existing = bets.get(user_id) or {}
    if isinstance(existing, dict) and existing.get("voided"):
        existing = {}
    balance_before_delta = int(get_balance(guild_id, user_id, nickname) or 0)
    balance_after_delta = balance_before_delta - wager_delta
    update_balance(guild_id, user_id, -wager_delta, nickname)

    bets[user_id] = {
        "nickname": nickname,
        "user_id": user_id,
        "team": team,
        "amount": amount,
        "balance_before_bet": existing.get("balance_before_bet", balance_before_delta),
        "balance_after_bet": balance_after_delta,
        "timestamp": _now_utc(),
    }
    market["bets"] = bets
    market["pools"] = get_market_pools(market)
    market["updated_at"] = _now_utc()
    markets[market_id] = market
    ref.set({"markets": markets, "updated_at": _now_utc()}, merge=True)
    return True


def place_bet(user_id, team, amount, delta, guild_id, nickname):
    entry_ref = db.collection("bets").document(str(guild_id)).collection("entries").document(str(user_id))
    amount = _safe_int(amount, 0)
    wager_delta = _safe_int(delta, 0)
    if amount <= 0 or wager_delta <= 0:
        raise ValueError("Bet amount must be positive")
    if amount < MIN_BET_AMOUNT:
        raise ValueError(f"Minimum bet amount is {MIN_BET_AMOUNT} Feederbucks")
    balance_before_bet = int(get_balance(guild_id, user_id, nickname) or 0)
    balance_after_bet = balance_before_bet - wager_delta
    update_balance(guild_id, user_id, -wager_delta, nickname)
    entry_ref.set({
        "nickname": nickname,
        "user_id": str(user_id),
        "team": team,
        "amount": amount,
        "balance_before_bet": balance_before_bet,
        "balance_after_bet": balance_after_bet,
        "timestamp": firestore.SERVER_TIMESTAMP
    })
    return True


def _result_entry(market, bet, *, won, net_delta, gross_payout, balance_after, note=None):
    team = str(bet.get("team", "unknown")).lower()
    result = {
        "market_id": market.get("id"),
        "market_label": market.get("label"),
        "user_id": str(bet.get("user_id", "")),
        "nickname": bet.get("nickname", "Unknown"),
        "team": team,
        "amount": _safe_int(bet.get("amount"), 0),
        "won": bool(won),
        "net_delta": int(net_delta),
        "gross_payout": int(gross_payout),
        "balance_before": _safe_int(bet.get("balance_before_bet"), 1000),
        "balance_after": int(balance_after),
    }
    if note:
        result["note"] = note
    return result


def _resolve_market_payouts(guild_id, market, betting_mode, *, allow_carryover=True):
    winner = str(market.get("winner") or "").lower()
    bets = _market_bets(market)
    all_bets = _market_bets(market, include_voided=True)
    options = get_market_options(market)
    results = []
    if winner not in options:
        for bet in bets.values():
            amount = _safe_int(bet.get("amount"), 0)
            balance_after = int(get_balance(guild_id, bet.get("user_id"), bet.get("nickname")) or 0)
            results.append(_result_entry(
                market,
                bet,
                won=False,
                net_delta=-amount,
                gross_payout=0,
                balance_after=balance_after,
                note="cancelled_no_winner",
            ))
        market["status"] = "cancelled"
        market["paid"] = True
        return results, market, 0

    pools = get_market_pools(market)
    winning_pool = pools.get(winner, 0)
    total_pool = get_market_total_pool(market, betting_mode)
    eligible_carryover_pool = max(0, total_pool - _safe_int(market.get("base_seed"), 0))
    carryover_added = 0

    if (
        allow_carryover
        and betting_mode == BETTING_MODE_POOL
        and market.get("id") == MARKET_MATCH_WINNER
        and winning_pool <= 0
        and eligible_carryover_pool > 0
    ):
        carryover_added = eligible_carryover_pool
        add_carryover_jackpot(guild_id, eligible_carryover_pool)

    for user_id, bet in bets.items():
        amount = _safe_int(bet.get("amount"), 0)
        team = str(bet.get("team", "")).lower()
        nickname = bet.get("nickname")
        gross_payout = 0
        won = team == winner and winning_pool > 0
        if won:
            if betting_mode == BETTING_MODE_POOL:
                gross_payout = int(math.ceil(total_pool * (amount / winning_pool)))
            else:
                gross_payout = amount * 2
            balance_before_credit = int(get_balance(guild_id, user_id, nickname) or 0)
            balance_after = update_balance(guild_id, user_id, gross_payout, nickname)
            net_delta = gross_payout - amount
            bet["balance_before_payout"] = balance_before_credit
            bet["balance_after_payout"] = balance_after
        else:
            balance_after = int(get_balance(guild_id, user_id, nickname) or 0)
            net_delta = -amount
        bet["resolved"] = True
        bet["won"] = won
        bet["gross_payout"] = gross_payout
        bet["net_delta"] = net_delta
        bet["balance_after"] = balance_after
        results.append(_result_entry(
            market,
            bet,
            won=won,
            net_delta=net_delta,
            gross_payout=gross_payout,
            balance_after=balance_after,
        ))

    market["bets"] = all_bets
    market["pools"] = pools
    market["total_pool"] = total_pool
    market["winning_pool"] = winning_pool
    market["carryover_jackpot_added"] = carryover_added
    market["status"] = "paid"
    market["paid"] = True
    market["paid_at"] = _now_utc()
    return results, market, carryover_added


def _resolve_market_bets(guild_id, match_id, winning_team, market_winners=None, match_result=None):
    state = ensure_match_betting_state(guild_id, match_id)
    if not state:
        return []
    ref = _match_betting_ref(guild_id, match_id)
    betting_mode = state.get("betting_mode", BETTING_MODE_CLASSIC)
    random_mode = bool(state.get("random_mode", False))
    markets = deepcopy(state.get("markets") or {})
    market_winners = {
        **_final_market_winners_from_state(state, match_result=match_result),
        **(market_winners or {}),
    }
    all_results = []
    total_carryover_added = 0
    now = _now_utc()

    for deprecated_market_id in DEPRECATED_MARKET_IDS:
        deprecated_market = markets.get(deprecated_market_id)
        if deprecated_market and not deprecated_market.get("paid"):
            _void_market_state(
                guild_id,
                deprecated_market,
                reason="Market removed because Steam live data does not identify the result reliably.",
                voided_by="system",
            )
            markets[deprecated_market_id] = deprecated_market

    for market_id in MARKET_ORDER:
        market = markets.get(market_id)
        if not market:
            continue
        if market.get("paid"):
            continue
        if market.get("status") == "voided":
            market["paid"] = True
            markets[market_id] = market
            continue
        if market_id == MARKET_MATCH_WINNER:
            market["winner"] = str(winning_team).lower()
            market["status"] = "resolved"
            market["resolved_at"] = now
            market["result_source"] = "match_result"
        elif market_id in market_winners:
            market["winner"] = str(market_winners[market_id]).lower()
            market["status"] = "resolved"
            market["resolved_at"] = market.get("resolved_at") or now
            market["result_source"] = "manual_or_live"
        elif market.get("winner"):
            market["status"] = "resolved"
        else:
            market["winner"] = None

        results, resolved_market, carryover_added = _resolve_market_payouts(
            guild_id,
            market,
            betting_mode,
            allow_carryover=not random_mode,
        )
        all_results.extend(results)
        total_carryover_added += carryover_added
        markets[market_id] = resolved_market

    ref.set({
        "markets": markets,
        "status": "paid",
        "paid_at": now,
        "updated_at": now,
        "carryover_jackpot_added": total_carryover_added,
    }, merge=True)
    return all_results


def _resolve_legacy_bets(guild_id, winning_team):
    entries = list(db.collection("bets").document(str(guild_id)).collection("entries").stream())
    print(f"[RESOLVE_BETS] Resolving {len(entries)} legacy bets for guild: {guild_id}")
    batch = db.batch()
    logs = []
    results = []
    for doc in entries:
        data = doc.to_dict()
        user_id = data.get("user_id")
        nickname = data.get("nickname", "Unknown")
        team = data.get("team")
        amount = int(data.get("amount", 0) or 0)
        balance_before_bet = int(data.get("balance_before_bet", get_balance(guild_id, user_id, nickname) if user_id else 1000) or 0)
        balance_after_bet = int(data.get("balance_after_bet", balance_before_bet - amount) or 0)
        if not user_id or not team:
            print(f"[WARN] Missing data in doc {doc.id}")
            batch.delete(doc.reference)
            continue
        if team == winning_team:
            wallet_ref = db.collection("wallets").document(str(guild_id)).collection("users").document(str(user_id))
            final_balance = balance_after_bet + (amount * 2)
            batch.set(wallet_ref, {"balance": final_balance, "nickname": nickname}, merge=True)
            logs.append(("win", user_id, amount, team, nickname))
            results.append({
                "market_id": MARKET_MATCH_WINNER,
                "market_label": "Match Winner",
                "user_id": str(user_id),
                "nickname": nickname,
                "team": team,
                "amount": amount,
                "won": True,
                "net_delta": amount,
                "gross_payout": amount * 2,
                "balance_before": balance_before_bet,
                "balance_after": final_balance,
            })
        else:
            logs.append(("lose", user_id, amount, team, nickname))
            results.append({
                "market_id": MARKET_MATCH_WINNER,
                "market_label": "Match Winner",
                "user_id": str(user_id),
                "nickname": nickname,
                "team": team,
                "amount": amount,
                "won": False,
                "net_delta": -amount,
                "gross_payout": 0,
                "balance_before": balance_before_bet,
                "balance_after": balance_after_bet,
            })
        batch.delete(doc.reference)
    batch.commit()
    for outcome, user_id, amount, team, nickname in logs:
        if outcome == "win":
            print(f"[RESOLVE_BETS] WIN {nickname} ({user_id}) won {amount} on {team}")
        else:
            print(f"[RESOLVE_BETS] LOSE {nickname} ({user_id}) lost {amount} on {team}")
    return results


def resolve_bets(guild_id, winning_team, match_id=None, market_winners=None, match_result=None):
    if match_id and get_match_betting_state(guild_id, match_id):
        print(f"[RESOLVE_BETS] Resolving market bets for guild={guild_id} match={match_id}")
        return _resolve_market_bets(
            guild_id,
            match_id,
            winning_team,
            market_winners=market_winners,
            match_result=match_result,
        )
    return _resolve_legacy_bets(guild_id, winning_team)


def void_market(guild_id, match_id, market_id, reason=None, voided_by=None):
    market_id = normalize_market_id(market_id)
    if market_id not in MARKET_DEFINITIONS:
        raise ValueError("Invalid market")
    state = get_match_betting_state(guild_id, match_id)
    if not state:
        raise ValueError("No betting state found for this match")
    ref = _match_betting_ref(guild_id, match_id)
    markets = deepcopy(state.get("markets") or {})
    market = markets.get(market_id)
    if not market:
        raise ValueError("Market not found")
    if market.get("status") == "voided":
        return {
            "market_id": market_id,
            "market_label": market.get("label", market_id),
            "bet_count": len(_market_bets(market, include_voided=True)),
            "refunded_total": _safe_int(market.get("refunded_total"), 0),
            "already_voided": True,
        }

    result = _void_market_state(guild_id, market, reason=reason, voided_by=voided_by)
    markets[market_id] = market
    ref.set({"markets": markets, "updated_at": _now_utc()}, merge=True)
    return result


def _void_user_bet_state(guild_id, market, user_id, reason=None, voided_by=None):
    user_id = str(user_id)
    bets = _market_bets(market, include_voided=True)
    bet = bets.get(user_id)
    market_id = market.get("id")
    market_label = market.get("label", market_id or "Market")
    base_result = {
        "market_id": market_id,
        "market_label": market_label,
        "user_id": user_id,
    }
    if not isinstance(bet, dict):
        return {
            **base_result,
            "bet_found": False,
            "voided_now": False,
            "already_voided": False,
            "refunded_total": 0,
            "net_adjustment": 0,
        }
    amount = _safe_int(bet.get("amount"), 0)
    if bet.get("voided"):
        return {
            **base_result,
            "bet_found": True,
            "voided_now": False,
            "already_voided": True,
            "team": bet.get("team"),
            "amount": amount,
            "refunded_total": _safe_int(bet.get("refunded_amount"), amount),
            "net_adjustment": _safe_int(bet.get("refund_delta"), 0),
        }

    nickname = bet.get("nickname")
    gross_payout = _safe_int(bet.get("gross_payout"), 0) if market.get("paid") else 0
    refund_delta = amount - gross_payout
    before = int(get_balance(guild_id, user_id, nickname) or 0)
    after = update_balance(guild_id, user_id, refund_delta, nickname)
    bet["voided"] = True
    bet["void_reason"] = reason or "No reason provided"
    bet["voided_by"] = str(voided_by) if voided_by else "Unknown"
    bet["voided_at"] = _now_utc()
    bet["refund_delta"] = refund_delta
    bet["refunded_amount"] = amount
    bet["balance_before_refund"] = before
    bet["balance_after_refund"] = after
    bets[user_id] = bet
    market["bets"] = bets
    market["pools"] = get_market_pools(market)
    market["updated_at"] = _now_utc()
    return {
        **base_result,
        "bet_found": True,
        "voided_now": True,
        "already_voided": False,
        "team": bet.get("team"),
        "amount": amount,
        "refunded_total": amount,
        "net_adjustment": refund_delta,
        "balance_before_refund": before,
        "balance_after_refund": after,
    }


def _ordered_market_ids(markets):
    ordered = [market_id for market_id in MARKET_ORDER if market_id in markets]
    ordered.extend(market_id for market_id in markets.keys() if market_id not in ordered)
    return ordered


def void_user_bets(guild_id, match_id, user_id, market_ids=None, reason=None, voided_by=None):
    state = get_match_betting_state(guild_id, match_id)
    if not state:
        raise ValueError("No betting state found for this match")
    ref = _match_betting_ref(guild_id, match_id)
    markets = deepcopy(state.get("markets") or {})
    if market_ids is None:
        target_market_ids = _ordered_market_ids(markets)
    else:
        target_market_ids = []
        for raw_market_id in market_ids:
            market_id = normalize_market_id(raw_market_id)
            if market_id not in MARKET_DEFINITIONS:
                raise ValueError("Invalid market")
            if market_id not in target_market_ids:
                target_market_ids.append(market_id)

    results = []
    changed = False
    for market_id in target_market_ids:
        market = markets.get(market_id)
        if not market:
            results.append({
                "market_id": market_id,
                "market_label": MARKET_DEFINITIONS.get(market_id, {}).get("label", market_id),
                "user_id": str(user_id),
                "bet_found": False,
                "voided_now": False,
                "already_voided": False,
                "refunded_total": 0,
                "net_adjustment": 0,
            })
            continue
        result = _void_user_bet_state(
            guild_id,
            market,
            user_id,
            reason=reason,
            voided_by=voided_by,
        )
        if result.get("voided_now"):
            changed = True
            markets[market_id] = market
        results.append(result)

    if changed:
        ref.set({"markets": markets, "updated_at": _now_utc()}, merge=True)
    return results


def void_user_bet(guild_id, match_id, market_id, user_id, reason=None, voided_by=None):
    results = void_user_bets(
        guild_id,
        match_id,
        user_id,
        market_ids=[market_id],
        reason=reason,
        voided_by=voided_by,
    )
    return results[0] if results else None


def void_markets(guild_id, match_id, market_ids, reason=None, voided_by=None):
    results = []
    for market_id in market_ids:
        results.append(void_market(guild_id, match_id, market_id, reason=reason, voided_by=voided_by))
    return results


# ====================================
# Cleanup Functions
# ====================================

def clear_guild_bets(guild_id):
    entries_ref = db.collection("bets").document(str(guild_id)).collection("entries").stream()
    batch = db.batch()
    for entry in entries_ref:
        batch.delete(entry.reference)
    batch.commit()
    print(f"[CLEAR] Deleted all legacy bets for guild {guild_id}")


def clear_all_bets(bot):
    for guild in bot.guilds:
        guild_id = str(guild.id)
        entries = db.collection("bets").document(guild_id).collection("entries").stream()
        for entry in entries:
            db.collection("bets").document(guild_id).collection("entries").document(entry.id).delete()
            print(f"[CLEAR] Deleted legacy entry {entry.id} from guild {guild_id}")
    print("[INIT] Cleared ALL legacy bets from Firestore on startup.")
