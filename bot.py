"""
Simple WA Checker Bot
Features:
1. Connect unlimited WA accounts (QR / Pairing Code)
2. Check number via bot
3. Create keyless API endpoints per client
"""

import asyncio
import threading
import uvicorn
import api_server
import io
import re
import os
import secrets
import json
from datetime import datetime, timezone

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)
from telegram.constants import ParseMode

import config
import database as db
import whatsapp as wa

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def esc(s) -> str:
    s = str(s) if s is not None else ""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# ─── AUTH ────────────────────────────────────────────────────────────────────
def is_owner(uid: int) -> bool:
    return uid == config.OWNER_ID

# ─── STATES ──────────────────────────────────────────────────────────────────
user_states: dict = {}

# ─── BACK BUTTON ─────────────────────────────────────────────────────────────
BACK_BTN = InlineKeyboardMarkup([[InlineKeyboardButton("‹ Back", callback_data="main_menu")]])

# ─── LOG HELPER ──────────────────────────────────────────────────────────────
async def notify_owner(bot, text: str):
    if config.OWNER_ID:
        try:
            await bot.send_message(config.OWNER_ID, text, parse_mode=ParseMode.HTML)
        except Exception:
            pass

# ─── EDIT MSG HELPER ─────────────────────────────────────────────────────────
async def edit_msg(query, text: str, kb: InlineKeyboardMarkup):
    try:
        await query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True)
    except Exception:
        try:
            await query.message.edit_caption(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            try:
                await query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True)
            except Exception:
                pass

# ─── MAIN MENU ────────────────────────────────────────────────────────────────
def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 WA Accounts", callback_data="wa_accounts"),
         InlineKeyboardButton("➕ Add Account",  callback_data="add_account")],
        [InlineKeyboardButton("🔗 API Endpoints", callback_data="api_panel")],
        [InlineKeyboardButton("📊 Stats",          callback_data="stats")],
    ])

def welcome_text() -> str:
    connected = wa.get_connected_accounts()
    total     = len(db.get_all_accounts())
    return (
        f"🤖 <b>WA Checker Bot — Admin Panel</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 WA Accounts: <b>{total}</b> total | <b>{len(connected)}</b> connected\n\n"
        f"<b>Features:</b>\n"
        f"  • Unlimited WA accounts\n"
        f"  • Single &amp; Bulk number check\n"
        f"  • Keyless API for clients\n\n"
        f"<i>Choose an option:</i>"
    )

# ─── /start ───────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.message.from_user.id):
        await update.message.reply_text("🔒 Access denied.")
        return
    user_states.pop(update.message.from_user.id, None)
    await update.message.reply_text(welcome_text(), parse_mode=ParseMode.HTML, reply_markup=main_menu_kb())

# ─── CALLBACK ROUTER ─────────────────────────────────────────────────────────
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    user_id = query.from_user.id
    data    = query.data

    if not is_owner(user_id):
        await query.answer("🔒 Access denied.", show_alert=True)
        return

    await query.answer()

    # Dynamic callbacks
    if data.startswith("wa_qr_"):
        return await handle_wa_qr(query, data[6:], ctx)
    if data.startswith("wa_pair_"):
        return await handle_wa_pair_prompt(query, user_id, data[8:])
    if data.startswith("wa_dis_"):
        return await handle_wa_disconnect(query, data[7:], ctx)
    if data.startswith("wa_del_"):
        return await handle_wa_delete(query, data[7:], ctx)
    if data.startswith("api_del_"):
        return await handle_api_delete(query, data[8:])
    if data.startswith("api_toggle_"):
        return await handle_api_toggle(query, data[11:])
    if data.startswith("api_detail_"):
        return await handle_api_detail(query, data[11:])

    # Static
    handlers = {
        "main_menu":    lambda: show_main_menu(query),
        "wa_accounts":  lambda: show_wa_accounts(query),
        "add_account":  lambda: start_add_account(query, user_id),
        "api_panel":    lambda: show_api_panel(query),
        "create_api":   lambda: start_create_api(query, user_id),
        "stats":        lambda: show_stats(query),
    }
    handler = handlers.get(data)
    if handler:
        await handler()

# ─── SCREENS ──────────────────────────────────────────────────────────────────
async def show_main_menu(query):
    user_states.pop(query.from_user.id, None)
    await edit_msg(query, welcome_text(), main_menu_kb())

