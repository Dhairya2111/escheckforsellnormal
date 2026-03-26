"""
Database — Supabase as primary store, in-memory cache for fast reads.
"""
import asyncio
import json
from datetime import datetime, timezone
from typing import Optional
from supabase import create_client, Client
import config

if not config.SUPABASE_URL or not config.SB_SERVICE_KEY:
    raise RuntimeError("SUPABASE_URL and SB_SERVICE_KEY must be set!")

sb: Client = create_client(config.SUPABASE_URL, config.SB_SERVICE_KEY)

# ─── In-memory caches ────────────────────────────────────────────────────────
_accounts:  dict = {}   # account_id → account dict
_settings:  dict = {}   # key → value
_api_endpoints: dict = {}  # endpoint_id → endpoint dict


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sb_async(fn):
    """Fire-and-forget Supabase write — works from any thread/context."""
    async def _run():
        for attempt in range(3):
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, fn)
                return
            except Exception as e:
                if attempt == 2:
                    print(f"[DB] Write failed: {e}")
                else:
                    await asyncio.sleep(1)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
    except RuntimeError:
        try:
            fn()
        except Exception as e:
            print(f"[DB] Sync write failed: {e}")


# ─── BOOT ────────────────────────────────────────────────────────────────────
async def init():
    for attempt in range(5):
        try:
            results = await asyncio.gather(
                asyncio.to_thread(lambda: sb.from_("wa_accounts").select("*").order("id").execute()),
                asyncio.to_thread(lambda: sb.from_("bot_settings").select("*").execute()),
                asyncio.to_thread(lambda: sb.from_("api_endpoints").select("*").execute()),
            )
            accounts_r, settings_r, endpoints_r = results

            for a in (accounts_r.data or []):
                _accounts[a["account_id"]] = a
            for s in (settings_r.data or []):
                _settings[s["key"]] = s["value"]
            for e in (endpoints_r.data or []):
                _api_endpoints[e["endpoint_id"]] = e

            print(f"[DB] Loaded: {len(_accounts)} accounts, {len(_settings)} settings, {len(_api_endpoints)} endpoints")
            return
        except Exception as e:
            print(f"[DB] Init attempt {attempt+1} failed: {e}")
            if attempt < 4:
                await asyncio.sleep(3)
    print("[DB] Init failed after 5 attempts")


# ─── SETTINGS ────────────────────────────────────────────────────────────────
def get_setting(key: str) -> Optional[str]:
    return _settings.get(key)


def set_setting(key: str, value: str):
    _settings[key] = value
    _sb_async(lambda: sb.from_("bot_settings").upsert({"key": key, "value": value}, on_conflict="key").execute())


# ─── WA ACCOUNTS ─────────────────────────────────────────────────────────────
def get_all_accounts() -> list:
    return sorted(_accounts.values(), key=lambda a: str(a.get("account_id", "")))


def get_account(account_id: str) -> Optional[dict]:
    return _accounts.get(account_id)


def add_account(account_id: str, label: str):
    if account_id in _accounts:
        return
    acct = {
        "account_id": account_id, "label": label or account_id,
        "is_connected": 0, "total_checks": 0,
        "created_at": _now_iso(),
    }
    _accounts[account_id] = acct
    _sb_async(lambda: sb.from_("wa_accounts").upsert(acct, on_conflict="account_id").execute())


def remove_account(account_id: str):
    _accounts.pop(account_id, None)
    _sb_async(lambda: sb.from_("wa_accounts").delete().eq("account_id", account_id).execute())


def set_account_connected(account_id: str, is_connected: bool, phone_number: str = None):
    a = _accounts.get(account_id)
    if a:
        a["is_connected"] = 1 if is_connected else 0
        if phone_number:
            a["phone_number"] = phone_number
        if is_connected:
            a["last_connected"] = _now_iso()
    upd = {"is_connected": 1 if is_connected else 0}
    if phone_number:
        upd["phone_number"] = phone_number
    if is_connected:
        upd["last_connected"] = _now_iso()
    _sb_async(lambda: sb.from_("wa_accounts").update(upd).eq("account_id", account_id).execute())


def increment_account_checks(account_id: str, count: int = 1):
    a = _accounts.get(account_id)
    if a:
        a["total_checks"] = a.get("total_checks", 0) + count
    _sb_async(lambda: sb.from_("wa_accounts").update(
        {"total_checks": (_accounts.get(account_id) or {}).get("total_checks", count)}
    ).eq("account_id", account_id).execute())


# ─── API ENDPOINTS ────────────────────────────────────────────────────────────
def get_all_endpoints() -> list:
    return sorted(_api_endpoints.values(), key=lambda e: e.get("created_at", ""), reverse=True)


def get_endpoint(endpoint_id: str) -> Optional[dict]:
    return _api_endpoints.get(endpoint_id)


def add_endpoint(endpoint_id: str, label: str, owner_id: int) -> dict:
    ep = {
        "endpoint_id": endpoint_id,
        "label": label,
        "owner_id": owner_id,
        "total_requests": 0,
        "is_active": True,
        "created_at": _now_iso(),
    }
    _api_endpoints[endpoint_id] = ep
    _sb_async(lambda: sb.from_("api_endpoints").upsert(ep, on_conflict="endpoint_id").execute())
    return ep


def remove_endpoint(endpoint_id: str):
    _api_endpoints.pop(endpoint_id, None)
    _sb_async(lambda: sb.from_("api_endpoints").delete().eq("endpoint_id", endpoint_id).execute())


def increment_endpoint_requests(endpoint_id: str, count: int = 1):
    ep = _api_endpoints.get(endpoint_id)
    if ep:
        ep["total_requests"] = ep.get("total_requests", 0) + count
    _sb_async(lambda: sb.from_("api_endpoints").update(
        {"total_requests": (_api_endpoints.get(endpoint_id) or {}).get("total_requests", count)}
    ).eq("endpoint_id", endpoint_id).execute())


def set_endpoint_active(endpoint_id: str, active: bool):
    ep = _api_endpoints.get(endpoint_id)
    if ep:
        ep["is_active"] = active
    _sb_async(lambda: sb.from_("api_endpoints").update({"is_active": active}).eq("endpoint_id", endpoint_id).execute())
