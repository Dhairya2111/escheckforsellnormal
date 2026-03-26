"""
WhatsApp multi-account engine.
Baileys HTTP bridge — supports unlimited accounts.
"""
import asyncio
import aiohttp
from typing import Optional, Callable
import database as db
import config

BAILEYS_URL = config.BAILEYS_URL
MAX_RETRY   = 5

# In-memory per-account state
# account_id → { status, qr_code, phone_number, was_connected, retry_count }
accounts: dict = {}

# ─── HTTP helpers ─────────────────────────────────────────────────────────────
async def _get(path: str) -> Optional[dict]:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{BAILEYS_URL}{path}", timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 200:
                    return await r.json()
    except Exception as e:
        print(f"[WA GET {path}] {e}")
    return None

async def _post(path: str, data: dict = None) -> Optional[dict]:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{BAILEYS_URL}{path}", json=data or {}, timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 200:
                    return await r.json()
    except Exception as e:
        print(f"[WA POST {path}] {e}")
    return None

# ─── Wait for baileys to boot ─────────────────────────────────────────────────
async def wait_for_baileys(max_wait: int = 60) -> bool:
    for _ in range(max_wait):
        r = await _get("/health")
        if r:
            print("[Baileys] Ready!")
            return True
        await asyncio.sleep(1)
    return False

# ─── Account state helpers ────────────────────────────────────────────────────
def _get_state(account_id: str) -> dict:
    return accounts.setdefault(account_id, {
        "status": "disconnected", "qr_code": None, "phone_number": None,
        "was_connected": False, "retry_count": 0,
    })

def get_connected_accounts() -> list:
    return [
        {"id": aid, **state}
        for aid, state in accounts.items()
        if state.get("status") == "connected"
    ]

def has_any_connected() -> bool:
    return len(get_connected_accounts()) > 0

# ─── Connect account ──────────────────────────────────────────────────────────
async def connect_account(account_id: str, force: bool = False):
    state = _get_state(account_id)
    if not force and state.get("status") in ("connected", "connecting"):
        return
    db.add_account(account_id, account_id)
    state["status"] = "connecting"
    state["qr_code"] = None
    for attempt in range(5):
        result = await _post(f"/accounts/{account_id}/connect", {})
        if result:
            print(f"[WA] Connect requested: {account_id}")
            return
        await asyncio.sleep(3)
    state["status"] = "disconnected"

# ─── QR code ─────────────────────────────────────────────────────────────────
async def get_qr_code(account_id: str) -> Optional[str]:
    """Poll for QR — up to 30s."""
    for _ in range(30):
        r = await _get(f"/accounts/{account_id}/qr")
        if not r:
            await asyncio.sleep(1)
            continue
        if r.get("status") == "already_connected":
            accounts[account_id]["status"] = "connected"
            return None
        if r.get("qr"):
            accounts[account_id]["qr_code"] = r["qr"]
            accounts[account_id]["status"] = "waiting_for_scan"
            return r["qr"]
        await asyncio.sleep(1)
    return None

# ─── Pairing code ────────────────────────────────────────────────────────────
async def get_pairing_code(account_id: str, phone: str) -> str:
    db.add_account(account_id, account_id)
    _get_state(account_id).update({"status": "connecting"})
    for _ in range(5):
        result = await _post(f"/accounts/{account_id}/pair", {"phone": phone})
        if result and result.get("code"):
            return result["code"]
        await asyncio.sleep(3)
    raise RuntimeError("Failed to get pairing code from baileys-server")

# ─── Disconnect ───────────────────────────────────────────────────────────────
async def disconnect_account(account_id: str):
    await _post(f"/accounts/{account_id}/disconnect")
    state = _get_state(account_id)
    state["status"] = "disconnected"
    state["qr_code"] = None
    db.set_account_connected(account_id, False)

# ─── Sync status ─────────────────────────────────────────────────────────────
async def sync_account_status(account_id: str):
    r = await _get(f"/accounts/{account_id}/status")
    if not r:
        return
    status = r.get("status", "disconnected")
    phone  = r.get("phoneNumber")
    state  = _get_state(account_id)
    state["status"] = status
    state["phone_number"] = phone
    if status == "connected":
        db.set_account_connected(account_id, True, phone)
        state["was_connected"] = True
    elif status == "disconnected":
        db.set_account_connected(account_id, False)

# ─── Poll all statuses (background task) ─────────────────────────────────────
_notify_fn: Optional[Callable] = None