async def show_wa_accounts(query):
    all_accts = db.get_all_accounts()
    icons = {"connected": "🟢", "waiting_for_scan": "⏳", "connecting": "🔄", "banned": "🚫", "disconnected": "🔴"}
    body = f"📱 <b>WA Accounts ({len(all_accts)})</b>\n\n"
    if not all_accts:
        body += "<i>No accounts added yet.</i>"
    kb_rows = []
    for a in all_accts:
        s   = wa.accounts.get(a["account_id"], {})
        st  = s.get("status", "disconnected")
        em  = icons.get(st, "🔴")
        ph  = f"+{a['phone_number']}" if a.get("phone_number") else "Not linked"
        body += f"{em} <b>{esc(a['account_id'])}</b>\n   📞 {ph} | ✓ {a.get('total_checks', 0)} checks\n\n"
        row = []
        if st != "connected":
            row.append(InlineKeyboardButton("📷 QR",    callback_data=f"wa_qr_{a['account_id']}"))
            row.append(InlineKeyboardButton("🔗 Pair",  callback_data=f"wa_pair_{a['account_id']}"))
        else:
            row.append(InlineKeyboardButton("⏹ Disconnect", callback_data=f"wa_dis_{a['account_id']}"))
        row.append(InlineKeyboardButton("🗑 Delete", callback_data=f"wa_del_{a['account_id']}"))
        kb_rows.append(row)
    kb_rows.append([
        InlineKeyboardButton("➕ Add Account", callback_data="add_account"),
        InlineKeyboardButton("🔄 Refresh",     callback_data="wa_accounts"),
    ])
    kb_rows.append([InlineKeyboardButton("‹ Back", callback_data="main_menu")])
    await edit_msg(query, body, InlineKeyboardMarkup(kb_rows))

async def start_add_account(query, user_id: int):
    user_states[user_id] = {"mode": "add_account"}
    await edit_msg(query,
        "➕ <b>Add WA Account</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        "Send a name for this account:\n\n"
        "• Lowercase letters, numbers, underscores only\n"
        "• Examples: <code>acc1</code>, <code>main</code>, <code>client_2</code>",
        BACK_BTN)

async def handle_wa_qr(query, account_id: str, ctx):
    status_msg = await ctx.bot.send_message(
        query.message.chat_id,
        f"⏳ Generating QR for <b>{esc(account_id)}</b>...",
        parse_mode=ParseMode.HTML
    )
    # Check if already connected
    await wa.sync_account_status(account_id)
    if wa.accounts.get(account_id, {}).get("status") == "connected":
        await ctx.bot.edit_message_text(
            f"✅ <b>{esc(account_id)}</b> is already connected!",
            chat_id=query.message.chat_id,
            message_id=status_msg.message_id,
            parse_mode=ParseMode.HTML
        )
        return await show_wa_accounts(query)

    await wa.connect_account(account_id)
    qr_data = await wa.get_qr_code(account_id)

    if qr_data is None:
        if wa.accounts.get(account_id, {}).get("status") == "connected":
            await ctx.bot.edit_message_text(
                f"✅ <b>{esc(account_id)}</b> connected!",
                chat_id=query.message.chat_id,
                message_id=status_msg.message_id,
                parse_mode=ParseMode.HTML
            )
            return await show_wa_accounts(query)
        await ctx.bot.edit_message_text(
            "❌ Failed to generate QR. Please try again.",
            chat_id=query.message.chat_id,
            message_id=status_msg.message_id,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Try Again", callback_data=f"wa_qr_{account_id}")]]),
        )
        return

    import base64
    qr_b64 = qr_data.split(",")[1] if "," in qr_data else qr_data
    buf = io.BytesIO(base64.b64decode(qr_b64))
    buf.name = "qr.png"
    await ctx.bot.delete_message(query.message.chat_id, status_msg.message_id)
    await ctx.bot.send_photo(
        query.message.chat_id, buf,
        caption=(
            f"📱 <b>Scan QR — {esc(account_id)}</b>\n\n"
            f"WhatsApp → Settings → Linked Devices → Link a Device\n\n"
            f"⏳ Expires in ~60 seconds"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("‹ Back to Accounts", callback_data="wa_accounts")]]),
    )

async def handle_wa_pair_prompt(query, user_id: int, account_id: str):
    user_states[user_id] = {"mode": "pair_wa", "account_id": account_id}
    await edit_msg(query,
        f"🔗 <b>Pair via Phone — {esc(account_id)}</b>\n\n"
        f"Send your WhatsApp number with country code:\n\n"
        f"<code>919876543210</code>  (India)\n"
        f"<code>14155550123</code>   (USA)\n\n"
        f"<i>No + or spaces needed.</i>",
        BACK_BTN)

