import os
import re
import json
import random
import sqlite3
import asyncio
import traceback
import threading
import concurrent.futures
from flask import Flask, request, Response
from telegram import Bot, Update, InlineKeyboardMarkup, InlineKeyboardButton

# ---------- CONFIG ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 5000))
DB_PATH = "profiles.db"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set in environment variables")

# ---------- Bot ----------
bot = Bot(token=BOT_TOKEN)
app = Flask(__name__)

# ---------- Background worker loop + concurrency control ----------
OUTBOUND_SEMAPHORE = asyncio.Semaphore(10)  # tune as needed

_WORKER_LOOP = asyncio.new_event_loop()
_WORKER_THREAD = threading.Thread(target=lambda: _WORKER_LOOP.run_forever(), daemon=True)
_WORKER_THREAD.start()

def run_coro(coro, wait=False, timeout=15):
    async def _with_sem():
        async with OUTBOUND_SEMAPHORE:
            return await coro
    fut = asyncio.run_coroutine_threadsafe(_with_sem(), _WORKER_LOOP)
    if wait:
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            raise
    return fut

# ---------- Helper to reply preserving forum thread ----------
def reply(update: Update, text=None, photo=None, reply_markup=None, **kwargs):
    chat_id = update.effective_chat.id
    thread_id = None
    try:
        thread_id = getattr(update.message, "message_thread_id", None)
    except Exception:
        thread_id = None
    if photo:
        run_coro(bot.send_photo(chat_id=chat_id, photo=photo, caption=text, reply_markup=reply_markup, message_thread_id=thread_id, **kwargs))
    else:
        run_coro(bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, message_thread_id=thread_id, **kwargs))

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
        "bio TEXT, "
        "last_photo_time INTEGER"
        ")"
    )
    conn.commit()
    conn.close()

def get_profile(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id,username,name,age,role,photo_id,inventory,stats,exp,bio FROM profiles WHERE user_id = ?", (user_id,))
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
        "INSERT INTO profiles(user_id, username, name, age, role, photo_id, inventory, stats, exp, bio, last_photo_time) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET "
        "username=excluded.username, name=excluded.name, age=excluded.age, role=excluded.role, "
        "photo_id=excluded.photo_id, inventory=excluded.inventory, stats=excluded.stats, exp=excluded.exp, bio=excluded.bio, last_photo_time=excluded.last_photo_time"
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
            data.get("bio", ""),
            data.get("last_photo_time")
        )
    )
    conn.commit()
    conn.close()

def update_photo(user_id, username, photo_id, ts=None):
    prof = get_profile(user_id) or {}
    prof.update({
        "user_id": user_id,
        "username": username,
        "name": prof.get("name"),
        "age": prof.get("age"),
        "role": prof.get("role"),
        "photo_id": photo_id,
        "inventory": prof.get("inventory", []),
        "stats": prof.get("stats", {}),
        "exp": prof.get("exp", 0),
        "bio": prof.get("bio", ""),
        "last_photo_time": ts or 0
    })
    save_profile(prof)

