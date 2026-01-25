import asyncio
import json
import logging
import math
import mimetypes
import secrets
from aiohttp import web
from aiohttp.http_exceptions import BadStatusLine
from bot.helper.chats import get_chats, post_playlist, posts_chat, posts_db_file
from bot.helper.file_size import get_readable_file_size
from bot.helper.database import Database
from bot.helper.search import search
from bot.helper.thumbnail import get_image
from bot.telegram import work_loads, multi_clients
from aiohttp_session import get_session
from bot.config import Telegram
from bot.helper.exceptions import FIleNotFound, InvalidHash
from bot.helper.index import get_files, posts_file
from bot.server.custom_dl import ByteStreamer
from bot.server.render_template import render_page
from bot.helper.cache import rm_cache
from bot.helper.media_cache import media_cache
from bot.helper.subtitle_cache import subtitle_cache
from bot.helper.subtitle_extractor import extract_subtitle_from_telegram, get_subtitle_track_list, extract_subtitle_from_local_file

from bot.telegram import StreamBot

class_cache = {}

routes = web.RouteTableDef()
db = Database()


@routes.get('/login')
async def login_form(request):
    session = await get_session(request)
    redirect_url = session.get('redirect_url', '/')
    return web.Response(text=await render_page(None, None, route='login', redirect_url=redirect_url), content_type='text/html')


@routes.post('/login')
async def login_route(request):
    session = await get_session(request)
    if 'user' in session:
        return web.HTTPFound('/')
    data = await request.post()
    username = data.get('username')
    password = data.get('password')
    error_message = None
    if (username == Telegram.USERNAME and password == Telegram.PASSWORD) or (username == Telegram.ADMIN_USERNAME and password == Telegram.ADMIN_PASSWORD):
        session['user'] = username
        if 'redirect_url' not in session:
            session['redirect_url'] = '/'
        redirect_url = session['redirect_url']
        del session['redirect_url']
        return web.HTTPFound(redirect_url)
    else:
        error_message = "Invalid username or password"
    return web.Response(text=await render_page(None, None, route='login', msg=error_message), content_type='text/html')


@routes.get('/logout')
async def logout_route(request):
    session = await get_session(request)
    session.pop('user', None)
    return web.HTTPFound('/login')


@routes.post('/create')
async def create_route(request):
    session = await get_session(request)
    if (username := session.get('user')) != Telegram.ADMIN_USERNAME:
        return web.json_response({'msg': 'Who the hell you are'})
    data = await request.post()
    folderName = data.get('folderName')
    thumbnail = data.get('thumbnail')
    parent_dir = data.get('parent_dir')
    parent_dir = parent_dir.split('db=')[-1] if 'db=' in parent_dir else 'root'
    await db.create_folder(parent_dir, folderName, thumbnail)
    if parent_dir == 'root':
        return web.HTTPFound('/')
    else:
        return web.HTTPFound(f'/playlist?db={parent_dir}')


@routes.post('/delete')
async def delete_route(request):
    session = await get_session(request)
    if (username := session.get('user')) != Telegram.ADMIN_USERNAME:
        return web.json_response({'msg': 'Who the hell you are'})
    data = await request.json()
    id = data.get('delete_id')
    parent = data.get('parent')
    if not (success := await db.delete(id)):
        return web.HTTPInternalServerError()
    if parent == 'root':
        return web.HTTPFound('/')
    else:
        return web.HTTPFound(f'/playlist?db={parent}')


@routes.post('/edit')
async def editFolder_route(request):
    session = await get_session(request)
    if (username := session.get('user')) != Telegram.ADMIN_USERNAME:
        return web.json_response({'msg': 'Who the hell you are'})
    data = await request.post()
    folderName = data.get('folderName')
    thumbnail = data.get('thumbnail')
    id = data.get('folder_id')
    parent = data.get('parent')
    success = await db.edit(id, folderName, thumbnail)
    if not success:
        return web.HTTPInternalServerError()
    if parent == 'root':
        return web.HTTPFound('/')
    else:
        return web.HTTPFound(f'/playlist?db={parent}')