async def handle_wa_disconnect(query, account_id: str, ctx):
    await wa.disconnect_account(account_id)
    await notify_owner(ctx.bot, f"🔌 <b>Disconnected:</b> <code>{esc(account_id)}</code>")
    await show_wa_accounts(query)

async def handle_wa_delete(query, account_id: str, ctx):
    await wa.disconnect_account(account_id)
    wa.accounts.pop(account_id, None)
    db.remove_account(account_id)
    await notify_owner(ctx.bot, f"🗑 <b>Account Deleted:</b> <code>{esc(account_id)}</code>")
    await show_wa_accounts(query)

# ─── API PANEL ────────────────────────────────────────────────────────────────
async def show_api_panel(query):
    endpoints = db.get_all_endpoints()
    base_url  = config.API_PUBLIC_URL or "https://your-domain.com"
    body = f"🔗 <b>API Endpoints ({len(endpoints)})</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    if not endpoints:
        body += "<i>No endpoints created yet.</i>\n\n"
    else:
        for ep in endpoints[:10]:
            icon   = "🟢" if ep.get("is_active") else "🔴"
            reqs   = ep.get("total_requests", 0)
            body += f"{icon} <b>{esc(ep['label'])}</b>\n"
            body += f"   🔗 <code>{base_url}/check/{ep['endpoint_id']}</code>\n"
            body += f"   📊 {reqs} requests\n\n"
        if len(endpoints) > 10:
            body += f"<i>...and {len(endpoints) - 10} more</i>\n"
    kb_rows = [
        [InlineKeyboardButton(f"{'🟢' if ep.get('is_active') else '🔴'} {ep['label'][:20]}", callback_data=f"api_detail_{ep['endpoint_id']}")]
        for ep in endpoints[:8]
    ]
    kb_rows.append([InlineKeyboardButton("➕ Create Endpoint", callback_data="create_api")])
    kb_rows.append([InlineKeyboardButton("‹ Back", callback_data="main_menu")])
    await edit_msg(query, body, InlineKeyboardMarkup(kb_rows))

async def start_create_api(query, user_id: int):
    user_states[user_id] = {"mode": "create_api"}
    await edit_msg(query,
        "🔗 <b>Create API Endpoint</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        "Send a label for this endpoint:\n\n"
        "• Example: <code>John Client</code>, <code>Agency XYZ</code>\n\n"
        "<i>A unique URL will be generated for this client.</i>",
        BACK_BTN)

async def handle_api_detail(query, endpoint_id: str):
    ep = db.get_endpoint(endpoint_id)
    if not ep:
        return await edit_msg(query, "❌ Endpoint not found.", BACK_BTN)
    base_url = config.API_PUBLIC_URL or "https://your-domain.com"
    status   = "🟢 Active" if ep.get("is_active") else "🔴 Disabled"
    single_url = f"{base_url}/check/{endpoint_id}?phone=919876543210"
    bulk_url   = f"{base_url}/check/{endpoint_id}/bulk"
    curl_ex    = f'curl -X POST "{bulk_url}" -H "Content-Type: application/json" -d \'{{"phones":["919876543210","14155550123"]}}\''
    text = (
        f"🔗 <b>{esc(ep['label'])}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Status:</b> {status}\n"
        f"<b>Total Requests:</b> {ep.get('total_requests', 0)}\n"
        f"<b>Created:</b> {ep.get('created_at', 'N/A')[:10]}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>📖 Usage Guide</b>\n\n"
        f"<b>1️⃣ Single Check (GET):</b>\n"
        f"<code>{base_url}/check/{endpoint_id}?phone=NUMBER</code>\n\n"
        f"Response:\n"
        f'<code>{{"success":true,"phone":"919876543210","is_registered":true}}</code>\n\n'
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>2️⃣ Bulk Check (POST):</b>\n"
        f"URL: <code>{bulk_url}</code>\n"
        f'Body: <code>{{"phones":["919876543210","14155550123"]}}</code>\n\n'
        f"cURL:\n<code>{esc(curl_ex)}</code>\n\n"
        f"Response:\n"
        f'<code>{{"success":true,"registered":[...],"not_registered":[...]}}</code>\n\n'
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>⚠️ Keep this URL private — it is your client's access token.</i>"
    )
    toggle_label = "🔴 Disable" if ep.get("is_active") else "🟢 Enable"
    await edit_msg(query, text, InlineKeyboardMarkup([
        [InlineKeyboardButton(toggle_label, callback_data=f"api_toggle_{endpoint_id}"),
         InlineKeyboardButton("🗑 Delete",   callback_data=f"api_del_{endpoint_id}")],
        [InlineKeyboardButton("‹ Back to Endpoints", callback_data="api_panel")],
    ]))

