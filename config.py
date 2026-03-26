import os

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER_ID           = int(os.environ.get("OWNER_ID", "0"))
SUPABASE_URL       = os.environ.get("SUPABASE_URL", "")
SB_SERVICE_KEY     = os.environ.get("SB_SERVICE_KEY", "")
BAILEYS_URL        = os.environ.get("BAILEYS_URL", "http://localhost:3001")
PORT               = int(os.environ.get("PORT", "8000"))

# Auto-detect public URL (priority order):
# 1. Manual override: API_PUBLIC_URL env var
# 2. Render auto-sets: RENDER_EXTERNAL_URL
# 3. Fallback: localhost
_manual = os.environ.get("API_PUBLIC_URL", "").rstrip("/")
_render = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
API_PUBLIC_URL = _manual or _render or f"http://localhost:{PORT}"