@routes.post('/edit_post')
async def editPost_route(request):
    session = await get_session(request)
    if (username := session.get('user')) != Telegram.ADMIN_USERNAME:
        return web.json_response({'msg': 'Who the hell you are'})
    data = await request.post()
    fileName = data.get('fileName')
    thumbnail = data.get('filethumbnail')
    id = data.get('file_id')
    parent = data.get('file_folder_id')
    success = await db.edit(id, fileName, thumbnail)
    if not success:
        return web.HTTPInternalServerError()
    if parent == 'root':
        return web.HTTPFound('/')
    else:
        return web.HTTPFound(f'/playlist?db={parent}')


@routes.get('/searchDbFol')
async def searchDbFolder_route(request):
    session = await get_session(request)
    if (username := session.get('user')) != Telegram.ADMIN_USERNAME:
        return web.json_response({'msg': 'Who the hell you are'})
    query = request.query.get('query', '')
    folder_names = await db.search_DbFolder(query)
    return web.json_response(folder_names)


@routes.post('/send')
async def send_route(request):
    session = await get_session(request)
    if (username := session.get('user')) != Telegram.ADMIN_USERNAME:
        return web.json_response({'msg': 'Who the hell you are'})
    data = await request.post()
    chat_id = data.get('chatId')
    chat_id = f"-100{chat_id}"
    folder_id = data.get('folderId')
    selected_ids = data.get('selectedIds')
    if not all([chat_id, folder_id, selected_ids]):
        return {'error': 'Missing required data in request'}

    formatted_entries = []
    for entry in selected_ids.split(','):
        file_id, hash, filename, size, file_type, thumbnail = entry.split('|')
        formatted_entries.append({
            'chat_id': chat_id,
            'parent_folder': folder_id,
            'file_id': file_id,
            'hash': hash,
            'name': filename,
            'size': size,
            'file_type': file_type,
            'thumbnail': thumbnail,
            'type': 'file'
        })

    await db.add_json(formatted_entries)
    if folder_id == 'root':
        return web.HTTPFound('/')
    else:
        return web.HTTPFound(f'/playlist?db={folder_id}')


@routes.get('/reload')
async def reload_route(request):
    session = await get_session(request)
    if (username := session.get('user')) != Telegram.ADMIN_USERNAME:
        return web.json_response({'msg': 'Who the hell you are'})

    chat_id = request.query.get('chatId', '')
    if chat_id == 'home':
        rm_cache()
        response = web.HTTPFound('/')
    else:
        rm_cache(f"-100{chat_id}")
        response = web.HTTPFound(f'/channel/{chat_id}')
    
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    return response


@routes.post('/config')
async def editConfig_route(request):
    session = await get_session(request)
    if (username := session.get('user')) != Telegram.ADMIN_USERNAME:
        return web.json_response({'msg': 'Who the hell you are'})
    data = await request.post()
    channel = data.get('channel')
    theme = data.get('theme')
    success = await db.update_config(theme=theme, auth_channel=channel)
    if not success:
        return web.HTTPInternalServerError()
    return web.HTTPFound('/')



@routes.get('/')
async def home_route(request):
    session = await get_session(request)
    if username := session.get('user'):
        try:
            channels = await get_chats()
            playlists = await db.get_Dbfolder()
            phtml = await posts_chat(channels)
            dhtml = await post_playlist(playlists)
            is_admin = username == Telegram.ADMIN_USERNAME
            return web.Response(text=await render_page(None, None, route='home', html=phtml, playlist=dhtml, is_admin=is_admin), content_type='text/html')
        except Exception as e:
            logging.critical(e.with_traceback(None))
            raise web.HTTPInternalServerError(text=str(e)) from e
    else:
        session['redirect_url'] = request.path_qs
        return web.HTTPFound('/login')


