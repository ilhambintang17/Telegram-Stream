from aiohttp.web import Application
from cryptography.fernet import Fernet
from aiohttp_session import setup
from aiohttp_session.cookie_storage import EncryptedCookieStorage
from pathlib import Path

from bot.server.stream_routes import routes

secret_key = Fernet.generate_key()

# Static directory for SubtitlesOctopus and other assets
STATIC_DIR = Path(__file__).parent / "static"

async def web_server():
    web_app = Application(client_max_size=30000000)
    setup(web_app, EncryptedCookieStorage(Fernet(secret_key)))
    web_app.add_routes(routes)
    
    # Add static file serving for SubtitlesOctopus WASM files
    if STATIC_DIR.exists():
        from aiohttp import web
        web_app.router.add_static('/static/', STATIC_DIR, follow_symlinks=True)
    
    return web_app
