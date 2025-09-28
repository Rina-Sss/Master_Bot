import os
import re
import json
import random
import sqlite3
import asyncio
import traceback
from flask import Flask, request, Response
from telegram import Bot, Update

# ---------- CONFIG ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 5000))
DB_PATH = "profiles.db"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set in environment variables")

# Semaphore to limit concurrent outbound requests to Telegram
OUTBOUND_SEMAPHORE = asyncio.Semaphore(10)  # start conservative, increase if needed

# Create Bot using library default Request implementation
bot = Bot(token=BOT_TOKEN)

app = Flask(__name__)

# ---------- helper to run async Bot coroutines from sync code ----------
def run_coro(coro):
    """
    Robust runner for coroutines from sync code.
    - If a running loop exists and is usable, schedule a background task.
    - If no running loop or loop is closed, create a temporary loop,
      run coroutine to completion, then close that loop.
    This avoids "Event loop is closed" errors on process restarts.
    """
    async def _with_sem():
        async with OUTBOUND_SEMAPHORE:
            return await coro

    # Try get running loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and not loop.is_closed():
        # schedule as background task
        try:
            return asyncio.create_task(_with_sem())
        except RuntimeError:
            # fallback to temporary loop if scheduling fails
            pass

    # No running loop or it's closed: create temporary loop and run the coro
    new_loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(new_loop)
        return new_loop.run_until_complete(_with_sem())
    finally:
        try:
            new_loop.close()
        except Exception:
            pass
        try:
            asyncio.set_event_loop(None)
        except Exception:
            pass

# ---------- DB helpers ----------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS profiles ("
        "user_id INTEGER PRIMARY KEY, "
        "username TEXT, "
        "name TEXT, "
        "age TEXT, "
        "role TEXT, "
        "photo_id TEXT, "
        "inventory TEXT, "
        "stats TEXT, "
        "exp INTEGER, "
        "bio TEXT"
        ")"
    )
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
    cur.execute(
        "INSERT INTO profiles(user_id, username, name, age, role, photo_id, inventory, stats, exp, bio) "
        "VALUES(?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET "
        "username=excluded.username, name=excluded.name, age=excluded.age, role=excluded.role, "
        "photo_id=excluded.photo_id, inventory=excluded.inventory, stats=excluded.stats, exp=excluded.exp, bio=excluded.bio"
        ,
        (
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
        )
    )
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

# ---------- Command handlers ----------
def handle_start(update: Update):
    chat_id = update.effective_chat.id
    try:
        run_coro(bot.send_message(chat_id=chat_id, text=(
            "Привет! Я RPG-бот.\n"
            "/roll 2d20 — бросок кубиков\n"
            "/анкета — показать свою анкету\n"
            "/анкета @username — показать чужую анкету"
        )))
    except Exception:
        print("Error in handle_start:", traceback.format_exc())

def handle_roll(update: Update, expr_arg=None):
    chat_id = update.effective_chat.id
    try:
        text = (update.message.text or "").strip()
        parts = text.split()
        expr = None
        if expr_arg:
            expr = expr_arg
        elif len(parts) >= 2:
            expr = parts[1]
        if not expr:
            run_coro(bot.send_message(chat_id=chat_id, text="Использование: /roll 2d20"))
            return
        rolls = roll_expression(expr)
        if rolls is None:
            run_coro(bot.send_message(chat_id=chat_id, text="Неверный формат или слишком большие числа. Пример: /roll 2d20"))
            return
        run_coro(bot.send_message(chat_id=chat_id, text=f"🎲 {expr}: {rolls} — сумма {sum(rolls)}"))
    except Exception:
        print("Error in handle_roll:", traceback.format_exc())