@routes.get('/playlist')
async def playlist_route(request):
    session = await get_session(request)
    if username := session.get('user'):
        try:
            parent_id = request.query.get('db')
            page = request.query.get('page', '1')
            playlists = await db.get_Dbfolder(parent_id, page=page)
            files = await db.get_dbFiles(parent_id, page=page)
            text = await db.get_info(parent_id)
            dhtml = await post_playlist(playlists)
            dphtml = await posts_db_file(files)
            is_admin = username == Telegram.ADMIN_USERNAME
            return web.Response(text=await render_page(parent_id, None, route='playlist', playlist=dhtml, database=dphtml, msg=text, is_admin=is_admin, page=int(page)), content_type='text/html')
        except Exception as e:
            logging.critical(e.with_traceback(None))
            raise web.HTTPInternalServerError(text=str(e)) from e
    else:
        session['redirect_url'] = request.path_qs
        return web.HTTPFound('/login')


@routes.get('/search/db/{parent}')
async def dbsearch_route(request):
    session = await get_session(request)
    if username := session.get('user'):
        parent = request.match_info['parent']
        page = request.query.get('page', '1')
        query = request.query.get('q')
        is_admin = username == Telegram.ADMIN_USERNAME
        try:
            files = await db.search_dbfiles(id=parent, page=page, query=query)
            dphtml = await posts_db_file(files)
            name = await db.get_info(parent)
            text = f"{name} - {query}"
            return web.Response(text=await render_page(parent, None, route='playlist', database=dphtml, msg=text, is_admin=is_admin, page=int(page)), content_type='text/html')
        except Exception as e:
            logging.critical(e.with_traceback(None))
            raise web.HTTPInternalServerError(text=str(e)) from e
    else:
        session['redirect_url'] = request.path_qs
        return web.HTTPFound('/login')


@routes.get('/channel/{chat_id}')
async def channel_route(request):
    session = await get_session(request)
    if username := session.get('user'):
        chat_id = request.match_info['chat_id']
        chat_id = f"-100{chat_id}"
        page = request.query.get('page', '1')
        is_admin = username == Telegram.ADMIN_USERNAME
        try:
            posts = await get_files(chat_id, page=page)
            phtml = await posts_file(posts, chat_id)
            chat = await StreamBot.get_chat(int(chat_id))
            return web.Response(text=await render_page(None, None, route='index', html=phtml, msg=chat.title, chat_id=chat_id.replace("-100", ""), is_admin=is_admin, page=int(page)), content_type='text/html')
        except Exception as e:
            logging.critical(e.with_traceback(None))
            raise web.HTTPInternalServerError(text=str(e)) from e
    else:
        session['redirect_url'] = request.path_qs
        return web.HTTPFound('/login')


@routes.get('/search/{chat_id}')
async def search_route(request):
    session = await get_session(request)
    if username := session.get('user'):
        chat_id = request.match_info['chat_id']
        chat_id = f"-100{chat_id}"
        page = request.query.get('page', '1')
        query = request.query.get('q')
        is_admin = username == Telegram.ADMIN_USERNAME
        try:
            posts = await search(chat_id, page=page, query=query)
            phtml = await posts_file(posts, chat_id)
            chat = await StreamBot.get_chat(int(chat_id))
            text = f"{chat.title} - {query}"
            return web.Response(text=await render_page(None, None, route='index', html=phtml, msg=text, chat_id=chat_id.replace("-100", ""), is_admin=is_admin, page=int(page)), content_type='text/html')
        except Exception as e:
            logging.critical(e.with_traceback(None))
            raise web.HTTPInternalServerError(text=str(e)) from e
    else:
        session['redirect_url'] = request.path_qs
        return web.HTTPFound('/login')


