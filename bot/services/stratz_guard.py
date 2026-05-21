import asyncio
import os
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import bot.storage.firebase_setup  # ensures Firebase is initialized before anything else
from firebase_admin import firestore

db = firestore.client()

STRATZ_RATE_LIMIT_PER_MINUTE = int(os.getenv("STRATZ_RATE_LIMIT_PER_MINUTE", "150"))
STRATZ_CIRCUIT_COOLDOWN_SECONDS = int(os.getenv("STRATZ_CIRCUIT_COOLDOWN_SECONDS", "1800"))
MMR_REFRESH_RECENT_WINDOW_SECONDS = int(os.getenv("MMR_REFRESH_RECENT_WINDOW_SECONDS", "14400"))

_rate_lock = threading.Lock()
_request_times = deque()
_state_lock = threading.Lock()
_local_blocked_until = None
_local_block_reason = None
_local_status_cache_until = 0.0
_STATUS_CACHE_SECONDS = 15


def _now_utc():
    return datetime.now(timezone.utc)


def _to_utc_datetime(value):
    if hasattr(value, "to_datetime"):
        value = value.to_datetime()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


def _stratz_ref():
    return db.collection("bot_runtime").document("stratz_guard")


def _mmr_refresh_ref():
    return db.collection("bot_runtime").document("mmr_refresh")


def _cache_local_block(blocked_until, reason):
    global _local_blocked_until, _local_block_reason, _local_status_cache_until
    with _state_lock:
        _local_blocked_until = blocked_until
        _local_block_reason = reason
        _local_status_cache_until = time.monotonic() + _STATUS_CACHE_SECONDS


def get_stratz_block_state():
    now = _now_utc()
    monotonic_now = time.monotonic()
    with _state_lock:
        if _local_blocked_until and now < _local_blocked_until:
            return True, _local_block_reason, _local_blocked_until
        if monotonic_now < _local_status_cache_until:
            return False, None, None

    try:
        snap = _stratz_ref().get()
        data = snap.to_dict() if snap.exists else {}
        blocked_until = _to_utc_datetime((data or {}).get("blocked_until"))
        reason = (data or {}).get("reason")
        if blocked_until and now < blocked_until:
            _cache_local_block(blocked_until, reason)
            return True, reason, blocked_until
    except Exception as e:
        print(f"[stratz_guard] Failed to read STRATZ circuit state: {e}")

    _cache_local_block(None, None)
    return False, None, None


def _format_until(blocked_until):
    if not blocked_until:
        return "unknown"
    return blocked_until.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def format_stratz_block(reason, blocked_until):
    return f"{reason or 'STRATZ cooldown'} until {_format_until(blocked_until)}"


def _reserve_rate_slot():
    limit = max(1, int(STRATZ_RATE_LIMIT_PER_MINUTE or 1))
    while True:
        with _rate_lock:
            now = time.monotonic()
            while _request_times and now - _request_times[0] >= 60:
                _request_times.popleft()
            if len(_request_times) < limit:
                _request_times.append(now)
                return 0.0
            wait_seconds = max(0.05, 60 - (now - _request_times[0]))
        return wait_seconds


async def reserve_stratz_request():
    blocked, reason, blocked_until = get_stratz_block_state()
    if blocked:
        return False, reason, blocked_until
    while True:
        wait_seconds = _reserve_rate_slot()
        if wait_seconds <= 0:
            break
        await asyncio.sleep(wait_seconds)
    return get_stratz_block_state()


def reserve_stratz_request_sync():
    blocked, reason, blocked_until = get_stratz_block_state()
    if blocked:
        return False, reason, blocked_until
    while True:
        wait_seconds = _reserve_rate_slot()
        if wait_seconds <= 0:
            break
        time.sleep(wait_seconds)
    return get_stratz_block_state()


def _retry_after_seconds(headers):
    if not headers:
        return None
    raw_value = None
    try:
        raw_value = headers.get("Retry-After")
    except Exception:
        raw_value = None
    if not raw_value:
        return None
    try:
        return max(0, int(float(raw_value)))
    except (TypeError, ValueError):
        pass
    try:
        retry_at = parsedate_to_datetime(str(raw_value))
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0, int((retry_at.astimezone(timezone.utc) - _now_utc()).total_seconds()))
    except Exception:
        return None


def trip_stratz_circuit(reason, *, status=None, cooldown_seconds=None, endpoint=None):
    cooldown = int(cooldown_seconds or STRATZ_CIRCUIT_COOLDOWN_SECONDS)
    blocked_until = _now_utc() + timedelta(seconds=max(1, cooldown))
    _cache_local_block(blocked_until, reason)
    payload = {
        "blocked_until": blocked_until,
        "reason": reason,
        "status": status,
        "endpoint": endpoint,
        "updated_at": firestore.SERVER_TIMESTAMP,
    }
    try:
        _stratz_ref().set(payload, merge=True)
    except Exception as e:
        print(f"[stratz_guard] Failed to persist STRATZ circuit state: {e}")
    print(f"[stratz_guard] STRATZ disabled: {format_stratz_block(reason, blocked_until)}")
    return blocked_until


def note_stratz_response(status, body="", headers=None, endpoint=None):
    body_text = str(body or "")
    if int(status or 0) == 403:
        reason = "STRATZ 403 different IP lockout"
        if "different ip" not in body_text.lower():
            reason = "STRATZ 403 forbidden"
        trip_stratz_circuit(reason, status=status, endpoint=endpoint)
        return True
    if int(status or 0) == 429:
        retry_after = _retry_after_seconds(headers)
        cooldown = max(STRATZ_CIRCUIT_COOLDOWN_SECONDS, retry_after or 0)
        trip_stratz_circuit("STRATZ 429 rate limit", status=status, cooldown_seconds=cooldown, endpoint=endpoint)
        return True
    return False


def get_last_mmr_refresh_at():
    try:
        snap = _mmr_refresh_ref().get()
        data = snap.to_dict() if snap.exists else {}
        return _to_utc_datetime((data or {}).get("last_mmr_refresh_at"))
    except Exception as e:
        print(f"[stratz_guard] Failed to read MMR refresh state: {e}")
        return None


def should_skip_recent_mmr_refresh(window_seconds=MMR_REFRESH_RECENT_WINDOW_SECONDS):
    last_refresh_at = get_last_mmr_refresh_at()
    if not last_refresh_at:
        return False, None
    return (_now_utc() - last_refresh_at).total_seconds() < int(window_seconds), last_refresh_at


def mark_mmr_refresh_started():
    now = _now_utc()
    try:
        _mmr_refresh_ref().set(
            {
                "last_mmr_refresh_at": now,
                "last_mmr_refresh_started_at": now,
                "status": "running",
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
    except Exception as e:
        print(f"[stratz_guard] Failed to mark MMR refresh start: {e}")
    return now


def mark_mmr_refresh_completed(*, refreshed_players=0, updated_players=0, stopped_reason=None):
    payload = {
        "last_mmr_refresh_completed_at": _now_utc(),
        "refreshed_players": int(refreshed_players or 0),
        "updated_players": int(updated_players or 0),
        "status": "stopped" if stopped_reason else "complete",
        "stopped_reason": stopped_reason,
        "updated_at": firestore.SERVER_TIMESTAMP,
    }
    try:
        _mmr_refresh_ref().set(payload, merge=True)
    except Exception as e:
        print(f"[stratz_guard] Failed to mark MMR refresh completion: {e}")
