import asyncio
import json
import logging
import os
from datetime import datetime
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, MessageHandler, CommandHandler, CallbackQueryHandler,
    filters, ContextTypes
)
import polars as pl
from rapidfuzz import fuzz

logging.basicConfig(level=logging.INFO)

TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 10000))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://mst-price-bot-v2.onrender.com")

ADMIN_IDS = set()
for _id in os.environ.get("ADMIN_IDS", "").split(","):
    _id = _id.strip()
    if _id.isdigit():
        ADMIN_IDS.add(int(_id))

USERS_FILE = "users.json"
MAX_LOG_ENTRIES = 500

def load_users():
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r") as f:
                data = json.load(f)
                data.setdefault("approved", [])
                data.setdefault("blocked", [])
                data.setdefault("pending", {})
                data.setdefault("logs", [])
                data.setdefault("profiles", {})
                data.setdefault("search_count", 0)
                return data
        except:
            pass
    return {"approved": [], "blocked": [], "pending": {}, "logs": [], "profiles": {}, "search_count": 0}

def save_users(data):
    with open(USERS_FILE, "w") as f:
        json.dump(data, f)

users_data = load_users()

def is_approved(uid):
    return uid in ADMIN_IDS or uid in users_data["approved"]

def is_blocked(uid):
    return uid in users_data["blocked"]

def is_admin(uid):
    return uid in ADMIN_IDS

def touch_profile(user):
    users_data["profiles"][str(user.id)] = {
        "name": user.full_name,
        "username": user.username or "N/A",
        "last_seen": datetime.now().strftime("%d-%b %H:%M"),
    }

def add_log(uid, name, query):
    users_data["logs"].append({
        "uid": uid,
        "name": name,
        "query": query,
        "time": datetime.now().strftime("%d-%b %H:%M"),
    })
    if len(users_data["logs"]) > MAX_LOG_ENTRIES:
        users_data["logs"] = users_data["logs"][-MAX_LOG_ENTRIES:]

# ---------------- CSV LOAD ----------------

df = pl.read_csv("price.csv", infer_schema_length=0)
all_data = []
for row in df.iter_rows(named=True):
    try:
        code = str(row.get('CODE', '')).strip()
        desc = str(row.get('DESCRIPTION', '')).strip()
        if len(code) > 2 and len(desc) > 2 and code != 'CODE':
            all_data.append(row)
    except:
        pass

print(f"Loaded {len(all_data)} products")
print("Bot Online Hai")

def search_products(text):
    text = text.strip().lower()
    exact, ends_with, starts_with, contains_code, contains_desc, fuzzy = [], [], [], [], [], []

    for row in all_data:
        try:
            code = str(row.get('CODE', '')).strip().lower()
            desc = str(row.get('DESCRIPTION', '')).strip().lower()

            if text == code:
                exact.append(row)
            elif code.endswith(text):
                ends_with.append(row)
            elif code.startswith(text):
                starts_with.append(row)
            elif text in code:
                idx = code.find(text)
                after = code[idx+len(text):]
                if after == '' or after.startswith('-'):
                    ends_with.append(row)
                else:
                    contains_code.append(row)
            elif text in desc:
                contains_desc.append(row)
            else:
                score = fuzz.partial_ratio(text, code)
                if score > 88:
                    fuzzy.append((score, row))
        except:
            pass

    fuzzy_sorted = [r for _, r in sorted(fuzzy, key=lambda x: -x[0])]
    final = exact + ends_with + starts_with + contains_code + contains_desc + fuzzy_sorted

    seen = set()
    unique = []
    for r in final:
        c = str(r.get('CODE', ''))
        if c not in seen:
            seen.add(c)
            unique.append(r)
    return unique

def val(row, key):
    v = str(row.get(key, '')).strip()
    return v if v and v not in ['None', 'nan', ''] else None