@routes.get('/api/thumb/{chat_id}', allow_head=True)
async def get_thumbnail(request):
    chat_id = request.match_info['chat_id']
    if not chat_id.startswith("-100"):
        chat_id = f"-100{chat_id}"
    if message_id := request.query.get('id'):
        img = await get_image(int(chat_id), int(message_id))
    else:
        img = await get_image(int(chat_id), None)
    response = web.FileResponse(img)
    response.content_type = "image/jpeg"
    return response


@routes.get('/api/subtitle/{chat_id}')
async def get_subtitle(request):
    """Extract and serve subtitle from MKV file."""
    session = await get_session(request)
    if not session.get('user'):
        return web.HTTPUnauthorized(text="Login required")
    
    try:
        chat_id = request.match_info['chat_id']
        if not chat_id.startswith("-100"):
            chat_id = f"-100{chat_id}"
        
        message_id = request.query.get('id')
        secure_hash = request.query.get('hash')
        track_index = int(request.query.get('track', '0'))
        
        if not message_id or not secure_hash:
            return web.HTTPBadRequest(text="Missing id or hash parameter")
        
        # Check cache first
        cached_path = subtitle_cache.get_cached_subtitle(
            int(chat_id), int(message_id), secure_hash, track_index
        )
        
        if cached_path:
            return web.FileResponse(
                cached_path,
                headers={
                    "Content-Type": "text/plain; charset=utf-8",
                    "Content-Disposition": "inline",
                    "Access-Control-Allow-Origin": "*",
                    "Cache-Control": "public, max-age=86400",
                    "X-Subtitle-Cache": "HIT"
                }
            )
        
        # Not cached - need to extract
        # Acquire lock to prevent duplicate extraction
        lock = await subtitle_cache.get_lock(int(chat_id), int(message_id), secure_hash)
        
        async with lock:
            # Check cache again (another request might have completed extraction)
            cached_path = subtitle_cache.get_cached_subtitle(
                int(chat_id), int(message_id), secure_hash, track_index
            )
            if cached_path:
                return web.FileResponse(
                    cached_path,
                    headers={
                        "Content-Type": "text/plain; charset=utf-8",
                        "Content-Disposition": "inline",
                        "Access-Control-Allow-Origin": "*",
                        "Cache-Control": "public, max-age=86400",
                        "X-Subtitle-Cache": "HIT"
                    }
                )
            
            # Mark as processing
            subtitle_cache.mark_processing(int(chat_id), int(message_id), secure_hash, True)
            
            try:
                # Get file properties
                index = min(work_loads, key=work_loads.get)
                faster_client = multi_clients[index]
                
                if faster_client in class_cache:
                    tg_connect = class_cache[faster_client]
                else:
                    tg_connect = ByteStreamer(faster_client)
                    class_cache[faster_client] = tg_connect
                
                file_id = await tg_connect.get_file_properties(
                    chat_id=int(chat_id), message_id=int(message_id)
                )
                
                if file_id.unique_id[:6] != secure_hash:
                    raise InvalidHash
                
                file_size = file_id.file_size
                
                # Check for cached video file first
                cached_video_path = media_cache.get_cached_path(int(chat_id), int(message_id), secure_hash)
                
                if cached_video_path:
                    logging.info(f"Subtitle extraction: Using local cached video {cached_video_path}")
                    subtitle_content = await extract_subtitle_from_local_file(cached_video_path, track_index)
                else:
                    # Extract subtitle from Telegram (Partial Download)
                    logging.info(f"Subtitle extraction: Downloading from Telegram {chat_id}/{message_id}")
                    subtitle_content = await extract_subtitle_from_telegram(
                        int(chat_id), int(message_id), secure_hash,
                        file_id, file_size, tg_connect, index, track_index
                    )
                
                if not subtitle_content:
                    return web.HTTPNotFound(text="No subtitle track found in video")
                
                # Cache the result
                await subtitle_cache.cache_subtitle(
                    int(chat_id), int(message_id), secure_hash,
                    subtitle_content, track_index
                )
                
                return web.Response(
                    body=subtitle_content,
                    content_type="text/plain",
                    charset="utf-8",
                    headers={
                        "Access-Control-Allow-Origin": "*",
                        "Cache-Control": "public, max-age=86400",
                        "X-Subtitle-Cache": "MISS"
                    }
                )
                
            finally:
                subtitle_cache.mark_processing(int(chat_id), int(message_id), secure_hash, False)
    
    except InvalidHash:
        return web.HTTPForbidden(text="Invalid hash")
    except FIleNotFound:
        return web.HTTPNotFound(text="File not found")
    except Exception as e:
        logging.error(f"Subtitle extraction error: {e}")
        return web.HTTPInternalServerError(text=str(e))