async def handle_api_toggle(query, endpoint_id: str):
    ep = db.get_endpoint(endpoint_id)
    if not ep:
        return
    new_state = not ep.get("is_active", True)
    db.set_endpoint_active(endpoint_id, new_state)
    await handle_api_detail(query, endpoint_id)

async def handle_api_delete(query, endpoint_id: str):
    db.remove_endpoint(endpoint_id)
    await show_api_panel(query)

async def show_stats(query):
    accounts   = db.get_all_accounts()
    connected  = wa.get_connected_accounts()
    endpoints  = db.get_all_endpoints()
    total_reqs = sum(ep.get("total_requests", 0) for ep in endpoints)
    total_chks = sum(a.get("total_checks", 0) for a in accounts)

    lines = [f"📊 <b>Statistics</b>\n━━━━━━━━━━━━━━━━━━━━\n"]
    lines.append(f"<b>📱 WA Accounts</b>")
    lines.append(f"  Total:     <b>{len(accounts)}</b>")
    lines.append(f"  Connected: <b>{len(connected)}</b>")
    lines.append(f"  Checks:    <b>{total_chks:,}</b>")
    lines.append("")
    lines.append(f"<b>🔗 API Endpoints</b>")
    lines.append(f"  Total:    <b>{len(endpoints)}</b>")
    lines.append(f"  Requests: <b>{total_reqs:,}</b>")
    if accounts:
        lines.append("")
        lines.append("<b>📋 Account Details</b>")
        for a in accounts[:10]:
            s  = wa.accounts.get(a["account_id"], {})
            st = s.get("status", "disconnected")
            em = "🟢" if st == "connected" else "🔴"
            ph = f"+{a['phone_number']}" if a.get("phone_number") else "N/A"
            lines.append(f"  {em} <code>{esc(a['account_id'])}</code> {ph} — {a.get('total_checks', 0)} checks")

    await edit_msg(query, "\n".join(lines), InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="stats")],
        [InlineKeyboardButton("‹ Back",     callback_data="main_menu")],
    ]))

# ─── MESSAGE HANDLER ─────────────────────────────────────────────────────────
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg     = update.message
    if not msg or not msg.from_user: return
    user_id = msg.from_user.id
    text    = msg.text or ""

    if not is_owner(user_id): return
    if not text or text.startswith("/"): return

    state = user_states.get(user_id, {})
    mode  = state.get("mode")

    # ── Add account ──
    if mode == "add_account":
        user_states.pop(user_id, None)
        acc_id = re.sub(r"\s+", "_", text.strip().lower())
        if not re.match(r"^[a-z0-9_]+$", acc_id):
            await msg.reply_text("❌ Invalid name. Use only lowercase letters, numbers, underscores.")
            return
        if db.get_account(acc_id):
            await msg.reply_text(f"❌ Account <code>{esc(acc_id)}</code> already exists.", parse_mode=ParseMode.HTML)
            return
        db.add_account(acc_id, acc_id)
        await msg.reply_text(
            f"✅ Account <b>{esc(acc_id)}</b> created!\n\nHow to connect?",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📷 QR Code",       callback_data=f"wa_qr_{acc_id}")],
                [InlineKeyboardButton("🔗 Pairing Code",  callback_data=f"wa_pair_{acc_id}")],
                [InlineKeyboardButton("‹ Back",           callback_data="wa_accounts")],
            ])
        )
        return

    # ── Pairing ──
    if mode == "pair_wa":
        account_id = state["account_id"]
        user_states.pop(user_id, None)
        status_msg = await msg.reply_text(f"⏳ Getting pairing code for <b>{esc(account_id)}</b>...", parse_mode=ParseMode.HTML)
        try:
            code = await wa.get_pairing_code(account_id, text.strip())
            await ctx.bot.edit_message_text(
                f"🔗 <b>Pairing Code — {esc(account_id)}</b>\n\n"
                f"Code: <code>{code}</code>\n\n"
                f"<b>Steps:</b>\n"
                f"1. Open WhatsApp\n"
                f"2. Settings → Linked Devices\n"
                f"3. Link a Device → Link with phone number\n"
                f"4. Enter the code above\n\n"
                f"✅ Bot will notify you when connected.",
                chat_id=msg.chat_id,
                message_id=status_msg.message_id,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("‹ Back to Accounts", callback_data="wa_accounts")]]),
            )
        except Exception as e:
            await ctx.bot.edit_message_text(
                f"❌ <b>Failed:</b> <code>{esc(str(e))}</code>",
                chat_id=msg.chat_id,
                message_id=status_msg.message_id,
                parse_mode=ParseMode.HTML,
                reply_markup=BACK_BTN,
            )
        return

    # ── Create API endpoint ──
    if mode == "create_api":
        user_states.pop(user_id, None)
        label = text.strip()
        if not label or len(label) > 50:
            await msg.reply_text("❌ Invalid label. Max 50 characters.")
            return
        endpoint_id = secrets.token_urlsafe(16)
        ep = db.add_endpoint(endpoint_id, label, user_id)
        base_url = config.API_PUBLIC_URL or "https://your-domain.com"
        single_url = f"{base_url}/check/{endpoint_id}?phone=NUMBER"
        bulk_url   = f"{base_url}/check/{endpoint_id}/bulk"
        curl_ex    = f'curl -X POST "{bulk_url}" -H "Content-Type: application/json" -d \'{{"phones":["919876543210","14155550123"]}}\''
        await msg.reply_text(
            f"✅ <b>API Endpoint Created!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>Client:</b> {esc(label)}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>📖 Usage Guide</b>\n\n"
            f"<b>1️⃣ Single Check (GET):</b>\n"
            f"<code>{single_url}</code>\n\n"
            f"Response:\n"
            f'<code>{{"success":true,"phone":"919876543210","is_registered":true}}</code>\n\n'
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>2️⃣ Bulk Check (POST):</b>\n"
            f"URL: <code>{bulk_url}</code>\n"
            f'Body: <code>{{"phones":["919876543210","14155550123"]}}</code>\n\n'
            f"cURL:\n<code>{esc(curl_ex)}</code>\n\n"
            f"Response:\n"
            f'<code>{{"success":true,"registered":[...],"not_registered":[...],"unknown":[...]}}</code>\n\n'
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>⚠️ This URL is the client's access — keep it private.</i>",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 View All Endpoints", callback_data="api_panel")]]),
        )
        return

    # Default
    await msg.reply_text(welcome_text(), parse_mode=ParseMode.HTML, reply_markup=main_menu_kb())

