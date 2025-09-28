# main.py
import os
import re
import json
import random
import sqlite3
from flask import Flask, request, Response
from telegram import Bot, Update

# ---------- CONFIG ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 5000))
DB_PATH = "profiles.db"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set in environment variables")

bot = Bot(token=BOT_TOKEN)
app = Flask(__name__)

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
    prof = dict(zip(keys, row))
    # normalize json fields
    try:
        prof["inventory"] = json.loads(prof["inventory"]) if prof["inventory"] else []
    except:
        prof["inventory"] = []
    try:
        prof["stats"] = json.loads(prof["stats"]) if prof["stats"] else {}
    except:
        prof["stats"] = {}
    prof["exp"] = int(prof["exp"] or 0)
    return prof

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
        int(data.get("exp") or 0),
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
    if count < 1 or count > 100 or sides < 2 or sides > 1000:
        return None
    rolls = [random.randint(1, sides) for _ in range(count)]
    return rolls

# ---------- Command handlers (synchronous calls using bot) ----------
def handle_start(update: Update):
    chat_id = update.effective_chat.id
    bot.send_message(chat_id=chat_id, text="Привет! Я RPG-бот.\n/roll 2d20 — бросок кубиков\n/анкета — показать свою анкету\n/анкета @username — показать чужую анкету")

def handle_roll(update: Update):
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()
    parts = text.split()
    if len(parts) < 2:
        bot.send_message(chat_id=chat_id, text="Использование: /roll 2d20")
        return
    expr = parts[1]
    rolls = roll_expression(expr)
    if rolls is None:
        bot.send_message(chat_id=chat_id, text="Неверный формат или слишком большие числа. Пример: /roll 2d20")
        return
    bot.send_message(chat_id=chat_id, text=f"🎲 {expr}: {rolls} — сумма {sum(rolls)}")

def handle_profile(update: Update):
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()
    parts = text.split()
    # /анкета or /анкета @username
    if len(parts) > 1:
        target = parts[1].lstrip("@")
        conn = sqlite3.connect(DB_PATH); cur = conn.cursor()
        cur.execute("SELECT user_id FROM profiles WHERE username = ?", (target,))
        r = cur.fetchone(); conn.close()
        if not r:
            bot.send_message(chat_id=chat_id, text="Анкета не найдена.")
            return
        prof = get_profile(r[0])
    else:
        user = update.effective_user
        prof = get_profile(user.id)
        if not prof:
            bot.send_message(chat_id=chat_id, text="У тебя ещё нет анкеты. Пока можно создать локально или через отдельную команду/диалог (реализацию можно добавить).")
            return
    text_out = f"Имя: {prof.get('name') or '-'}\nВозраст: {prof.get('age') or '-'}\nРоль: {prof.get('role') or '-'}\nОпыт: {prof.get('exp')}\n\nПредыстория:\n{prof.get('bio') or '-'}"
    if prof.get("photo_id"):
        bot.send_photo(chat_id=chat_id, photo=prof.get("photo_id"), caption=text_out)
    else:
        bot.send_message(chat_id=chat_id, text=text_out)

# Placeholder: save profile by JSON message (quick admin tool)
# Usage: отправь боту в личку JSON с ключом "save_profile": { ... }
def handle_json_commands(update: Update):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if text.startswith("{") and "save_profile" in text:
        try:
            obj = json.loads(text)
            data = obj.get("save_profile")
            if not data or not data.get("user_id"):
                bot.send_message(chat_id=update.effective_chat.id, text="Некорректный JSON или отсутствует user_id")
                return
            save_profile(data)
            bot.send_message(chat_id=update.effective_chat.id, text="Анкета сохранена.")
        except Exception as e:
            bot.send_message(chat_id=update.effective_chat.id, text=f"Ошибка парсинга JSON: {e}")

# Callback query handler skeleton
def handle_callback_query(update: Update):
    cq = update.callback_query
    if not cq:
        return
    # отвечаем, чтобы Telegram не показывал "час ожидания"
    try:
        bot.answer_callback_query(cq.id)
    except Exception:
        pass
    data = cq.data or ""
    # простая распаковка для inv:USERID or stats:USERID
    if ":" in data:
        kind, uid_s = data.split(":", 1)
        try:
            uid = int(uid_s)
        except:
            return
        prof = get_profile(uid)
        if not prof:
            bot.edit_message_text(chat_id=cq.message.chat_id, message_id=cq.message.message_id, text="Анкета не найдена.")
            return
        if kind == "inv":
            inv = prof.get("inventory") or []
            text = "Инвентарь:\n" + ("\n".join(f"- {i}" for i in inv) if inv else "Пусто")
            bot.edit_message_text(chat_id=cq.message.chat_id, message_id=cq.message.message_id, text=text)
        elif kind == "stats":
            stats = prof.get("stats") or {}
            text = "Статистика:\n" + ("\n".join(f"{k}: {v}" for k,v in stats.items()) if stats else "Нет")
            bot.edit_message_text(chat_id=cq.message.chat_id, message_id=cq.message.message_id, text=text)
    else:
        # другие callback'и
        bot.edit_message_text(chat_id=cq.message.chat_id, message_id=cq.message.message_id, text="Нажата кнопка")

# ---------- Webhook endpoint (manual routing) ----------
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, bot)
    except Exception as e:
        print("Invalid update received:", e)
        return Response("Bad Request", status=400)

    try:
        # message routing
        if update.message
