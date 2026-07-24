"""
CRM + Order Management module for the Jaquar price bot.
Bathroom-wise quotations, material status tracking, payment tracking,
delivery dates, WhatsApp share.
"""
import json
import os
import uuid
from datetime import datetime, timedelta
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

import db

DEFAULT_CUSTOMERS_DATA = {"customers": {}, "next_id": 1, "quote_lookup": {}}
customers_data = dict(DEFAULT_CUSTOMERS_DATA)

async def load_customers():
    global customers_data
    data = await db.load_json("customers_data", DEFAULT_CUSTOMERS_DATA)
    for k, v in DEFAULT_CUSTOMERS_DATA.items():
        data.setdefault(k, v)
    customers_data = data

async def save_customers(data):
    await db.save_json("customers_data", data)

COMPANY_NAME = "MST CERAMIC WORLD"
COMPANY_SUB = "Authorized Jaquar Dealer"
COMPANY_ADDRESS = "Lalji Arcade, Kalyan"
COMPANY_GST = "27AABFM8508N1ZV"

SEGMENTS = ["Shower Area", "WC Area", "Basin Area", "Water Geyser & Wellness", "Accessories"]
STATUS_CYCLE = ["Pending", "Ordered (Vashi Godown)", "Ready/In Stock", "Delivered"]
STATUS_EMOJI = {"Pending": "🔴", "Ordered (Vashi Godown)": "🟡", "Ready/In Stock": "🟢", "Delivered": "✅"}
PAYMENT_MODES = ["Cash", "UPI", "Bank Transfer", "Cheque", "Credit"]

def _approved(context, uid):
    check = context.bot_data.get("is_approved")
    return check(uid) if check else True

def _is_admin(context, uid):
    check = context.bot_data.get("is_admin")
    return check(uid) if check else False

# ---------------- Conversation states ----------------
ADD_NAME, ADD_PHONE, ADD_ADDRESS = range(3)
(BATHROOM_NAME, SEGMENT_ITEMS, DELIVERY_DATE, DISCOUNT,
 PAYMENT_AMOUNT, PAYMENT_MODE) = range(3, 9)

# ================= ADD CUSTOMER =================

async def addcustomer_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _approved(context, update.effective_user.id):
        await update.message.reply_text("❌ Access nahi hai. /start bhejo.")
        return ConversationHandler.END
    await update.message.reply_text("👤 Customer ka naam bhejo:\n(/cancel se cancel)")
    return ADD_NAME

async def addcustomer_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_cust_name"] = update.message.text.strip()
    await update.message.reply_text("📞 Phone number bhejo:")
    return ADD_PHONE

async def addcustomer_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_cust_phone"] = update.message.text.strip()
    await update.message.reply_text("📍 Address bhejo:")
    return ADD_ADDRESS

async def addcustomer_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    address = update.message.text.strip()
    cid = str(customers_data["next_id"])
    customers_data["next_id"] += 1
    customers_data["customers"][cid] = {
        "name": context.user_data.pop("new_cust_name"),
        "phone": context.user_data.pop("new_cust_phone"),
        "address": address,
        "added_by": update.effective_user.full_name,
        "created_at": datetime.now().strftime("%d-%b-%Y"),
        "followup_date": None,
        "notes": "",
        "quotations": [],
    }
    await save_customers(customers_data)
    await update.message.reply_text(f"✅ Customer saved! ID: {cid}\n\n/customers se list dekh sakte ho.")
    return ConversationHandler.END

async def cancel_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Cancel ho gaya.")
    return ConversationHandler.END

addcustomer_conv = ConversationHandler(
    entry_points=[CommandHandler("addcustomer", addcustomer_start)],
    states={
        ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, addcustomer_name)],
        ADD_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addcustomer_phone)],
        ADD_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, addcustomer_address)],
    },
    fallbacks=[CommandHandler("cancel", cancel_flow)],
)

# ================= CUSTOMER LIST / VIEW =================

def customers_menu_rows():
    rows = []
    for cid, c in list(customers_data["customers"].items())[:25]:
        rows.append([InlineKeyboardButton(f"{c['name']} ({c['phone']})", callback_data=f"custview_{cid}")])
    return rows

async def customers_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _approved(context, update.effective_user.id):
        await update.message.reply_text("❌ Access nahi hai. /start bhejo.")
        return
    if not customers_data["customers"]:
        await update.message.reply_text("Koi customer nahi hai abhi. /addcustomer se add karo.")
        return
    await update.message.reply_text("👥 Customers:", reply_markup=InlineKeyboardMarkup(customers_menu_rows()))

