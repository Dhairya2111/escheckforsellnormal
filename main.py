from dotenv import load_dotenv
load_dotenv()

import asyncio
import threading
import subprocess
import os
import sys
import uvicorn

import config
import database as db
import whatsapp as wa
import bot as b
import api_server


def run_api_server():
    uvicorn.run(api_server.app, host="0.0.0.0", port=config.PORT, log_level="warning")


def start_baileys_server():
    baileys_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baileys-server")
    if not os.path.isdir(baileys_dir):
        print("[Baileys] ERROR: baileys-server folder not found!")
        return None

    baileys_port = int(os.getenv("BAILEYS_PORT", "3001"))

    print("[Baileys] Running npm install...")
    try:
        r = subprocess.run(
            "npm install", shell=True, cwd=baileys_dir,
            capture_output=True, text=True, timeout=180
        )
        if r.returncode != 0:
            print(f"[Baileys] npm install error: {r.stderr[:500]}")
        else:
            print("[Baileys] npm install OK")
    except Exception as e:
        print(f"[Baileys] npm install exception: {e}")

    env = os.environ.copy()
    env["PORT"] = str(baileys_port)
    env["SUPABASE_URL"] = os.getenv("SUPABASE_URL", "")
    env["SUPABASE_SERVICE_KEY"] = os.getenv("SB_SERVICE_KEY", os.getenv("SUPABASE_SERVICE_KEY", ""))

    print(f"[Baileys] Starting on port {baileys_port}...")
    try:
        proc = subprocess.Popen(
            "node index.js", shell=True, cwd=baileys_dir,
            stdout=sys.stdout, stderr=sys.stderr, env=env,
        )
        print(f"[Baileys] PID={proc.pid}")
        return proc
    except Exception as e:
        print(f"[Baileys] Failed to start: {e}")
        return None


async def main():
    baileys_proc = start_baileys_server()
    if baileys_proc:
        print("[Boot] Waiting for Baileys...")
        ready = await wa.wait_for_baileys(max_wait=60)
        print(f"[Boot] Baileys ready={ready}")
    else:
        print("[Boot] Baileys not started — WA disabled")

    # Start API server thread
    threading.Thread(target=run_api_server, daemon=True).start()
    print(f"✅ API server on port {config.PORT}")

    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", b.cmd_start))
    application.add_handler(CallbackQueryHandler(b.on_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, b.on_message))
    application.add_error_handler(b.error_handler)

    async with application:
        await db.init()
        wa.set_notify_callback(b._wa_notify)
        b._bot_ref = application.bot
        await wa.connect_all_saved()
        asyncio.create_task(wa.poll_all_statuses())

        print("✅ Bot started!")
        await application.start()
        await asyncio.sleep(3)
        await application.bot.delete_webhook(drop_pending_updates=True)
        await asyncio.sleep(2)
        await application.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
        )
        try:
            await asyncio.Event().wait()
        finally:
            if baileys_proc:
                baileys_proc.terminate()


if __name__ == "__main__":
    asyncio.run(main())
