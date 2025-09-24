import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

# Токен, который ты получил у @BotFather
API_TOKEN = "8220290836:AAG7IudopuBPXYlE5hzqc7LY6zRm3h4kOkE"

# Создаём объекты
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Команда /start (бот сможет реагировать в группе)
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.reply("Привет! Я бот, и я уже умею работать в группе ✨")

# Пример реакции на любое сообщение
@dp.message()
async def echo(message: types.Message):
    # Бот не будет отвечать самому себе
    if message.from_user.id != (await bot.me()).id:
        await message.reply(f"Ты написал: {message.text}")

# --- Функция для парсинга и броска ---
def roll_dice(expr: str) -> str:
    """
    Поддерживает форматы: XdY, XdY+Z, XdY-Z
    Например: 2d6, 1d20+5, 3d10-2
    """
    match = re.match(r"(\d*)d(\d+)([+-]\d+)?", expr.strip().lower())
    if not match:
        return "❌ Неверный формат. Пример: /roll 2d6"

    num = int(match.group(1)) if match.group(1) else 1  # сколько кубов
    sides = int(match.group(2))  # граней
    mod = int(match.group(3)) if match.group(3) else 0  # модификатор

    if num > 100:
        return "⚠️ Слишком много кубов (максимум 100)."

    rolls = [random.randint(1, sides) for _ in range(num)]
    total = sum(rolls) + mod

    details = " + ".join(map(str, rolls))
    if mod:
        details += f" {'+' if mod > 0 else ''}{mod}"
    return f"🎲 {expr} → {details} = **{total}**"

# --- Обработчик команды /roll ---
@dp.message(Command("roll"))
async def cmd_roll(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) == 1:
        await message.answer("Использование: /roll XdY [+Z/-Z]\nПример: /roll 2d6+3")
    else:
        expr = args[1]
        result = roll_dice(expr)
        await message.answer(result, parse_mode="Markdown")
        
import sqlite3

conn = sqlite3.connect("data.sqlite")
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    name TEXT,
    age INTEGER
)
""")
conn.commit()


# Запуск бота
async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
