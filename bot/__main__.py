from asyncio import get_event_loop, sleep as asleep, gather
from traceback import format_exc

from aiohttp import web
from pyrogram import idle

from bot import __version__, LOGGER
from bot.config import Telegram
from bot.server import web_server
from bot.telegram import StreamBot, UserBot
from bot.telegram.clients import initialize_clients
from bot.helper.media_cache import media_cache
from bot.helper.subtitle_cache import subtitle_cache

LOGGER.info(f"Media cache module loaded, enabled={media_cache.enabled}")

import uvloop
import asyncio
uvloop.install()
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

async def start_services():
    LOGGER.info(f'Initializing Surf-TG v-{__version__}')
    await asleep(1.2)
    
    # Initialize Database Indexes
    from bot.helper.database import Database
    await Database().create_indexes()
    
    await StreamBot.start()
    StreamBot.username = StreamBot.me.username
    LOGGER.info(f"Bot Client : [@{StreamBot.username}]")
    if len(Telegram.SESSION_STRING) != 0:
        await UserBot.start()
        UserBot.username = UserBot.me.username or UserBot.me.first_name or UserBot.me.id
        LOGGER.info(f"User Client : {UserBot.username}")
    
    await asleep(1.2)
    LOGGER.info("Initializing Multi Clients")
    await initialize_clients()
    
    await asleep(2)
    LOGGER.info('Initalizing Surf Web Server..')
    server = web.AppRunner(await web_server())
    LOGGER.info("Server CleanUp!")
    await server.cleanup()
    
    await asleep(2)
    LOGGER.info("Server Setup Started !")
    
    await server.setup()
    await web.TCPSite(server, '0.0.0.0', Telegram.PORT).start()

    LOGGER.info("Surf-TG Started Revolving !")
    
    # Start cache cleanup background task
    LOGGER.info(f"Checking media cache status: enabled={media_cache.enabled}")
    if media_cache.enabled:
        loop.create_task(cache_cleanup_task())
        LOGGER.info(f"Media cache enabled: max {Telegram.CACHE_MAX_SIZE_GB}GB at {media_cache.cache_dir}")
    else:
        LOGGER.info("Media cache is disabled")
    
    # Start subtitle cache cleanup background task
    loop.create_task(subtitle_cache.periodic_cleanup())
    LOGGER.info("Subtitle cache cleanup task started")
    
    await idle()


async def cache_cleanup_task():
    """Background task to cleanup cache every 30 minutes."""
    while True:
        await asleep(30 * 60)  # 30 minutes
        try:
            result = await media_cache.cleanup()
            LOGGER.info(f"Cache stats: {result['files_cached']} files, {result['cache_size_gb']:.2f}GB used")
        except Exception as e:
            LOGGER.error(f"Cache cleanup error: {e}")

async def stop_clients():
    await StreamBot.stop()
    if len(Telegram.SESSION_STRING) != 0:
        await UserBot.stop()


if __name__ == '__main__':
    try:
        loop.run_until_complete(start_services())
    except KeyboardInterrupt:
        LOGGER.info('Service Stopping...')
    except Exception:
        LOGGER.error(format_exc())
    finally:
        loop.run_until_complete(stop_clients())
        loop.stop()