# ---------- Utilities: parsing setanketa text ----------
def parse_setanketa_text(text: str):
    # Accept lines like "Имя: Лира" or "Name: Lira"
    keys = {
        "name": ["имя", "name"],
        "age": ["возраст", "age"],
        "role": ["роль", "role"],
        "bio": ["биография", "bio", "предыстория"],
        "inventory": ["инвентарь", "inventory"],
        "stats": ["характеристики", "stats", "характ"],
        "exp": ["опыт", "exp", "experience"]
    }
    out = {}
    # Split by lines; ignore first line if it's the command
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines and re.match(r"^/?setanketa\b", lines[0], flags=re.I):
        lines = lines[1:]
    # join multi-line values by detecting key prefix
    curr_key = None
    for ln in lines:
        m = re.match(r"^([^\:]+)\s*:\s*(.*)$", ln)
        if m:
            k_raw = m.group(1).strip().lower()
            val = m.group(2).strip()
            found = None
            for k, aliases in keys.items():
                if k_raw in aliases:
                    found = k
                    break
            if found:
                curr_key = found
                out[curr_key] = val
                continue
        # If not key:value, append to previous key (multiline bio)
        if curr_key:
            out[curr_key] = out.get(curr_key, "") + "\n" + ln
    # Post-process inventory and stats into structures
    if "inventory" in out:
        items = re.split(r"[,\;]| и ", out["inventory"])
        out["inventory"] = [i.strip() for i in items if i.strip()]
    if "stats" in out:
        # Accept "Сила=8, Ловкость=10" or "Сила:8"
        stats = {}
        parts = re.split(r"[,\;]", out["stats"])
        for p in parts:
            if not p.strip():
                continue
            m = re.match(r"^\s*([^:=\d]+)\s*[:=]?\s*([-\d]+)\s*$", p.strip())
            if m:
                stats[m.group(1).strip()] = int(m.group(2))
            else:
                # fallback: split by whitespace
                kv = p.strip().split()
                if len(kv) >= 2 and kv[-1].isdigit():
                    stats[" ".join(kv[:-1])] = int(kv[-1])
        out["stats"] = stats
    if "exp" in out:
        try:
            out["exp"] = int(re.findall(r"\d+", out["exp"])[0])
        except:
            out["exp"] = 0
    return out

# ---------- Buttons / markups ----------
def profile_buttons(user_id):
    kb = [
        [InlineKeyboardButton("🧭 Опыт", callback_data=f"exp:{user_id}"),
         InlineKeyboardButton("📊 Характеристики", callback_data=f"stats:{user_id}")],
        [InlineKeyboardButton("📜 Биография", callback_data=f"bio:{user_id}"),
         InlineKeyboardButton("🎒 Инвентарь", callback_data=f"inv:{user_id}")]
    ]
    return InlineKeyboardMarkup(kb)

def back_button(user_id):
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data=f"back:{user_id}")]])

# ---------- Display helpers ----------
def short_profile_text(prof):
    name = prof.get("name") or "-"
    age = prof.get("age") or "-"
    role = prof.get("role") or "-"
    return f"Имя: {name}\nВозраст: {age}\nРоль: {role}"

def exp_text(prof):
    return f"Опыт: {prof.get('exp', 0)}"

def stats_text(prof):
    stats = prof.get("stats") or {}
    if not stats:
        return "Характеристики: —"
    return "Характеристики:\n" + "\n".join(f"{k}: {v}" for k, v in stats.items())

def bio_text(prof):
    return f"Биография:\n{prof.get('bio') or '-'}"

def inv_text(prof):
    inv = prof.get("inventory") or []
    if not inv:
        return "Инвентарь: пусто"
    return "Инвентарь:\n" + "\n".join(f"- {i}" for i in inv)

# ---------- Command handlers ----------
def handle_start(update: Update):
    try:
        reply(update, text=(
            "Привет! Я RPG-бот.\n"
            "/anketa — показать свою анкету или чужую: /anketa @username\n"
            "/setanketa — создать или обновить свою анкету (см. формат)\n\n"
            "Формат /setanketa (пример):\n"
            "/setanketa\n"
            "Имя: Лира\n"
            "Возраст: 23\n"
            "Роль: Следопыт\n"
            "Биография: Родилась в лесах...\n"
            "Инвентарь: Лук, зелье, карта\n"
            "Характеристики: Сила=8, Ловкость=10\n"
            "Опыт: 120"
        ))
    except Exception:
        print("Error in handle_start:", traceback.format_exc())

