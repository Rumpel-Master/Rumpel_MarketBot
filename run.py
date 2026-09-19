import asyncio
import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv
import os

import bot as bot_module
from admin_panel import app

load_dotenv()

async def run_bot():
    if not bot_module.BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN в .env")
    bot_module.init_db()
    bot_module.seed()
    b = Bot(bot_module.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(bot_module.router)
    await b.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(b)

async def run_web():
    config = uvicorn.Config(app, host=os.getenv("WEB_HOST", "127.0.0.1"), port=int(os.getenv("WEB_PORT", "8080")), log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

async def main():
    await asyncio.gather(run_bot(), run_web())

if __name__ == "__main__":
    asyncio.run(main())
