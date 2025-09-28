import os
import re
import json
import random
import sqlite3
from threading import Thread
from flask import Flask, Response
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, ConversationHandler

# ---------- CONFIG ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DB_PATH = "profiles.db"
PORT = int(os.environ.get("PORT", 5000))
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

# ---------- DB helpers ----------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS profiles (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        name TEXT,
        age TEXT,
        role TEXT,
        photo_id TEXT,
        inventory TEXT,
        stats TEXT,
        exp INTEGER,
        bio TEXT
    )""")
    conn.commit()
    conn.close()

def get_profile(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    keys = ["user_id","username","name","age","role","photo_id","inventory","stats","exp","bio"]
    return dict(zip(keys, row))

def save_profile(data: dict):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO profiles(user_id, username, name, age, role, photo_id, inventory, stats, exp, bio)
    VALUES(?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(user_id) DO UPDATE SET
      username=excluded.username,
      name=excluded.name,
      age=excluded.age,
      role=excluded.role,
      photo_id=excluded.photo_id,
      inventory=excluded.inventory,
      stats=excluded.stats,
      exp=excluded.exp,
      bio=excluded.bio
    """, (
        data.get("user_id"),
        data.get("username"),
        data.get("name"),
        data.get("age"),
        data.get("role"),
        data.get("photo_id"),
        json.dumps(data.get("inventory", []), ensure_ascii=False),
        json.dumps(data.get("stats", {}), ensure_ascii=False),
        data.get("exp", 0),
        data.get("bio", "")
    ))
    conn.commit()
    conn.close()

# ---------- Dice parser ----------
def roll_expression(expr: str):
    expr = expr.strip().lower()
    m = re.match(r"^(\d+)d(\d+)$", expr)
    if not m:
        return None
    count, sides = int(m.group(1)), int(m.group(2))
    if count > 100 or sides > 1000:
        return None
    rolls = [random.randint(1, sides) for _ in range(count)]
    return rolls

# ---------- Conversation states for profile creation/edit ----------
(NAME, AGE, ROLE, PHOTO, BIO, STATS_DONE) = range(6)

# ---------- Handlers ----------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я RPG‑бот. /roll 2d20  — бросок. /анкета — создать/посмотреть профиль.")

async def roll_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Использование: /roll 2d20")
        return
    expr = args[0]
    rolls = roll_expression(expr)
    if rolls is None:
        await update.message.reply_text("Неверный формат или слишком большие значения. Пример: /roll 2d20")
        return
    await update.message.reply_text(f"🎲 {expr}: {rolls}  — сумма {sum(rolls)}")

# --- Profile conversation ---
async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    # If username provided, show that user's profile
    if args:
        target = args[0].lstrip("@")
        # try find by username
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM profiles WHERE username = ?", (target,))
        row = cur.fetchone()
        conn.close()
        if not row:
            await update.message.reply_text("Анкета не найдена.")
            return
        return await show_profile_by_id(update, context, row[0])
    # own profile or start creation
    prof = get_profile(user.id)
    if prof:
        return await show_profile_by_id(update, context, user.id)
    await update.message.reply_text("У тебя нет анкеты. Давай создадим.\nКак зовут персонажа?")
    return NAME

async def name_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["profile"] = {"user_id": update.effective_user.id, "username": update.effective_user.username}
    context.user_data["profile"]["name"] = update.message.text.strip()
    await update.message.reply_text("Возраст персонажа?")
    return AGE

async def age_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["profile"]["age"] = update.message.text.strip()
    await update.message.reply_text("Роль / класс персонажа?")
    return ROLE

async def role_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["profile"]["role"] = update.message.text.strip()
    await update.message.reply_text("Прикрепи фото персонажа или напиши 'нет'")
    return PHOTO

async def photo_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prof = context.user_data["profile"]
    if update.message.photo:
        prof["photo_id"] = update.message.photo[-1].file_id
    else:
        prof["photo_id"] = None
    await update.message.reply_text("Короткая предыстория (bio):")
    return BIO

async def bio_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["profile"]["bio"] = update.message.text.strip()
    # defaults
    context.user_data["profile"]["inventory"] = []
    context.user_data["profile"]["stats"] = {"strength": 1, "dex":1, "int":1}
    context.user_data["profile"]["exp"] = 0
    save_profile(context.user_data["profile"])
    await update.message.reply_text("Анкета создана. Используй /анкета чтобы посмотреть.")
    return ConversationHandler.END

async def cancel_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Создание анкеты отменено.")
    return ConversationHandler.END