# ─── ERROR HANDLER ────────────────────────────────────────────────────────────
async def error_handler(update, context):
    import traceback
    err = context.error
    tb  = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    print(f"[ERROR] {err}\n{tb[:300]}")
    try:
        if config.OWNER_ID:
            await context.bot.send_message(
                config.OWNER_ID,
                f"⚠️ <b>Bot Error</b>\n\n<code>{esc(str(err))[:300]}</code>",
                parse_mode=ParseMode.HTML,
            )
    except Exception:
        pass

# ─── WA NOTIFY CALLBACK ───────────────────────────────────────────────────────
_bot_ref = None

async def _wa_notify(account_id: str, phone: str, event: str):
    if not _bot_ref or not config.OWNER_ID:
        return
    icons = {"connected": "✅", "disconnected": "🔴", "banned": "🚫"}
    icon  = icons.get(event, "ℹ️")
    if event == "connected":
        text = f"{icon} <b>Account Connected</b>\n\n📱 <code>{esc(account_id)}</code>\n📞 +<code>{phone}</code>"
    elif event == "banned":
        text = f"{icon} <b>Account BANNED</b>\n\n📱 <code>{esc(account_id)}</code>\n\n⚠️ Please add a new account."
    else:
        text = f"{icon} <b>Account Disconnected</b>\n\n📱 <code>{esc(account_id)}</code>"
    try:
        await _bot_ref.send_message(config.OWNER_ID, text, parse_mode=ParseMode.HTML)
    except Exception:
        pass

# ─── MAIN ────────────────────────────────────────────────────────────────────
async def _async_main():
    global _bot_ref

    # Start FastAPI (API server) in background thread — binds port for Render
    def _run_api():
        uvicorn.run(api_server.app, host="0.0.0.0", port=config.PORT, log_level="warning")
    threading.Thread(target=_run_api, daemon=True).start()
    print(f"✅ API server started on port {config.PORT}")

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    _bot_ref = app.bot

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_error_handler(error_handler)

    async with app:
        _bot_ref = app.bot
        await db.init()
        wa.set_notify_callback(_wa_notify)
        await wa.connect_all_saved()
        asyncio.create_task(wa.poll_all_statuses())
        print("✅ Simple WA Checker Bot started!")
        await app.start()
        await app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
        )
        await asyncio.Event().wait()  # run forever


def main():
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
