from fastapi import FastAPI
import asyncio
from aiogram import Bot, Dispatcher, types
import sqlite3
import random
import os

TOKEN = "8220290836:AAG7IudopuBPXYlE5hzqc7LY6zRm3h4kOkE"

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- Setup SQLite ---
DB_PATH = "data.sqlite"
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute("""CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    name TEXT,
    age INTEGER
)""")
conn.commit()

# --- Telegram Handlers ---
@dp.message(commands=["start"])
async def start(message: types.Message):
    await message.answer("Привет! Я бот с кубиком и анкетой!")

@dp.message(commands=["roll"])
async def roll(message: types.Message):
    value = random.randint(1, 6)
    await message.answer(f"🎲 Выпало: {value}")

@dp.message(commands=["profile"])
async def profile(message: types.Message):
    cursor.execute("SELECT name, age FROM users WHERE user_id=?", (message.from_user.id,))
    user = cursor.fetchone()
    if user:
        await message.answer(f"Ваш профиль:\nИмя: {user[0]}\nВозраст: {user[1]}")
    else:
        await message.answer("Профиль не найден. Используй /setprofile")

@dp.message(commands=["setprofile"])
async def setprofile(message: types.Message):
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("Используй: /setprofile <Имя> <Возраст>")
        return
    name, age = args[1], args[2]
    cursor.execute(
        "INSERT OR REPLACE INTO users (user_id, name, age) VALUES (?, ?, ?)",
        (message.from_user.id, name, age)
    )
    conn.commit()
    await message.answer("Профиль сохранён!")

# --- FastAPI для ping ---
app = FastAPI()

@app.get("/")
async def ping():
    return {"status": "Bot is running"}

# --- Telegram Polling ---
async def run_bot():
    await dp.start_polling(bot)

# --- Main ---
if __name__ == "__main__":
    import uvicorn
    loop = asyncio.get_event_loop()
    loop.create_task(run_bot())
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))