def format_product(row):
    code = val(row, 'CODE') or ''
    desc = val(row, 'DESCRIPTION') or ''
    source = val(row, 'SOURCE') or ''

    msg = f"📦 Code: {code}\n"
    msg += f"📝 {desc}\n\n"

    nrp_new = val(row, 'NRP_JULY2026')
    mrp_new = val(row, 'MRP_JULY2026')
    sdp = val(row, 'SDP')
    ewp = val(row, 'EWP')
    mdp = val(row, 'MDP')

    if source == 'LIGHTING':
        if ewp: msg += f"💡 EWP: Rs.{ewp}\n"
        if mdp: msg += f"💡 MDP: Rs.{mdp}\n"
        if sdp: msg += f"💰 SDP: Rs.{sdp}\n"
        if nrp_new: msg += f"💰 NRP: Rs.{nrp_new}\n"
        if mrp_new: msg += f"💰 MRP: Rs.{mrp_new}\n"
        msg += f"🔆 Category: Lighting\n"
    else:
        if sdp: msg += f"💰 SDP: Rs.{sdp}\n"
        if nrp_new: msg += f"💰 NRP: Rs.{nrp_new}\n"
        if mrp_new: msg += f"💰 MRP: Rs.{mrp_new}\n"
        msg += f"🚿 Category: Fittings\n"

    nrp_jan = val(row, 'NRP_JAN2026')
    mrp_jan = val(row, 'MRP_JAN2026')
    nrp_2025 = val(row, 'NRP_2025')
    mrp_2025 = val(row, 'MRP_2025')

    if nrp_jan or nrp_2025:
        msg += "\n📜 Price History:\n"
        if nrp_jan: msg += f"   Jan 2026: NRP Rs.{nrp_jan} | MRP Rs.{mrp_jan}\n"
        if nrp_2025: msg += f"   2025: NRP Rs.{nrp_2025} | MRP Rs.{mrp_2025}\n"

    msg += "-----------------------------\n\n"
    return msg

# ---------------- USER SIDE ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    touch_profile(user)
    save_users(users_data)

    if is_blocked(uid):
        await update.message.reply_text("🚫 Tumhe block kiya gaya hai. Admin se contact karo.")
        return

    if is_approved(uid):
        await update.message.reply_text(
            "✅ Welcome! Product code ya naam bhejo price dekhne ke liye.\nExample: ALD-CHR-079N"
        )
        return

    if str(uid) in users_data["pending"]:
        await update.message.reply_text("⏳ Tumhari request pending hai, admin approve karega.")
        return

    users_data["pending"][str(uid)] = {
        "name": user.full_name,
        "username": user.username or "N/A",
    }
    save_users(users_data)

    await update.message.reply_text("⏳ Request bheji gayi hai, admin approve karega tabhi bot use kar paoge.")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"approve_{uid}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"reject_{uid}"),
    ]])
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                admin_id,
                f"🔔 Naya access request!\n"
                f"Naam: {user.full_name}\n"
                f"Username: @{user.username or 'N/A'}\n"
                f"ID: {uid}",
                reply_markup=kb,
            )
        except:
            pass

async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    touch_profile(user)

    if is_blocked(uid):
        await update.message.reply_text("🚫 Tumhe block kiya gaya hai. Admin se contact karo.")
        return

    if not is_approved(uid):
        await update.message.reply_text("❌ Ye bot private hai. Access ke liye /start bhejo, admin approve karega.")
        return

    text = update.message.text.strip()
    results = search_products(text)

    users_data["search_count"] += 1
    add_log(uid, user.full_name, text)
    save_users(users_data)

    if results:
        total = len(results)
        show = results[:5]
        msg = f"🔍 {total} product(s) mila\n"
        if total > 5:
            msg += f"_(Top 5 dikh rahe hain)_\n"
        msg += "\n"
        for row in show:
            msg += format_product(row)
        if total > 5:
            msg += f"💡 Aur {total-5} products hain — zyada specific code likho!"
    else:
        msg = "❌ Product nahi mila!\n\nKripya sahi code ya naam likho.\nExample: ALD-CHR-079N"

    await update.message.reply_text(msg)

# ---------------- ADMIN PANEL ----------------

def name_for(uid):
    p = users_data["profiles"].get(str(uid))
    if p:
        return f"{p['name']} (@{p['username']})"
    return f"ID {uid}"

def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"⏳ Pending ({len(users_data['pending'])})", callback_data="menu_pending")],
        [InlineKeyboardButton(f"✅ Approved ({len(users_data['approved'])})", callback_data="menu_approved")],
        [InlineKeyboardButton(f"🚫 Blocked ({len(users_data['blocked'])})", callback_data="menu_blocked")],
        [InlineKeyboardButton("📜 Activity Log", callback_data="menu_logs")],
        [InlineKeyboardButton("📊 Stats", callback_data="menu_stats")],
    ])