def customer_totals(c):
    total_amt = 0.0
    total_received = 0.0
    for q in c["quotations"]:
        total_amt += q["total"]
        total_received += q["payment"]["received"]
    return total_amt, total_received, total_amt - total_received

def customer_detail_text(cid, c):
    total, received, due = customer_totals(c)
    text = (
        f"👤 {c['name']}\n"
        f"📞 {c['phone']}\n"
        f"📍 {c['address']}\n"
        f"🗓️ Added: {c['created_at']} by {c['added_by']}\n"
        f"⏰ Follow-up: {c['followup_date'] or '-'}\n\n"
        f"📄 Quotations: {len(c['quotations'])}\n"
        f"💰 Total: Rs.{total:,.2f} | Received: Rs.{received:,.2f} | Due: Rs.{due:,.2f}\n"
    )
    for q in c["quotations"][-5:]:
        due_q = q["total"] - q["payment"]["received"]
        text += f"  • {q['date']} — Rs.{q['total']:,.2f} (Due: Rs.{due_q:,.2f})\n"
    return text

def customer_detail_kb(cid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧾 New Quotation", callback_data=f"newquote_{cid}")],
        [InlineKeyboardButton("📦 View Latest Order Status", callback_data=f"vieworder_{cid}")],
        [InlineKeyboardButton("💰 Record Payment", callback_data=f"recpay_{cid}")],
        [InlineKeyboardButton("⏰ Set Follow-up (3 din)", callback_data=f"followup3_{cid}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="custlist_back")],
    ])

async def customer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _approved(context, query.from_user.id):
        await query.answer("Access nahi hai.", show_alert=True)
        return
    cid = query.data.split("_")[1]
    c = customers_data["customers"].get(cid)
    if not c:
        await query.answer("Customer nahi mila.")
        return
    await query.edit_message_text(customer_detail_text(cid, c), reply_markup=customer_detail_kb(cid))
    await query.answer()

async def customer_list_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text("👥 Customers:", reply_markup=InlineKeyboardMarkup(customers_menu_rows()))
    await query.answer()

async def followup_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    cid = query.data.split("_")[1]
    c = customers_data["customers"].get(cid)
    if c:
        due = (datetime.now() + timedelta(days=3)).strftime("%d-%b-%Y")
        c["followup_date"] = due
        await save_customers(customers_data)
        await query.answer(f"Follow-up set: {due}")
        await query.edit_message_text(customer_detail_text(cid, c), reply_markup=customer_detail_kb(cid))
    else:
        await query.answer("Customer nahi mila.")

async def followups_due(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _approved(context, update.effective_user.id):
        await update.message.reply_text("❌ Access nahi hai.")
        return
    today = datetime.now().date()
    due_list = []
    for cid, c in customers_data["customers"].items():
        if c.get("followup_date"):
            try:
                fd = datetime.strptime(c["followup_date"], "%d-%b-%Y").date()
                if fd <= today:
                    due_list.append((cid, c))
            except:
                pass
    if not due_list:
        await update.message.reply_text("✅ Koi follow-up due nahi hai.")
        return
    text = "⏰ Follow-ups Due:\n\n"
    for cid, c in due_list:
        text += f"👤 {c['name']} — {c['phone']} (due: {c['followup_date']})\n"
    await update.message.reply_text(text)

async def payment_due_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _approved(context, update.effective_user.id):
        await update.message.reply_text("❌ Access nahi hai.")
        return
    due_customers = []
    for cid, c in customers_data["customers"].items():
        total, received, due = customer_totals(c)
        if due > 0.5:
            due_customers.append((cid, c, due))
    if not due_customers:
        await update.message.reply_text("✅ Koi payment due nahi hai.")
        return
    due_customers.sort(key=lambda x: -x[2])
    text = "💰 Payment Due:\n\n"
    for cid, c, due in due_customers:
        text += f"👤 {c['name']} ({c['phone']}) — Rs.{due:,.2f} due\n"
    await update.message.reply_text(text)

async def all_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _is_admin(context, uid):
        await update.message.reply_text("❌ Ye command sirf admin ke liye hai.")
        return
    lines = ["📋 All Orders (sabhi quotations):\n"]
    found = False
    for cid, c in customers_data["customers"].items():
        for q in c["quotations"]:
            found = True
            due_q = q["total"] - q["payment"]["received"]
            lines.append(
                f"👤 {c['name']} | 🧑‍💼 {q.get('created_by', '?')} | {q['date']} | "
                f"Rs.{q['total']:,.2f} (Due: Rs.{due_q:,.2f})"
            )
    if not found:
        await update.message.reply_text("Koi order nahi hai abhi.")
        return
    text = "\n".join(lines)
    if len(text) > 3800:
        text = text[:3800] + "\n...(aur bhi hain)"
    await update.message.reply_text(text)

# ================= QUOTATION FLOW =================

def seg_prompt(bathroom, segment):
    return (
        f"🛁 {bathroom} — 📦 {segment}\n\n"
        f"Products is format me bhejo (ek line ek product):\n"
        f"CODE QUANTITY RATE\n"
        f"Example: ALD-CHR-079N 2 45000\n\n"
        f"Segment khatam karke agle pe jaane ke liye 'next' likho."
    )

async def newquote_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _approved(context, query.from_user.id):
        await query.answer("Access nahi hai.", show_alert=True)
        return ConversationHandler.END
    cid = query.data.split("_")[1]
    context.user_data["quote_cid"] = cid
    context.user_data["quote_items"] = []
    await query.answer()
    await query.message.reply_text(
        "🧾 Naya Quotation shuru\n\nBathroom ka naam do (e.g. Master Bathroom 1):"
    )
    return BATHROOM_NAME

async def bathroom_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() == "done":
        if not context.user_data.get("quote_items"):
            await update.message.reply_text("Koi product add nahi hua. Kam se kam ek bathroom/product add karo ya /cancel karo.")
            return BATHROOM_NAME
        await update.message.reply_text("📅 Expected delivery date? (e.g. 25-Jul-2026) ya 'skip'")
        return DELIVERY_DATE

    context.user_data["current_bathroom"] = text
    context.user_data["segment_idx"] = 0
    await update.message.reply_text(seg_prompt(text, SEGMENTS[0]))
    return SEGMENT_ITEMS

async def segment_items_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    seg_idx = context.user_data["segment_idx"]
    bathroom = context.user_data["current_bathroom"]

    if text.lower() in ("next", "skip"):
        seg_idx += 1
        if seg_idx < len(SEGMENTS):
            context.user_data["segment_idx"] = seg_idx
            await update.message.reply_text(seg_prompt(bathroom, SEGMENTS[seg_idx]))
            return SEGMENT_ITEMS
        else:
            await update.message.reply_text(
                f"✅ {bathroom} complete!\n\nAgle bathroom ka naam do, ya 'done' likho agar sab ho gaya."
            )
            return BATHROOM_NAME

    parts = text.split()
    if len(parts) < 2:
        await update.message.reply_text("Format galat hai. Example: ALD-CHR-079N 2 45000")
        return SEGMENT_ITEMS

    code = parts[0]
    try:
        qty = int(parts[1])
    except:
        await update.message.reply_text("Quantity number honi chahiye. Example: ALD-CHR-079N 2 45000")
        return SEGMENT_ITEMS

    rate = None
    if len(parts) >= 3:
        try:
            rate = float(parts[2])
        except:
            pass

    all_data = context.bot_data.get("all_data", [])
    found = None
    for row in all_data:
        if str(row.get("CODE", "")).strip().lower() == code.lower():
            found = row
            break

    desc = code
    if found:
        desc = str(found.get("DESCRIPTION", code)).strip()
        if rate is None:
            mrp = str(found.get("MRP_JULY2026", "")).strip()
            try:
                rate = float(mrp)
            except:
                rate = 0.0
    elif rate is None:
        rate = 0.0

    context.user_data["quote_items"].append({
        "bathroom": bathroom, "segment": SEGMENTS[seg_idx],
        "code": code, "desc": desc, "qty": qty, "rate": rate,
        "amount": qty * rate, "status": "Pending",
    })
    await update.message.reply_text(f"✅ Added: {code} x{qty} @ Rs.{rate}\n\nAur product bhejo ya 'next' likho.")
    return SEGMENT_ITEMS

async def delivery_date_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["quote_delivery"] = "" if text.lower() == "skip" else text
    await update.message.reply_text("💸 Discount % dena hai? (nahi to 0 bhejo)")
    return DISCOUNT

async def discount_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        discount = float(text)
    except:
        discount = 0.0
    context.user_data["quote_discount"] = discount
    await update.message.reply_text("💰 Abhi kitna payment mila hai? (amount, 0 agar nahi mila)")
    return PAYMENT_AMOUNT

async def payment_amount_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        amount = float(text)
    except:
        amount = 0.0
    context.user_data["quote_payment_amount"] = amount

    if amount <= 0:
        return await finalize_quotation(update, context, mode="")

    kb = InlineKeyboardMarkup([[InlineKeyboardButton(m, callback_data=f"paymode_{m}")] for m in PAYMENT_MODES])
    await update.message.reply_text("💳 Payment mode kya hai?", reply_markup=kb)
    return PAYMENT_MODE

async def payment_mode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    mode = query.data.split("_", 1)[1]
    await query.answer()
    await query.edit_message_text(f"💳 Payment mode: {mode}")
    return await finalize_quotation(update, context, mode=mode, from_callback=True)

async def finalize_quotation(update, context, mode="", from_callback=False):
    cid = context.user_data.pop("quote_cid")
    items = context.user_data.pop("quote_items")
    delivery = context.user_data.pop("quote_delivery", "")
    discount = context.user_data.pop("quote_discount", 0.0)
    payment_amount = context.user_data.pop("quote_payment_amount", 0.0)
    context.user_data.pop("current_bathroom", None)
    context.user_data.pop("segment_idx", None)

    c = customers_data["customers"].get(cid)
    target = update.callback_query.message if from_callback else update.message
    if not c:
        await target.reply_text("Customer nahi mila.")
        return ConversationHandler.END

    subtotal = sum(i["amount"] for i in items)
    discount_amt = subtotal * discount / 100
    total = subtotal - discount_amt

    qid = uuid.uuid4().hex[:8]
    user = update.effective_user
    quotation = {
        "qid": qid,
        "date": datetime.now().strftime("%d-%b-%Y"),
        "created_by": user.full_name,
        "created_by_id": user.id,
        "items": items,
        "delivery_date": delivery,
        "subtotal": subtotal,
        "discount": discount,
        "discount_amt": discount_amt,
        "total": total,
        "payment": {"received": payment_amount, "mode": mode, "history": (
            [{"amount": payment_amount, "mode": mode, "date": datetime.now().strftime("%d-%b-%Y")}]
            if payment_amount > 0 else []
        )},
    }
    c["quotations"].append(quotation)
    customers_data["quote_lookup"][qid] = cid
    await save_customers(customers_data)

    pdf_path = f"/tmp/quotation_{qid}.pdf"
    generate_quotation_pdf(pdf_path, c, quotation)

    with open(pdf_path, "rb") as f:
        await target.reply_document(f, filename=f"Quotation_{c['name']}.pdf",
                                     caption=f"🧾 Quotation ready! Total: Rs.{total:,.2f}")

    wa_link = build_whatsapp_link(c["phone"], c["name"], quotation)
    if wa_link:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📲 Share on WhatsApp", url=wa_link)]])
        await target.reply_text("Customer ko WhatsApp pe bhejne ke liye:", reply_markup=kb)

    return ConversationHandler.END

quote_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(newquote_start, pattern="^newquote_")],
    states={
        BATHROOM_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, bathroom_name_handler)],
        SEGMENT_ITEMS: [MessageHandler(filters.TEXT & ~filters.COMMAND, segment_items_handler)],
        DELIVERY_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, delivery_date_handler)],
        DISCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, discount_handler)],
        PAYMENT_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, payment_amount_handler)],
        PAYMENT_MODE: [CallbackQueryHandler(payment_mode_handler, pattern="^paymode_")],
    },
    fallbacks=[CommandHandler("cancel", cancel_flow)],
    per_message=False,
)

