from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from fastapi import FastAPI
from mangum import Mangum  # Для совместимости с Render, если нужен HTTP адаптер
import asyncio
import os

# Токен бота
BOT_TOKEN = os.getenv("8220290836:AAG7IudopuBPXYlE5hzqc7LY6zRm3h4kOkE")  # Установи его в Render environment variables

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# FastAPI для проверки, что бот работает
app = FastAPI()

@app.get("/ping")
async def ping():
    return {"status": "ok"}

# Хэндлер команды /start
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    await message.answer("Привет! Бот запущен и работает!")

# Фоновая функция запуска бота
async def main():
    # Запуск long-polling
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

# Запуск asyncio loop для Render
if __name__ == "__main__":
    asyncio.run(main())
