"""
API Server — Keyless URL-based endpoints.
Each client gets a unique URL: /check/{endpoint_id}?phone=...
No API key needed — the endpoint_id IS the access token.
"""
import asyncio
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import database as db
import whatsapp as wa

app = FastAPI(title="WA Checker API", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

class BulkRequest(BaseModel):
    phones: List[str]

# ─── Health ───────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "accounts": len(wa.get_connected_accounts())}

# ─── Single check ─────────────────────────────────────────────────────────────
@app.get("/check/{endpoint_id}")
async def check_single(endpoint_id: str, phone: str = Query(...)):
    ep = db.get_endpoint(endpoint_id)
    if not ep or not ep.get("is_active"):
        raise HTTPException(status_code=404, detail="Endpoint not found or disabled")

    phone = phone.replace("+", "").replace(" ", "").replace("-", "")
    if not phone.isdigit() or not (7 <= len(phone) <= 15):
        return JSONResponse({"success": False, "error": "Invalid phone number format"}, status_code=400)

    result = await wa.check_number_any(phone)
    db.increment_endpoint_requests(endpoint_id)

    return {
        "success": result.get("is_registered") is not None,
        "phone":   phone,
        "is_registered": result.get("is_registered"),
        "error":   result.get("error"),
    }

# ─── Bulk check ───────────────────────────────────────────────────────────────
@app.post("/check/{endpoint_id}/bulk")
async def check_bulk(endpoint_id: str, body: BulkRequest):
    ep = db.get_endpoint(endpoint_id)
    if not ep or not ep.get("is_active"):
        raise HTTPException(status_code=404, detail="Endpoint not found or disabled")

    phones = [p.replace("+", "").replace(" ", "").replace("-", "") for p in body.phones]
    phones = [p for p in phones if p.isdigit() and 7 <= len(p) <= 15]

    if not phones:
        return JSONResponse({"success": False, "error": "No valid phone numbers"}, status_code=400)
    if len(phones) > 1000:
        return JSONResponse({"success": False, "error": "Max 1000 numbers per request"}, status_code=400)

    results = await wa.bulk_check(phones)
    db.increment_endpoint_requests(endpoint_id, len(phones))

    registered     = [r["phone"] for r in results if r.get("is_registered") is True]
    not_registered = [r["phone"] for r in results if r.get("is_registered") is False]
    unknown        = [r["phone"] for r in results if r.get("is_registered") is None]

    return {
        "success":        True,
        "total":          len(phones),
        "registered":     registered,
        "not_registered": not_registered,
        "unknown":        unknown,
    }
