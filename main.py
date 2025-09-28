import os
import re
import json
import random
import sqlite3
from flask import Flask, request, Response
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler

# Config
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 5000))
DB_PATH = "profiles.db"
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set in env")

bot = Bot(token=BOT_TOKEN)
app = Flask(__name__)

# DB helpers
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

# Dice parser
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

# Handlers
def start(update: Update, context=None):
    chat_id = update.effective_chat.id
    bot.send_message(chat_id=chat_id, text="Привет! Я RPG‑бот. /roll 2d20 — бросок. /анкета — создать/посмотреть профиль.")

def roll(update: Update, context=None):
    chat_id = update.effective_chat.id
    args = update.message.text.split()
    if len(args) < 2:
        bot.send_message(chat_id=chat_id, text="Использование: /roll 2d20")
        return
    expr = args[1]
    rolls = roll_expression(expr)
    if rolls is None:
        bot.send_message(chat_id=chat_id, text="Неверный формат. Пример: /roll 2d20")
        return
    bot.send_message(chat_id=chat_id, text=f"🎲 {expr}: {rolls} — сумма {sum(rolls)}")

def profile_cmd(update: Update, context=None):
    user = update.effective_user
    args = (update.message.text or "").split()
    if len(args) > 1:
        target = args[1].lstrip("@")
        conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
        cur.execute("SELECT user_id FROM profiles WHERE username = ?", (target,)); row = cur.fetchone(); conn.close()
        if not row:
            bot.send_message(chat_id=update.effective_chat.id, text="Анкета не найдена.")
            return
        prof = get_profile(row[0])
    else:
        prof = get_profile(user.id)
        if not prof:
            bot.send_message(chat_id=update.effective_chat.id, text="У тебя нет анкеты. Используй диалог создания позже.")
            return
    text = f"Имя: {prof.get('name') or '-'}\nВозраст: {prof.get('age') or '-'}\nРоль: {prof.get('role') or '-'}\nОпыт: {prof.get('exp')}\n\nПредыстория:\n{prof.get('bio') or '-'}"
    if prof.get("photo_id"):
        bot.send_photo(chat_id=update.effective_chat.id, photo=prof.get("photo_id"), caption=text)
    else:
        bot.send_message(chat_id=update.effective_chat.id, text=text)

# Dispatcher to use handlers with webhook input
dispatcher = Dispatcher(bot, None, workers=0, use_context=False)
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("roll", roll))
dispatcher.add_handler(CommandHandler("анкета", profile_cmd))

# Webhook endpoint
@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return Response("OK", status=200)

# Health check
@app.route("/", methods=["GET"])
def index():
    return "OK"

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=PORT)