def handle_profile(update: Update):
    chat_id = update.effective_chat.id
    try:
        text = (update.message.text or "").strip()
        parts = text.split()
        if len(parts) > 1:
            target = parts[1].lstrip("@")
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT user_id FROM profiles WHERE username = ?", (target,))
            r = cur.fetchone()
            conn.close()
            if not r:
                run_coro(bot.send_message(chat_id=chat_id, text="Анкета не найдена."))
                return
            prof = get_profile(r[0])
        else:
            user = update.effective_user
            prof = get_profile(user.id)
            if not prof:
                run_coro(bot.send_message(chat_id=chat_id, text="У тебя ещё нет анкеты. Пока можно создать локально или через отдельную команду/диалог (реализацию можно добавить)."))
                return
        text_out = (
            f"Имя: {prof.get('name') or '-'}\n"
            f"Возраст: {prof.get('age') or '-'}\n"
            f"Роль: {prof.get('role') or '-'}\n"
            f"Опыт: {prof.get('exp')}\n\n"
            f"Предыстория:\n{prof.get('bio') or '-'}"
        )
        if prof.get("photo_id"):
            run_coro(bot.send_photo(chat_id=chat_id, photo=prof.get("photo_id"), caption=text_out))
        else:
            run_coro(bot.send_message(chat_id=chat_id, text=text_out))
    except Exception:
        print("Error in handle_profile:", traceback.format_exc())

# Quick JSON save (admin helper)
def handle_json_commands(update: Update):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if text.startswith("{") and "save_profile" in text:
        try:
            obj = json.loads(text)
            data = obj.get("save_profile")
            if not data or not data.get("user_id"):
                run_coro(bot.send_message(chat_id=update.effective_chat.id, text="Некорректный JSON или отсутствует user_id"))
                return
            save_profile(data)
            run_coro(bot.send_message(chat_id=update.effective_chat.id, text="Анкета сохранена."))
        except Exception as e:
            run_coro(bot.send_message(chat_id=update.effective_chat.id, text=f"Ошибка парсинга JSON: {e}"))

# Callback query handler skeleton
def handle_callback_query(update: Update):
    cq = update.callback_query
    if not cq:
        return
    try:
        run_coro(bot.answer_callback_query(cq.id))
    except Exception:
        print("Error answering callback:", traceback.format_exc())
    data = cq.data or ""
    if ":" in data:
        kind, uid_s = data.split(":", 1)
        try:
            uid = int(uid_s)
        except:
            return
        prof = get_profile(uid)
        if not prof:
            try:
                run_coro(bot.edit_message_text(chat_id=cq.message.chat.id, message_id=cq.message.message_id, text="Анкета не найдена."))
            except:
                pass
            return
        if kind == "inv":
            inv = prof.get("inventory") or []
            text = "Инвентарь:\n" + ("\n".join(f"- {i}" for i in inv) if inv else "Пусто")
            run_coro(bot.edit_message_text(chat_id=cq.message.chat.id, message_id=cq.message.message_id, text=text))
        elif kind == "stats":
            stats = prof.get("stats") or {}
            text = "Статистика:\n" + ("\n".join(f"{k}: {v}" for k,v in stats.items()) if stats else "Нет")
            run_coro(bot.edit_message_text(chat_id=cq.message.chat.id, message_id=cq.message.message_id, text=text))
    else:
        try:
            run_coro(bot.edit_message_text(chat_id=cq.message.chat.id, message_id=cq.message.message_id, text="Нажата кнопка"))
        except:
            pass

# ---------- Webhook endpoint ----------
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
    except Exception as e:
        print("Invalid JSON in request:", e)
        return Response("Bad Request", status=400)

    try:
        print("INCOMING UPDATE:", json.dumps(data, ensure_ascii=False))
    except Exception:
        print("INCOMING UPDATE: <could not serialize update>")

    try:
        update = Update.de_json(data, bot)
    except Exception as e:
        print("Failed to parse Update:", e)
        return Response("Bad Request", status=400)

    try:
        if update.message:
            text = (update.message.text or "").strip()
            cmd = ""
            args = []
            if text:
                parts = text.split()
                cmd = parts[0].split("@")[0]
                args = parts[1:]
            if cmd == "/start":
                handle_start(update)
            elif cmd == "/roll":
                expr_arg = args[0] if args else None
                handle_roll(update, expr_arg=expr_arg)
            elif cmd == "/анкета":
                handle_profile(update)
            else:
                handle_json_commands(update)
        elif update.callback_query:
            handle_callback_query(update)
        else:
            pass
    except Exception:
        print("Error handling update:", traceback.format_exc())

    return Response("OK", status=200)

# Health check
@app.route("/", methods=["GET"])
def index():
    return "OK"

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=PORT)