@routes.get('/api/subtitle-tracks/{chat_id}')
async def get_subtitle_tracks(request):
    """Get list of available subtitle tracks in a video."""
    session = await get_session(request)
    if not session.get('user'):
        return web.HTTPUnauthorized(text="Login required")
    
    try:
        chat_id = request.match_info['chat_id']
        if not chat_id.startswith("-100"):
            chat_id = f"-100{chat_id}"
        
        message_id = request.query.get('id')
        secure_hash = request.query.get('hash')
        
        if not message_id or not secure_hash:
            return web.HTTPBadRequest(text="Missing id or hash parameter")
        
        # Get file properties
        index = min(work_loads, key=work_loads.get)
        faster_client = multi_clients[index]
        
        if faster_client in class_cache:
            tg_connect = class_cache[faster_client]
        else:
            tg_connect = ByteStreamer(faster_client)
            class_cache[faster_client] = tg_connect
        
        file_id = await tg_connect.get_file_properties(
            chat_id=int(chat_id), message_id=int(message_id)
        )
        
        if file_id.unique_id[:6] != secure_hash:
            raise InvalidHash
        
        file_size = file_id.file_size
        
        # Get track list
        tracks = await get_subtitle_track_list(
            int(chat_id), int(message_id), secure_hash,
            file_id, file_size, tg_connect, index
        )
        
        return web.json_response({
            "tracks": tracks,
            "count": len(tracks)
        })
    
    except InvalidHash:
        return web.HTTPForbidden(text="Invalid hash")
    except FIleNotFound:
        return web.HTTPNotFound(text="File not found")
    except Exception as e:
        logging.error(f"Subtitle tracks error: {e}")
        return web.HTTPInternalServerError(text=str(e))


@routes.get('/watch/{chat_id}', allow_head=True)
async def stream_handler_watch(request: web.Request):
    session = await get_session(request)
    if username := session.get('user'):
        try:
            chat_id = request.match_info['chat_id']
            chat_id = f"-100{chat_id}"
            message_id = request.query.get('id')
            secure_hash = request.query.get('hash')
            return web.Response(text=await render_page(message_id, secure_hash, chat_id=chat_id), content_type='text/html')
        except InvalidHash as e:
            raise web.HTTPForbidden(text=e.message) from e
        except FIleNotFound as e:
            await db.delete_file(chat_id=chat_id, msg_id=message_id, hash=secure_hash)
            raise web.HTTPNotFound(text=e.message) from e
        except (AttributeError, BadStatusLine, ConnectionResetError):
            pass
        except Exception as e:
            logging.critical(e.with_traceback(None))
            raise web.HTTPInternalServerError(text=str(e)) from e
    else:
        session['redirect_url'] = request.path_qs
        return web.HTTPFound('/login')


