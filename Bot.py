import asyncio
import json
import logging
import os
from aiohttp import web
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
import polars as pl
from rapidfuzz import fuzz

logging.basicConfig(level=logging.INFO)

TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 10000))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://mst-price-bot-v2.onrender.com")

# ADMIN_IDS = comma separated Telegram user IDs, set in Render Environment
ADMIN_IDS = set()
_admin_env = os.environ.get("ADMIN_IDS", "")
for _id in _admin_env.split(","):
    _id = _id.strip()
    if _id.isdigit():
        ADMIN_IDS.add(int(_id))

USERS_FILE = "users.json"

def load_users():
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {"approved": [], "pending": {}, "search_count": 0}

def save_users(data):
    with open(USERS_FILE, "w") as f:
        json.dump(data, f)

users_data = load_users()

def is_approved(user_id):
    return user_id in ADMIN_IDS or user_id in users_data["approved"]

def is_admin(user_id):
    return user_id in ADMIN_IDS

# CSV LOAD - new format with 3 price generations
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

# ---------------- ADMIN / ACCESS COMMANDS ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id

    if is_approved(uid):
        await update.message.reply_text(
            "✅ Welcome! Product code ya naam bhejo price dekhne ke liye.\n"
            "Example: ALD-CHR-079N"
        )
        return

    # Already pending?
    if str(uid) in users_data["pending"]:
        await update.message.reply_text("⏳ Tumhari request pending hai, admin approve karega.")
        return

    users_data["pending"][str(uid)] = {
        "name": user.full_name,
        "username": user.username or "N/A",
    }
    save_users(users_data)

    await update.message.reply_text("⏳ Request bheji gayi hai, admin approve karega tabhi bot use kar paoge.")

    # Notify all admins
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                admin_id,
                f"🔔 Naya access request!\n"
                f"Naam: {user.full_name}\n"
                f"Username: @{user.username or 'N/A'}\n"
                f"ID: {uid}\n\n"
                f"Approve karne ke liye: /approve {uid}"
            )
        except:
            pass

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Ye command sirf admin use kar sakta hai.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /approve <user_id>")
        return

    try:
        uid = int(context.args[0])
    except:
        await update.message.reply_text("Invalid user ID.")
        return

    if uid not in users_data["approved"]:
        users_data["approved"].append(uid)
    users_data["pending"].pop(str(uid), None)
    save_users(users_data)

    await update.message.reply_text(f"✅ User {uid} approved!")
    try:
        await context.bot.send_message(uid, "✅ Tumhara access approve ho gaya! Ab product code bhej ke price dekh sakte ho.")
    except:
        pass

async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Ye command sirf admin use kar sakta hai.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /remove <user_id>")
        return

    try:
        uid = int(context.args[0])
    except:
        await update.message.reply_text("Invalid user ID.")
        return

    if uid in users_data["approved"]:
        users_data["approved"].remove(uid)
        save_users(users_data)
        await update.message.reply_text(f"🗑️ User {uid} ka access hata diya gaya.")
    else:
        await update.message.reply_text("Ye user approved list me nahi hai.")

async def pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Ye command sirf admin use kar sakta hai.")
        return

    if not users_data["pending"]:
        await update.message.reply_text("Koi pending request nahi hai.")
        return

    msg = "⏳ Pending Requests:\n\n"
    for uid, info in users_data["pending"].items():
        msg += f"👤 {info['name']} (@{info['username']})\nID: {uid}\n/approve {uid}\n\n"
    await update.message.reply_text(msg)

async def approved_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Ye command sirf admin use kar sakta hai.")
        return

    if not users_data["approved"]:
        await update.message.reply_text("Koi approved user nahi hai.")
        return

    msg = "✅ Approved Users:\n\n"
    for uid in users_data["approved"]:
        msg += f"ID: {uid}\n"
    await update.message.reply_text(msg)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Ye command sirf admin use kar sakta hai.")
        return

    msg = (
        f"📊 Bot Stats\n\n"
        f"✅ Approved users: {len(users_data['approved'])}\n"
        f"⏳ Pending requests: {len(users_data['pending'])}\n"
        f"🔍 Total searches: {users_data['search_count']}\n"
        f"📦 Total products loaded: {len(all_data)}"
    )
    await update.message.reply_text(msg)

# ---------------- PRODUCT SEARCH ----------------

async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not is_approved(uid):
        await update.message.reply_text("❌ Ye bot private hai. Access ke liye /start bhejo, admin approve karega.")
        return

    text = update.message.text.strip()
    results = search_products(text)

    users_data["search_count"] += 1
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

async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("pending", pending))
    app.add_handler(CommandHandler("approved", approved_list))
    app.add_handler(CommandHandler("stats", stats))
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
            