# ================= ORDER STATUS VIEW =================

def order_status_text_and_kb(qid):
    cid = customers_data["quote_lookup"].get(qid)
    c = customers_data["customers"].get(cid) if cid else None
    if not c:
        return None, None
    q = next((x for x in c["quotations"] if x["qid"] == qid), None)
    if not q:
        return None, None

    text = f"📦 Order Status — {c['name']}\n🗓️ {q['date']} | 🚚 Delivery: {q['delivery_date'] or 'TBD'}\n\n"
    rows = []
    current_bathroom = None
    for idx, item in enumerate(q["items"]):
        if item["bathroom"] != current_bathroom:
            current_bathroom = item["bathroom"]
            text += f"\n🛁 {current_bathroom}\n"
        emoji = STATUS_EMOJI.get(item["status"], "⚪")
        text += f"  {emoji} [{item['segment']}] {item['code']} x{item['qty']} — {item['status']}\n"
        rows.append([InlineKeyboardButton(
            f"{item['code']} → next status", callback_data=f"itemstat_{qid}_{idx}"
        )])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"custview_{cid}")])
    return text, InlineKeyboardMarkup(rows)

async def view_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _approved(context, query.from_user.id):
        await query.answer("Access nahi hai.", show_alert=True)
        return
    cid = query.data.split("_")[1]
    c = customers_data["customers"].get(cid)
    if not c or not c["quotations"]:
        await query.answer("Koi quotation nahi hai.", show_alert=True)
        return
    qid = c["quotations"][-1]["qid"]
    text, kb = order_status_text_and_kb(qid)
    await query.edit_message_text(text, reply_markup=kb)
    await query.answer()