@routes.get('/{chat_id}/{encoded_name}', allow_head=True)
async def stream_handler(request: web.Request):
    try:
        chat_id = request.match_info['chat_id']
        chat_id = f"-100{chat_id}"
        message_id = request.query.get('id')
        #name = request.match_info['encoded_name']
        secure_hash = request.query.get('hash')
        return await media_streamer(request, int(chat_id), int(message_id), secure_hash)
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message) from e
    except FIleNotFound as e:
        await db.delete_file(chat_id=chat_id, msg_id=message_id, hash=secure_hash)
        raise web.HTTPNotFound(text=e.message) from e
    except (AttributeError, BadStatusLine, ConnectionResetError):
        pass
    except asyncio.CancelledError:
        # Client disconnected - expected behavior for video seeking
        pass
    except Exception as e:
        logging.critical(e.with_traceback(None))
        raise web.HTTPInternalServerError(text=str(e))



async def stream_from_cache(request: web.Request, cached_path, file_size: int, mime_type: str, file_name: str, chat_id: int, msg_id: int, secure_hash: str):
    """Stream file from local cache."""
    range_header = request.headers.get("Range", 0)
    
    if range_header:
        from_bytes, until_bytes = range_header.replace("bytes=", "").split("-")
        from_bytes = int(from_bytes)
        until_bytes = int(until_bytes) if until_bytes else file_size - 1
    else:
        from_bytes = request.http_range.start or 0
        until_bytes = (request.http_range.stop or file_size) - 1
    
    if (until_bytes > file_size) or (from_bytes < 0) or (until_bytes < from_bytes):
        return web.Response(
            status=416,
            body="416: Range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )
    
    until_bytes = min(until_bytes, file_size - 1)
    req_length = until_bytes - from_bytes + 1
    
    # Record access for LFU scoring
    await media_cache.record_access(chat_id, msg_id, secure_hash)
    
    async def file_sender():
        chunk_size = 1024 * 1024  # 1MB chunks (Telegram max limit)
        with open(cached_path, 'rb') as f:
            f.seek(from_bytes)
            remaining = req_length
            while remaining > 0:
                chunk = f.read(min(chunk_size, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
    
    logging.info(f"Streaming from cache: {file_name}")
    
    return web.Response(
        status=206 if range_header else 200,
        body=file_sender(),
        headers={
            "Content-Type": f"{mime_type}",
            "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
            "Content-Length": str(req_length),
            "Content-Disposition": f'attachment; filename="{file_name}"',
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=31536000",
            "Connection": "keep-alive",
            "X-Content-Type-Options": "nosniff",
            "X-Cache": "HIT",
        },
    )


@routes.get('/admin/dashboard')
async def admin_dashboard(request):
    """Admin Dashboard Route"""
    session = await get_session(request)
    if (username := session.get('user')) != Telegram.ADMIN_USERNAME:
        return web.json_response({'msg': 'Unauthorized'}, status=403)
    
    import psutil
    
    # System Stats
    cpu_percent = psutil.cpu_percent()
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    
    # Cache Stats
    cache_stats = await media_cache.cleanup() # Get stats without force cleanup
    
    # Fetch Cached Files (Top 50 recently accessed)
    from pymongo import DESCENDING
    cached_files_cursor = media_cache.collection.find().sort("last_access", DESCENDING).limit(50)
    cached_files = []
    for doc in cached_files_cursor:
        cached_files.append(doc)
    
    rows_html = ""
    if not cached_files:
        rows_html = '<tr><td colspan="4" class="p-3 text-center">No files cached yet</td></tr>'
    else:
        for file in cached_files:
            fname = file.get('file_name', 'Unknown') or 'Unknown'
            # Truncate long filenames
            if len(fname) > 50:
                fname = fname[:47] + "..."
                
            fsize = get_readable_file_size(file.get('file_size', 0))
            last_access = file.get('last_access').strftime("%Y-%m-%d %H:%M:%S") if file.get('last_access') else "N/A"
            score = f"{file.get('score', 0):.1f}"
            
            rows_html += f"""
            <tr class="hover:bg-white/5 transition">
                <td class="p-3 font-medium text-gray-200">{fname}</td>
                <td class="p-3">{fsize}</td>
                <td class="p-3 text-gray-400">{last_access}</td>
                <td class="p-3 text-blue-400">{score}</td>
            </tr>
            """

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Surf-TG Admin Panel</title>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            body {{ font-family: 'Outfit', sans-serif; background: #0f172a; color: white; }}
            .glass {{ background: rgba(255, 255, 255, 0.05); backdrop-filter: blur(10px); border: 1px solid rgba(255, 255, 255, 0.1); }}
        </style>
    </head>
    <body class="p-6">
        <div class="max-w-7xl mx-auto">
            <h1 class="text-3xl font-bold mb-8 text-transparent bg-clip-text bg-gradient-to-r from-blue-400 to-purple-500">Surf-TG Admin Dashboard</h1>
            
            <div class="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
                <div class="glass rounded-xl p-6">
                    <h3 class="text-gray-400 text-sm mb-2">CPU Usage</h3>
                    <p class="text-3xl font-bold">{cpu_percent}%</p>
                    <div class="w-full bg-gray-700 h-2 rounded-full mt-2">
                        <div class="bg-blue-500 h-2 rounded-full" style="width: {cpu_percent}%"></div>
                    </div>
                </div>
                <div class="glass rounded-xl p-6">
                    <h3 class="text-gray-400 text-sm mb-2">RAM Usage</h3>
                    <p class="text-3xl font-bold">{ram.percent}%</p>
                    <p class="text-xs text-gray-400">{get_readable_file_size(ram.used)} / {get_readable_file_size(ram.total)}</p>
                </div>
                 <div class="glass rounded-xl p-6">
                    <h3 class="text-gray-400 text-sm mb-2">Disk Usage</h3>
                    <p class="text-3xl font-bold">{disk.percent}%</p>
                     <p class="text-xs text-gray-400">{get_readable_file_size(disk.used)} / {get_readable_file_size(disk.total)}</p>
                </div>
                <div class="glass rounded-xl p-6">
                    <h3 class="text-gray-400 text-sm mb-2">Media Cache</h3>
                    <p class="text-3xl font-bold">{cache_stats['cache_size_gb']:.2f} GB</p>
                    <p class="text-xs text-gray-400">{cache_stats['files_cached']} Files Cached</p>
                </div>
            </div>
            
            <div class="glass rounded-xl p-6">
                <h2 class="text-xl font-bold mb-4">Cached Files (Top 50)</h2>
                <div class="overflow-x-auto">
                    <table class="w-full text-left text-sm text-gray-400">
                        <thead class="bg-white/5 text-gray-200 uppercase">
                            <tr>
                                <th class="p-3">Filename</th>
                                <th class="p-3">Size</th>
                                <th class="p-3">Last Access</th>
                                <th class="p-3">Score</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-gray-700">
                             {rows_html}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html')

async def media_streamer(request: web.Request, chat_id: int, id: int, secure_hash: str):
    range_header = request.headers.get("Range", 0)

    index = min(work_loads, key=work_loads.get)
    faster_client = multi_clients[index]

    if Telegram.MULTI_CLIENT:
        logging.info(f"Client {index} is now serving {request.remote}")

    if faster_client in class_cache:
        tg_connect = class_cache[faster_client]
        logging.debug(f"Using cached ByteStreamer object for client {index}")
    else:
        logging.debug(f"Creating new ByteStreamer object for client {index}")
        tg_connect = ByteStreamer(faster_client)
        class_cache[faster_client] = tg_connect
    logging.debug("before calling get_file_properties")
    file_id = await tg_connect.get_file_properties(chat_id=chat_id, message_id=id)
    logging.debug("after calling get_file_properties")

    if file_id.unique_id[:6] != secure_hash:
        logging.debug(f"Invalid hash for message with ID {id}")
        raise InvalidHash

    file_size = file_id.file_size
    mime_type = file_id.mime_type
    file_name = file_id.file_name
    
    if mime_type:
        if not file_name:
            try:
                file_name = f"{secrets.token_hex(2)}.{mime_type.split('/')[1]}"
            except (IndexError, AttributeError):
                file_name = f"{secrets.token_hex(2)}.unknown"
    else:
        if file_name:
            mime_type = mimetypes.guess_type(file_id.file_name)
            if isinstance(mime_type, tuple):
                mime_type = mime_type[0] or "application/octet-stream"
        else:
            mime_type = "application/octet-stream"
            file_name = f"{secrets.token_hex(2)}.unknown"

    # Check if file is cached
    cached_path = media_cache.get_cached_path(chat_id, id, secure_hash)
    if cached_path and cached_path.exists():
        logging.info(f"Cache HIT: {file_name}")
        return await stream_from_cache(
            request, cached_path, file_size, mime_type, file_name,
            chat_id, id, secure_hash
        )
    
    # Not cached - start background download with DIFFERENT client
    if media_cache.enabled and media_cache._is_cacheable(mime_type, file_name):
        if not media_cache.is_downloading(chat_id, id, secure_hash):
            # Pick a different client for background download
            bg_index = (index + 1) % len(multi_clients)
            bg_client = multi_clients[bg_index]
            
            if bg_client in class_cache:
                bg_tg_connect = class_cache[bg_client]
            else:
                bg_tg_connect = ByteStreamer(bg_client)
                class_cache[bg_client] = bg_tg_connect
            
            # Start background download (non-blocking)
            await media_cache.start_background_download(
                chat_id, id, secure_hash, file_id, file_size,
                mime_type, file_name, bg_tg_connect, bg_index
            )
            
            # TRIGGER SMART PRE-CACHING HERE TOO
            # So if user watches Ep 1 (from Telegram), we start fetching Ep 2 immediately
            asyncio.create_task(media_cache.smart_pre_cache(chat_id, file_name))

        else:
            logging.info(f"Download already in progress: {file_name}")

    if range_header:
        from_bytes, until_bytes = range_header.replace("bytes=", "").split("-")
        from_bytes = int(from_bytes)
        until_bytes = int(until_bytes) if until_bytes else file_size - 1
    else:
        from_bytes = request.http_range.start or 0
        until_bytes = (request.http_range.stop or file_size) - 1

    if (until_bytes > file_size) or (from_bytes < 0) or (until_bytes < from_bytes):
        return web.Response(
            status=416,
            body="416: Range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    # Chunk size: Telegram API max limit is 1MB
    chunk_size = 1024 * 1024  # 1MB for all files

    until_bytes = min(until_bytes, file_size - 1)

    offset = from_bytes - (from_bytes % chunk_size)
    first_part_cut = from_bytes - offset
    last_part_cut = until_bytes % chunk_size + 1

    req_length = until_bytes - from_bytes + 1
    part_count = math.ceil(until_bytes / chunk_size) - \
        math.floor(offset / chunk_size)
    

    # Debug: Log range request info
    logging.info(f"Request: from={from_bytes}, until={until_bytes}, size={file_size}, mime={mime_type}")

    return web.Response(
        status=206 if range_header else 200,
        body=tg_connect.yield_file(
            file_id, index, offset, first_part_cut, last_part_cut, part_count, chunk_size
        ),
        headers={
            "Content-Type": f"{mime_type}",
            "Content-Range": f"bytes {from_bytes}-{until_bytes}/{file_size}",
            "Content-Length": str(req_length),
            "Content-Disposition": f'attachment; filename="{file_name}"',
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=31536000",
            "Connection": "keep-alive",
            "X-Content-Type-Options": "nosniff",
            "X-Cache": "MISS",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Range",
            "Access-Control-Expose-Headers": "Content-Range, Content-Length",
        },
    )
