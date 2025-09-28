import os
import re
import json
import random
import sqlite3
import asyncio
import traceback
from flask import Flask, request, Response
from telegram import Bot, Update

# Try Request import from possible locations
try:
    from telegram.request import Request
except Exception:
    from telegram.utils.request import Request

# ---------- CONFIG ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 5000))
DB_PATH = "profiles.db"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set in environment variables")

# ---------- Telegram Request / pool settings ----------
REQUEST_POOL_SIZE = 40
REQUEST_POOL_TIMEOUT = 60
REQUEST_CONNECT_TIMEOUT = 10.0
REQUEST_READ_TIMEOUT = 30.0

request_session = Request(
    con_pool_size=REQUEST_POOL_SIZE,
    pool_timeout=REQUEST_POOL_TIMEOUT,
    connect_timeout=REQUEST_CONNECT_TIMEOUT,
    read_timeout=REQUEST_READ_TIMEOUT
)

OUTBOUND_SEMAPHORE = asyncio.Semaphore(20)

bot = Bot(token=BOT_TOKEN, request=request_session)
app = Flask(__name__)

# ---------- helper to run async Bot coroutines from sync code ----------
def run_coro(coro):
    async def _with_sem():
        async with OUTBOUND_SEMAPHORE:
            return await coro

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_with_sem())
    else:
        return asyncio.create_task(_with_sem())

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