async def cycle_item_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _approved(context, query.from_user.id):
        await query.answer("Access nahi hai.", show_alert=True)
        return
    _, qid, idx_str = query.data.split("_")
    idx = int(idx_str)
    cid = customers_data["quote_lookup"].get(qid)
    c = customers_data["customers"].get(cid) if cid else None
    if not c:
        await query.answer("Nahi mila.")
        return
    q = next((x for x in c["quotations"] if x["qid"] == qid), None)
    if not q or idx >= len(q["items"]):
        await query.answer("Nahi mila.")
        return

    item = q["items"][idx]
    cur = STATUS_CYCLE.index(item["status"]) if item["status"] in STATUS_CYCLE else 0
    item["status"] = STATUS_CYCLE[(cur + 1) % len(STATUS_CYCLE)]
    await save_customers(customers_data)

    text, kb = order_status_text_and_kb(qid)
    await query.edit_message_text(text, reply_markup=kb)
    await query.answer(f"Status: {item['status']}")

# ================= PAYMENT RECORDING =================

RECPAY_AMOUNT = 100

async def recpay_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _approved(context, query.from_user.id):
        await query.answer("Access nahi hai.", show_alert=True)
        return ConversationHandler.END
    cid = query.data.split("_")[1]
    context.user_data["recpay_cid"] = cid
    await query.answer()
    await query.message.reply_text("💰 Kitna payment aaya hai? (amount bhejo)")
    return RECPAY_AMOUNT