def handle_setanketa(update: Update):
    try:
        user = update.effective_user
        text = (update.message.text or "")
        # If message contains photo, it may be captioned; but photo handler separate
        parsed = parse_setanketa_text(text)
        prof = get_profile(user.id) or {"user_id": user.id, "username": user.username}
        # Merge parsed fields
        if "name" in parsed:
            prof["name"] = parsed["name"]
        if "age" in parsed:
            prof["age"] = parsed["age"]
        if "role" in parsed:
            prof["role"] = parsed["role"]
        if "bio" in parsed:
            prof["bio"] = parsed["bio"]
        if "inventory" in parsed:
            prof["inventory"] = parsed["inventory"]
        if "stats" in parsed:
            prof["stats"] = parsed["stats"]
        if "exp" in parsed:
            prof["exp"] = parsed["exp"]
        prof["user_id"] = user.id
        prof["username"] = user.username
        # Preserve existing photo_id if any
        old = get_profile(user.id)
        if old and old.get("photo_id") and not prof.get("photo_id"):
            prof["photo_id"] = old.get("photo_id")
        # Save timestamp as 0 if not present; last_photo_time handled by photo handler
        prof["last_photo_time"] = old.get("last_photo_time") if old else 0
        save_profile(prof)
        reply(update, text="Анкета обновлена ✅")
    except Exception:
        print("Error in handle_setanketa:", traceback.format_exc())
        reply(update, text="Произошла ошибка при сохранении анкеты.")

def handle_photo(update: Update):
    try:
        # Save user's last sent photo (largest file_id)
        user = update.effective_user
        photos = update.message.photo or []
        if not photos:
            return
        # largest is last
        photo = photos[-1]
        photo_id = photo.file_id
        ts = int(update.message.date.timestamp()) if update.message.date else 0
        update_photo(user.id, user.username, photo_id, ts)
        reply(update, text="Фото сохранено для анкеты ✅")
    except Exception:
        print("Error in handle_photo:", traceback.format_exc())

def handle_anketa(update: Update):
    try:
        text = (update.message.text or "").strip()
        parts = text.split()
        target_prof = None
        if len(parts) > 1:
            target = parts[1].lstrip("@")
            # find by username
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT user_id FROM profiles WHERE username = ?", (target,))
            r = cur.fetchone()
            conn.close()
            if r:
                target_prof = get_profile(r[0])
        else:
            user = update.effective_user
            target_prof = get_profile(user.id)
        if not target_prof:
            reply(update, text="Анкета не найдена. Создай свою с помощью /setanketa и отправки фото.")
            return
        text_out = short_profile_text(target_prof)
        photo_id = target_prof.get("photo_id")
        reply(update, text=text_out, photo=photo_id, reply_markup=profile_buttons(target_prof["user_id"]))
    except Exception:
        print("Error in handle_anketa:", traceback.format_exc())
        reply(update, text="Ошибка при показе анкеты.")

# ---------- Callback handling ----------
def handle_callback_query(update: Update):
    cq = update.callback_query
    if not cq:
        return
    try:
        # Acknowledge quickly
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
            except Exception:
                pass
            return
        try:
            if kind == "bio":
                run_coro(bot.edit_message_caption(chat_id=cq.message.chat.id, message_id=cq.message.message_id, caption=bio_text(prof), reply_markup=back_button(uid)))
            elif kind == "inv":
                run_coro(bot.edit_message_caption(chat_id=cq.message.chat.id, message_id=cq.message.message_id, caption=inv_text(prof), reply_markup=back_button(uid)))
            elif kind == "stats":
                run_coro(bot.edit_message_caption(chat_id=cq.message.chat.id, message_id=cq.message.message_id, caption=stats_text(prof), reply_markup=back_button(uid)))
            elif kind == "exp":
                run_coro(bot.edit_message_caption(chat_id=cq.message.chat.id, message_id=cq.message.message_id, caption=exp_text(prof), reply_markup=back_button(uid)))
            elif kind == "back":
                # show short profile again; if message had photo, edit caption; else edit text
                if cq.message.photo:
                    run_coro(bot.edit_message_caption(chat_id=cq.message.chat.id, message_id=cq.message.message_id, caption=short_profile_text(prof), reply_markup=profile_buttons(uid)))
                else:
                    run_coro(bot.edit_message_text(chat_id=cq.message.chat.id, message_id=cq.message.message_id, text=short