def set_notify_callback(fn):
    global _notify_fn
    _notify_fn = fn

async def poll_all_statuses():
    """Background task: poll all account statuses every 10s."""
    while True:
        try:
            result = await _get("/accounts")
            if result and isinstance(result.get("accounts"), list):
                for acct in result["accounts"]:
                    aid = acct.get("id")
                    if not aid:
                        continue
                    state = _get_state(aid)
                    prev  = state.get("status")
                    new   = acct.get("status", "disconnected")
                    state["status"]       = new
                    state["phone_number"] = acct.get("phoneNumber")
                    if acct.get("qrCode"):
                        state["qr_code"] = acct["qrCode"]

                    if new == "connected":
                        db.set_account_connected(aid, True, state["phone_number"])
                        state["qr_code"] = None
                        state["retry_count"] = 0
                        if prev != "connected":
                            state["was_connected"] = True
                            phone = state.get("phone_number") or "Unknown"
                            print(f"[WA] {aid} Connected: +{phone}")
                            if _notify_fn:
                                try:
                                    await _notify_fn(aid, phone, "connected")
                                except Exception:
                                    pass

                    elif new in ("disconnected", "banned") and prev == "connected":
                        db.set_account_connected(aid, False)
                        if _notify_fn:
                            try:
                                await _notify_fn(aid, None, new)
                            except Exception:
                                pass
                        # Schedule retry
                        if new != "banned" and state.get("retry_count", 0) < MAX_RETRY:
                            state["retry_count"] = state.get("retry_count", 0) + 1
                            backoff = min(300, 30 * state["retry_count"])
                            async def _retry(a=aid):
                                await asyncio.sleep(backoff)
                                await connect_account(a)
                            asyncio.ensure_future(_retry())
        except Exception as e:
            print(f"[WA Poll] {e}")
        await asyncio.sleep(10)

# ─── Number check ─────────────────────────────────────────────────────────────
async def check_number(account_id: str, phone: str) -> dict:
    result = await _post(f"/accounts/{account_id}/check", {"phone": phone})
    if result is None:
        return {"phone": phone, "is_registered": None, "error": "request_failed"}
    db.increment_account_checks(account_id)
    return {
        "phone":         phone,
        "is_registered": result.get("isRegistered"),
        "error":         result.get("error"),
    }

async def check_number_any(phone: str) -> dict:
    """Check using any connected account (round-robin)."""
    connected = get_connected_accounts()
    if not connected:
        return {"phone": phone, "is_registered": None, "error": "no_accounts_connected"}
    # Pick account with least checks
    best = min(connected, key=lambda a: db.get_account(a["id"]) and db.get_account(a["id"]).get("total_checks", 0) or 0)
    return await check_number(best["id"], phone)

async def bulk_check(numbers: list, on_progress=None) -> list:
    connected = get_connected_accounts()
    if not connected:
        return [{"phone": n, "is_registered": None, "error": "no_accounts"} for n in numbers]

    if len(connected) == 1:
        cid = connected[0]["id"]
        result = await _post(f"/accounts/{cid}/check-batch", {"phones": numbers})
        if result and result.get("results"):
            db.increment_account_checks(cid, len(numbers))
            if on_progress: on_progress(len(numbers), len(numbers))
            return [
                {"phone": r.get("phone_number", numbers[i]), "is_registered": r.get("isRegistered"), "error": r.get("error")}
                for i, r in enumerate(result["results"])
            ]

    # Multi-account: distribute load
    results = [None] * len(numbers)
    done = [0]
    chunks = {c["id"]: [] for c in connected}
    for i, num in enumerate(numbers):
        chunks[connected[i % len(connected)]["id"]].append((i, num))

    async def process(cid, items):
        for idx, num in items:
            results[idx] = await check_number(cid, num)
            done[0] += 1
            if on_progress: on_progress(done[0], len(numbers))
            await asyncio.sleep(0.05)

    await asyncio.gather(*[process(cid, items) for cid, items in chunks.items()])
    return results

# ─── Boot: restore all saved accounts ────────────────────────────────────────
async def connect_all_saved():
    saved = db.get_all_accounts()
    if not saved:
        print("[WA] No saved accounts to restore.")
        return
    print(f"[Boot] Restoring {len(saved)} WA account(s)...")
    for a in saved:
        aid = a["account_id"]
        _get_state(aid)
        await connect_account(aid)
        await asyncio.sleep(0.5)