async def recpay_amount_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        amount = float(text)
    except:
        await update.message.reply_text("Sirf number bhejo. Example: 15000")
        return RECPAY_AMOUNT
    context.user_data["recpay_amount"] = amount
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(m, callback_data=f"recpaymode_{m}")] for m in PAYMENT_MODES])
    await update.message.reply_text("💳 Payment mode?", reply_markup=kb)
    return ConversationHandler.END

async def recpay_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    mode = query.data.split("_", 1)[1]
    cid = context.user_data.pop("recpay_cid", None)
    amount = context.user_data.pop("recpay_amount", 0)
    c = customers_data["customers"].get(cid) if cid else None
    await query.answer()
    if not c or not c["quotations"]:
        await query.edit_message_text("Customer/quotation nahi mila.")
        return
    q = c["quotations"][-1]
    q["payment"]["received"] += amount
    q["payment"]["mode"] = mode
    q["payment"]["history"].append({"amount": amount, "mode": mode, "date": datetime.now().strftime("%d-%b-%Y")})
    await save_customers(customers_data)
    due = q["total"] - q["payment"]["received"]
    await query.edit_message_text(f"✅ Rs.{amount:,.2f} ({mode}) record ho gaya.\nBaki due: Rs.{due:,.2f}")

recpay_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(recpay_start, pattern="^recpay_")],
    states={RECPAY_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, recpay_amount_handler)]},
    fallbacks=[CommandHandler("cancel", cancel_flow)],
    per_message=False,
)

# ================= WHATSAPP LINK =================

def build_whatsapp_link(phone, name, quotation):
    digits = "".join(ch for ch in phone if ch.isdigit())
    if not digits:
        return None
    if len(digits) == 10:
        digits = "91" + digits
    msg = f"Namaste {name}, aapka quotation MST Ceramic World se ready hai. Total: Rs.{quotation['total']:,.2f}."
    if quotation.get("delivery_date"):
        msg += f" Expected delivery: {quotation['delivery_date']}."
    from urllib.parse import quote
    return f"https://wa.me/{digits}?text={quote(msg)}"

# ================= PDF GENERATION =================