# show profile helper
async def show_profile_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id):
    prof = get_profile(user_id)
    if not prof:
        await update.message.reply_text("Анкета не найдена.")
        return
    text = f"Имя: {prof.get('name') or '-'}\nВозраст: {prof.get('age') or '-'}\nРоль: {prof.get('role') or '-'}\nОпыт: {prof.get('exp')}\n\nПредыстория:\n{prof.get('bio') or '-'}"
    buttons = []
    buttons.append([InlineKeyboardButton("Инвентарь", callback_data=f"inv:{user_id}"),
                    InlineKeyboardButton("Статистика", callback_data=f"stats:{user_id}")])
    if update.effective_user.id == user_id:
        buttons.append([InlineKeyboardButton("Редактировать", callback_data=f"edit:{user_id}")])
    kb = InlineKeyboardMarkup(buttons)
    if prof.get("photo_id"):
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=prof.get("photo_id"), caption=text, reply_markup=kb)
    else:
        await update.message.reply_text(text, reply_markup=kb)

# callback queries for inventory / stats / edit
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    kind, uid = data.split(":",1)
    uid = int(uid)
    prof = get_profile(uid)
    if not prof:
        await q.edit_message_text("Анкета не найдена.")
        return
    if kind == "inv":
        inv = prof.get("inventory") or "[]"
        try:
            inv_list = json.loads(inv) if isinstance(inv, str) else inv
        except:
            inv_list = []
        text = "Инвентарь:\n" + ("\n".join(f"- {i}" for i in inv_list) if inv_list else "Пусто")
        await q.edit_message_text(text)
    elif kind == "stats":
        stats = prof.get("stats") or "{}"
        try:
            stats_d = json.loads(stats) if isinstance(stats, str) else stats
        except:
            stats_d = {}
        text = "Статистика:\n" + ("\n".join(f"{k}: {v}" for k,v in stats_d.items()))
        await q.edit_message_text(text)
    elif kind == "edit":
        # only owner can edit
        if update.effective_user.id != uid:
            await q.edit_message_text("Редактировать может только владелец анкеты.")
            return
        # offer simple edit menu
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Изменить имя", callback_data=f"editname:{uid}")],
            [InlineKeyboardButton("Изменить фото", callback_data=f"editphoto:{uid}")],
            [InlineKeyboardButton("Изменить биографию", callback_data=f"editbio:{uid}")],
            [InlineKeyboardButton("Отмена", callback_data="cancel")]
        ])
        await q.edit_message_text("Выберите поле для редактирования:", reply_markup=kb)
    elif data == "cancel":
        await q.edit_message_text("Операция отменена.")

# simple edit handlers (for demo keep linear)
async def edit_choice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    if data.startswith("editname:"):
        await q.edit_message_text("Напиши новое имя:")
        context.user_data["awaiting_edit"] = ("name", int(data.split(":")[1]))
    elif data.startswith("editphoto:"):
        await q.edit_message_text("Прикрепи новое фото:")
        context.user_data["awaiting_edit"] = ("photo", int(data.split(":")[1]))
    elif data.startswith("editbio:"):
        await q.edit_message_text("Напиши новую биографию:")
        context.user_data["awaiting_edit"] = ("bio", int(data.split(":")[1]))
    elif data == "cancel":
        await q.edit_message_text("Отменено.")

async def message_edit_receiver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "awaiting_edit" not in context.user_data:
        return
    field, uid = context.user_data["awaiting_edit"]
    if update.effective_user.id != uid:
        await update.message.reply_text("Редактировать может только владелец.")
        context.user_data.pop("awaiting_edit", None)
        return
    prof = get_profile(uid) or {"user_id": uid, "username": update.effective_user.username}
    if field == "name":
        prof["name"] = update.message.text.strip()
    elif field == "bio":
        prof["bio"] = update.message.text.strip()
    elif field == "photo":
        if update.message.photo:
            prof["photo_id"] = update.message.photo[-1].file_id
        else:
            await update.message.reply_text("Фото не получено. Отмена.")
            context.user_data.pop("awaiting_edit", None)
            return
    save_profile(prof)
    await update.message.reply_text("Изменение сохранено.")
    context.user_data.pop("awaiting_edit", None)

# ---------- Flask keepalive ----------
flask_app = Flask("keepalive")
@flask_app.route("/")
def index():
    return Response("OK", status=200)
def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT)

# ---------- Setup and run ----------
def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("анкета", profile_cmd)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, name_step)],
            AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, age_step)],
            ROLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, role_step)],
            PHOTO: [MessageHandler((filters.PHOTO | filters.TEXT) & ~filters.COMMAND, photo_step)],
            BIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, bio_step)],
        },
        fallbacks=[CommandHandler("cancel", cancel_profile)],
        allow_reentry=True
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("roll", roll_cmd))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(callback_handler, pattern=r"^(inv:|stats:|edit:)\d+"))
    app.add_handler(CallbackQueryHandler(edit_choice_handler, pattern=r"^(editname:|editphoto:|editbio:|cancel)"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_edit_receiver))
    # start Flask in thread, then polling
    Thread(target=run_flask, daemon=True).start()
    app.run_polling()

if __name__ == "__main__":
    main()
