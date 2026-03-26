import os

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER_ID           = int(os.environ.get("OWNER_ID", "0"))
SUPABASE_URL       = os.environ.get("SUPABASE_URL", "")
SB_SERVICE_KEY     = os.environ.get("SB_SERVICE_KEY", "")
BAILEYS_URL        = os.environ.get("BAILEYS_URL", "http://localhost:3001")
PORT               = int(os.environ.get("PORT", "8000"))
API_PUBLIC_URL     = os.environ.get("API_PUBLIC_URL", "")