def generate_quotation_pdf(path, customer, quotation):
    doc = SimpleDocTemplate(path, pagesize=A4, topMargin=15*mm, bottomMargin=15*mm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title", parent=styles["Heading1"], fontSize=16, textColor=colors.HexColor("#1a3c6e"))
    sub_style = ParagraphStyle("sub", parent=styles["Normal"], fontSize=9, textColor=colors.grey)
    bath_style = ParagraphStyle("bath", parent=styles["Heading3"], fontSize=11, textColor=colors.HexColor("#1a3c6e"), spaceBefore=6)

    elements = []
    elements.append(Paragraph(COMPANY_NAME, title_style))
    elements.append(Paragraph(COMPANY_SUB, sub_style))
    elements.append(Paragraph(f"{COMPANY_ADDRESS} | GST: {COMPANY_GST}", sub_style))
    elements.append(Spacer(1, 8*mm))

    elements.append(Paragraph(f"<b>Quotation Date:</b> {quotation['date']}", styles["Normal"]))
    elements.append(Paragraph(f"<b>Customer:</b> {customer['name']}", styles["Normal"]))
    elements.append(Paragraph(f"<b>Phone:</b> {customer['phone']}", styles["Normal"]))
    elements.append(Paragraph(f"<b>Address:</b> {customer['address']}", styles["Normal"]))
    if quotation.get("delivery_date"):
        elements.append(Paragraph(f"<b>Expected Delivery:</b> {quotation['delivery_date']}", styles["Normal"]))
    elements.append(Spacer(1, 6*mm))

    bathrooms = {}
    for item in quotation["items"]:
        bathrooms.setdefault(item["bathroom"], []).append(item)

    for bath, items in bathrooms.items():
        elements.append(Paragraph(f"🛁 {bath}", bath_style))
        table_data = [["Segment", "Code", "Description", "Qty", "Rate", "Amount"]]
        for i in items:
            table_data.append([i["segment"], i["code"], i["desc"][:30], str(i["qty"]),
                                f"{i['rate']:,.2f}", f"{i['amount']:,.2f}"])
        table = Table(table_data, colWidths=[70, 65, 145, 25, 55, 60])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a3c6e")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 4*mm))

    totals_data = [
        ["Subtotal", f"Rs.{quotation['subtotal']:,.2f}"],
        [f"Discount ({quotation['discount']}%)", f"-Rs.{quotation['discount_amt']:,.2f}"],
        ["Total", f"Rs.{quotation['total']:,.2f}"],
        ["Received", f"Rs.{quotation['payment']['received']:,.2f}"],
        ["Due", f"Rs.{quotation['total'] - quotation['payment']['received']:,.2f}"],
    ]
    totals_table = Table(totals_data, colWidths=[100, 100], hAlign="RIGHT")
    totals_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
        ("LINEABOVE", (0, 2), (-1, 2), 1, colors.black),
    ]))
    elements.append(totals_table)
    elements.append(Spacer(1, 8*mm))
    elements.append(Paragraph("This is a computer-generated quotation. Prices valid for 7 days.", sub_style))

    doc.build(elements)

# ================= REGISTER =================

async def register_crm_handlers(app, all_data, is_approved_fn=None, is_admin_fn=None):
    await load_customers()
    app.bot_data["all_data"] = all_data
    if is_approved_fn:
        app.bot_data["is_approved"] = is_approved_fn
    if is_admin_fn:
        app.bot_data["is_admin"] = is_admin_fn

    app.add_handler(addcustomer_conv)
    app.add_handler(quote_conv)
    app.add_handler(recpay_conv)

    app.add_handler(CommandHandler("customers", customers_list))
    app.add_handler(CommandHandler("followups", followups_due))
    app.add_handler(CommandHandler("paymentdue", payment_due_list))
    app.add_handler(CommandHandler("allorders", all_orders))

    app.add_handler(CallbackQueryHandler(customer_callback, pattern="^custview_"))
    app.add_handler(CallbackQueryHandler(customer_list_back, pattern="^custlist_back$"))
    app.add_handler(CallbackQueryHandler(followup_set, pattern="^followup3_"))
    app.add_handler(CallbackQueryHandler(view_order, pattern="^vieworder_"))
    app.add_handler(CallbackQueryHandler(cycle_item_status, pattern="^itemstat_"))
    app.add_handler(CallbackQueryHandler(recpay_mode_callback, pattern="^recpaymode_"))