async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Ye command sirf admin use kar sakta hai.")
        return
    await update.message.reply_text("🛠️ Admin Panel", reply_markup=main_menu_kb())

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid_caller = query.from_user.id
    data = query.data

    # Approve/Reject from request notification - allowed for any admin
    if data.startswith("approve_") or data.startswith("reject_"):
        if not is_admin(uid_caller):
            await query.answer("Sirf admin ke liye.", show_alert=True)
            return
        target = int(data.split("_")[1])
        if data.startswith("approve_"):
            if target not in users_data["approved"]:
                users_data["approved"].append(target)
            users_data["pending"].pop(str(target), None)
            save_users(users_data)
            await query.edit_message_text(f"✅ Approved: {name_for(target)}")
            try:
                await context.bot.send_message(target, "✅ Tumhara access approve ho gaya! Ab product code bhej ke price dekh sakte ho.")
            except:
                pass
        else:
            users_data["pending"].pop(str(target), None)
            save_users(users_data)
            await query.edit_message_text(f"❌ Rejected: {name_for(target)}")
            try:
                await context.bot.send_message(target, "❌ Tumhari access request reject ho gayi.")
            except:
                pass
        await query.answer()
        return

    if not is_admin(uid_caller):
        await query.answer("Sirf admin ke liye.", show_alert=True)
        return

    if data == "menu_main":
        await query.edit_message_text("🛠️ Admin Panel", reply_markup=main_menu_kb())

    elif data == "menu_pending":
        if not users_data["pending"]:
            rows = [[InlineKeyboardButton("⬅️ Back", callback_data="menu_main")]]
            await query.edit_message_text("Koi pending request nahi hai.", reply_markup=InlineKeyboardMarkup(rows))
        else:
            rows = []
            for pid, info in list(users_data["pending"].items())[:15]:
                rows.append([InlineKeyboardButton(f"{info['name']} ({pid})", callback_data="noop")])
                rows.append([
                    InlineKeyboardButton("✅ Approve", callback_data=f"approve_{pid}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"reject_{pid}"),
                ])
            rows.append([InlineKeyboardButton("⬅️ Back", callback_data="menu_main")])
            await query.edit_message_text("⏳ Pending Requests:", reply_markup=InlineKeyboardMarkup(rows))

    elif data == "menu_approved":
        if not users_data["approved"]:
            rows = [[InlineKeyboardButton("⬅️ Back", callback_data="menu_main")]]
            await query.edit_message_text("Koi approved user nahi hai.", reply_markup=InlineKeyboardMarkup(rows))
        else:
            rows = []
            for aid in users_data["approved"][:15]:
                rows.append([InlineKeyboardButton(name_for(aid), callback_data="noop")])
                rows.append([
                    InlineKeyboardButton("🚫 Block", callback_data=f"block_{aid}"),
                    InlineKeyboardButton("🗑️ Remove", callback_data=f"unapprove_{aid}"),
                ])
            rows.append([InlineKeyboardButton("⬅️ Back", callback_data="menu_main")])
            await query.edit_message_text("✅ Approved Users:", reply_markup=InlineKeyboardMarkup(rows))

    elif data == "menu_blocked":
        if not users_data["blocked"]:
            rows = [[InlineKeyboardButton("⬅️ Back", callback_data="menu_main")]]
            await query.edit_message_text("Koi blocked user nahi hai.", reply_markup=InlineKeyboardMarkup(rows))
        else:
            rows = []
            for bid in users_data["blocked"][:15]:
                rows.append([InlineKeyboardButton(name_for(bid), callback_data="noop")])
                rows.append([InlineKeyboardButton("✅ Unblock", callback_data=f"unblock_{bid}")])
            rows.append([InlineKeyboardButton("⬅️ Back", callback_data="menu_main")])
            await query.edit_message_text("🚫 Blocked Users:", reply_markup=InlineKeyboardMarkup(rows))

    elif data == "menu_logs":
        recent = users_data["logs"][-15:][::-1]
        if not recent:
            text = "Koi activity nahi hai abhi tak."
        else:
            text = "📜 Recent Activity:\n\n"
            for l in recent:
                text += f"👤 {l['name']} → \"{l['query']}\" ({l['time']})\n"
        rows = [[InlineKeyboardButton("⬅️ Back", callback_data="menu_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))

    elif data == "menu_stats":
        text = (
            f"📊 Bot Stats\n\n"
            f"✅ Approved: {len(users_data['approved'])}\n"
            f"⏳ Pending: {len(users_data['pending'])}\n"
            f"🚫 Blocked: {len(users_data['blocked'])}\n"
            f"🔍 Total searches: {users_data['search_count']}\n"
            f"📦 Products loaded: {len(all_data)}"
        )
        rows = [[InlineKeyboardButton("⬅️ Back", callback_data="menu_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))

    elif data.startswith("block_"):
        target = int(data.split("_")[1])
        if target in users_data["approved"]:
            users_data["approved"].remove(target)
        if target not in users_data["blocked"]:
            users_data["blocked"].append(target)
        save_users(users_data)
        await query.answer("Blocked!")
        try:
            await context.bot.send_message(target, "🚫 Tumhe block kar diya gaya hai.")
        except:
            pass
        # refresh approved menu
        rows = []
        for aid in users_data["approved"][:15]:
            rows.append([InlineKeyboardButton(name_for(aid), callback_data="noop")])
            rows.append([
                InlineKeyboardButton("🚫 Block", callback_data=f"block_{aid}"),
                InlineKeyboardButton("🗑️ Remove", callback_data=f"unapprove_{aid}"),
            ])
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="menu_main")])
        await query.edit_message_text("✅ Approved Users:", reply_markup=InlineKeyboardMarkup(rows))

    elif data.startswith("unapprove_"):
        target = int(data.split("_")[1])
        if target in users_data["approved"]:
            users_data["approved"].remove(target)
        save_users(users_data)
        await query.answer("Access hataya gaya!")
        rows = []
        for aid in users_data["approved"][:15]:
            rows.append([InlineKeyboardButton(name_for(aid), callback_data="noop")])
            rows.append([
                InlineKeyboardButton("🚫 Block", callback_data=f"block_{aid}"),
                InlineKeyboardButton("🗑️ Remove", callback_data=f"unapprove_{aid}"),
            ])
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="menu_main")])
        if not users_data["approved"]:
            await query.edit_message_text("Koi approved user nahi hai.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="menu_main")]]))
        else:
            await query.edit_message_text("✅ Approved Users:", reply_markup=InlineKeyboardMarkup(rows))

    elif data.startswith("unblock_"):
        target = int(data.split("_")[1])
        if target in users_data["blocked"]:
            users_data["blocked"].remove(target)
        save_users(users_data)
        await query.answer("Unblocked!")
        try:
            await context.bot.send_message(target, "✅ Tumhara block hata diya gaya hai. /start bhejo dobara use karne ke liye.")
        except:
            pass
        rows = []
        for bid in users_data["blocked"][:15]:
            rows.append([InlineKeyboardButton(name_for(bid), callback_data="noop")])
            rows.append([InlineKeyboardButton("✅ Unblock", callback_data=f"unblock_{bid}")])
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="menu_main")])
        if not users_data["blocked"]:
            await query.edit_message_text("Koi blocked user nahi hai.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="menu_main")]]))
        else:
            await query.edit_message_text("🚫 Blocked Users:", reply_markup=InlineKeyboardMarkup(rows))

    elif data == "noop":
        await query.answer()
        return

    await query.answer()

# ---------------- MAIN ----------------

async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("panel", panel))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply))

    webhook_path = f"/webhook/{TOKEN}"
    full_webhook_url = f"{WEBHOOK_URL}{webhook_path}"

    await app.initialize()
    await app.bot.set_webhook(url=full_webhook_url, drop_pending_updates=True)

    async def handle_webhook(request):
        data = await request.json()
        update = Update.de_json(data, app.bot)
        await app.process_update(update)
        return web.Response(text="OK")

    async def handle_health(request):
        return web.Response(text="Jaquar Bot is Running!")

    web_app = web.Application()
    web_app.router.add_post(webhook_path, handle_webhook)
    web_app.router.add_get("/", handle_health)
    web_app.router.add_get("/health", handle_health)

    await app.start()
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    print(f"Webhook: {full_webhook_url}")
    print(f"Port: {PORT}")
    print(f"Admins: {ADMIN_IDS}")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
    
