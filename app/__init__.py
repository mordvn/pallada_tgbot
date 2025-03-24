import asyncio
import logging
import os
from typing import NoReturn

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from routers.user import user_router
from services.search_results import fetch_database_sync
from services.notification_processor import NotificationManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

if not (token := os.getenv('TG_BOT_TOKEN')):
    raise ValueError("TG_BOT_TOKEN environment variable is not set")

bot = Bot(token=token)

storage = MemoryStorage()
dp = Dispatcher(storage=storage)

async def init_dispatcher() -> None:
    """
    Initialize dispatcher with required data and routers.
    """
    dp['search_results'] = fetch_database_sync("cache/search_results.json")
    dp['notifyer'] = NotificationManager()

    dp.include_router(user_router)

async def start_bot() -> None:
    """
    Start the bot by initializing the dispatcher and starting polling.
    """
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Starting bot polling...")
    await dp.start_polling(bot)

async def main() -> NoReturn:
    """
    Main application entry point.
    Initializes the dispatcher and starts the bot.
    """
    try:
        await init_dispatcher()
        await start_bot()
    except Exception as e:
        logger.error(f"Error occurred: {e}")
        raise

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot stopped due to error: {e}")
