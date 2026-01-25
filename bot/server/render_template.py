import re
import time
from aiofiles import open as aiopen
from os import path as ospath

from bot import LOGGER
from bot.config import Telegram
from bot.helper.database import Database
from bot.helper.exceptions import InvalidHash
from bot.helper.file_size import get_readable_file_size
from bot.server.file_properties import get_file_ids
from bot.telegram import StreamBot

db = Database()

admin_block = """
                    <style>
                        .admin-only {
                            display: none;
                        }
                    </style>"""

hide_channel = """
                    <style>
                        .hide-channel {
                            display: none;
                        }
                    </style>"""


_theme_cache = {'value': None, 'time': 0}

async def render_page(id, secure_hash, is_admin=False, html='', playlist='', database='', route='', redirect_url='', msg='', chat_id='', page=1):
    global _theme_cache
    if _theme_cache['value'] and (time.time() - _theme_cache['time'] < 60):
        theme = _theme_cache['value']
    else:
        theme = await db.get_variable('theme')
        if theme:
            _theme_cache = {'value': theme, 'time': time.time()}
    if theme is None or theme == '':
        theme = Telegram.THEME
    tpath = ospath.join('bot', 'server', 'template')
    
    # Pagination Logic
    prev_btn = ""
    next_btn = ""
    
    # We construct the base URL query args (like ?q=...) manually if needed, 
    # but simplest is to just append &page=... provided the original URL scheme is consistent.
    # However, since we don't know the full current URL path here easily without request context,
    # we assume standard "?page=" or "&page=" appending logic based on route.
    # Actually, simpler: "Next" button just adds ?page=page+1, "Prev" ?page=page-1. 
    # But we need to keep existing params like 'q' (query). 
    # For now, let's assume simple pagination without complex query preservation unless explicit.
    # The routes calling this (dbsearch_route, search_route) should ideally handle this, 
    # but here we can just make relative links potentially? 
    # No, let's simpler:
    # If page > 1: show Prev
    # Always show Next (let user hit empty page if end, as requested "limit 50").
    
    if page > 1:
        prev_page = page - 1
        prev_url = f"?page={prev_page}"
        # If 'msg' contains " - ", it likely came from search, so we might need to append &q=... 
        # But `render_page` doesn't strictly know the 'q'. 
        # A robust solution needs 'q' passed to render_page.
        # For this quick fix, we rely on the browser/user re-appending/routes handling it, 
        # or we just use simple ?page=... which might lose 'q' if not careful.
        # Wait, if I am on /search/chat?q=foo&page=2, clicking "?page=3" works relative.
        # So providing just "?page={p}" works if the base action is GET.
        # BUT anchor tags replace the query string if not carefully constructed.
        # Better: use JS or backend full URL construction.
        # Let's try simple relative append/replace. 
        # Actually standard href="?page=2" REPLACES the query string completely.
        # To preserve 'q', we'd need to know it. 
        # Let's hope the user is fine with basic pagination for now or use Javascript to update `page` param.
        # Javascript solution is safest: onclick="updatePage({page-1})"
        
        prev_btn = f"""
        <a href="javascript:void(0)" onclick="updateParam('page', {prev_page})" 
           class="flex items-center gap-1 px-4 py-2 rounded-full bg-white/5 hover:bg-primary/20 text-white transition-colors border border-white/10 hover:border-primary/30">
            <span class="material-symbols-outlined text-[18px]">arrow_back</span>
            <span class="text-sm font-medium">Prev</span>
        </a>
        <script>
            function updateParam(key, value) {{
                const url = new URL(window.location.href);
                url.searchParams.set(key, value);
                window.location.href = url.toString();
            }}
        </script>
        """

    next_page = page + 1
    next_btn = f"""
    <a href="javascript:void(0)" onclick="updateParam('page', {next_page})" 
       class="flex items-center gap-1 px-4 py-2 rounded-full bg-white/5 hover:bg-primary/20 text-white transition-colors border border-white/10 hover:border-primary/30">
        <span class="text-sm font-medium">Next</span>
        <span class="material-symbols-outlined text-[18px]">arrow_forward</span>
    </a>
    """
    # Note: verify updateParam doesn't duplicate if added twice (it won't because script ID or simple re-def is fine in HTML body)
    
    if route == 'login':
        async with aiopen(ospath.join(tpath, 'login.html'), 'r') as f:
            html = (await f.read()).replace("<!-- Error -->", msg or '').replace("<!-- Theme -->", theme.lower()).replace("<!-- RedirectURL -->", redirect_url)
    elif route == 'home':
        async with aiopen(ospath.join(tpath, 'home.html'), 'r') as f:
            html = (await f.read()).replace("<!-- Print -->", html).replace("<!-- Theme -->", theme.lower()).replace("<!-- Playlist -->", playlist)
            if not is_admin:
                html += admin_block
                if Telegram.HIDE_CHANNEL:
                    html += hide_channel
    elif route == 'playlist':
        async with aiopen(ospath.join(tpath, 'playlist.html'), 'r') as f:
            html = (await f.read()).replace("<!-- Theme -->", theme.lower()).replace("<!-- Playlist -->", playlist).replace("<!-- Database -->", database).replace("<!-- Title -->", msg).replace("<!-- Parent_id -->", id).replace("<!-- Prev -->", prev_btn).replace("<!-- Next -->", next_btn)
            if not is_admin:
                html += admin_block
    elif route == 'index':
        async with aiopen(ospath.join(tpath, 'index.html'), 'r') as f:
            html = (await f.read()).replace("<!-- Print -->", html).replace("<!-- Theme -->", theme.lower()).replace("<!-- Title -->", msg).replace("<!-- Chat_id -->", chat_id).replace("<!-- Prev -->", prev_btn).replace("<!-- Next -->", next_btn)
            if not is_admin:
                html += admin_block
    else:
        file_data = await get_file_ids(StreamBot, chat_id=int(chat_id), message_id=int(id))
        if file_data.unique_id[:6] != secure_hash:
            LOGGER.info('Link hash: %s - %s', secure_hash,
                        file_data.unique_id[:6])
            LOGGER.info('Invalid hash for message with - ID %s', id)
            raise InvalidHash
        filename, tag, size = file_data.file_name, file_data.mime_type.split(
            '/')[0].strip(), get_readable_file_size(file_data.file_size)
        if filename is None:
            filename = "Proper Filename is Missing"
        filename = re.sub(r'[,|_\',]', ' ', filename)
        
        # Series/Part Detection & Playlist Generation
        playlist_html = ""
        match = re.search(r'(.*)[ ._]part(\d+)', filename, re.IGNORECASE)
        if match:
            series_name = match.group(1).strip()
            try:
                # Search for siblings (limit 50 should allow up to ~50 parts)
                # We need to import search - ensuring it's available
                from bot.helper.search import search 
                search_results = await search(chat_id, series_name, 1)
                
                # Improved Deduplication: Group by Part Number
                all_parts = []
                for post in search_results:
                    # Verify it matches the series name and has a part number
                    p_match = re.search(r'(.*)[ ._]part(\d+)', post['title'], re.IGNORECASE)
                    if p_match and p_match.group(1).strip().lower() == series_name.lower():
                        post['part_number'] = int(p_match.group(2))
                        all_parts.append(post)
                
                # Filter duplicates - prefer the one currently playing if it exists
                parts_map = {}
                for post in all_parts:
                    p_num = post['part_number']
                    if p_num not in parts_map:
                        parts_map[p_num] = []
                    parts_map[p_num].append(post)
                
                parts = []
                current_msg_id = str(id)
                
                for p_num, posts in parts_map.items():
                    selected_post = posts[0] # Default to first
                    # If any post in this group is the current one, force select it
                    for p in posts:
                        if str(p['msg_id']) == current_msg_id:
                            selected_post = p
                            break
                    parts.append(selected_post)
                
                # Sort by part number
                parts.sort(key=lambda x: x['part_number'])
                
                if len(parts) > 1:
                    list_items = ""
                    for part in parts:
                        is_active = str(part['msg_id']) == str(id)
                        active_class = "bg-primary/20 ring-1 ring-primary/50" if is_active else ""
                        active_text = "text-primary" if is_active else "text-white"
                        
                        # Thumbnail URL
                        thumb_url = f"/api/thumb/{str(chat_id).replace('-100', '')}?id={part['msg_id']}"
                        
                        list_items += f"""
                        <a href="/watch/{str(chat_id).replace("-100", "")}?id={part['msg_id']}&hash={part['hash']}" class="group flex items-center gap-3 p-2 rounded-lg hover:bg-white/10 transition-colors {active_class}">
                           <div class="relative w-16 h-10 shrink-0 rounded overflow-hidden bg-white/5 border border-white/10 group-hover:border-primary/50 transition-colors">
                               <img src="{thumb_url}" class="w-full h-full object-cover" loading="lazy">
                               <div class="absolute inset-0 flex items-center justify-center bg-black/50 text-xs font-bold text-white backdrop-blur-[1px]">
                                   {part['part_number']}
                               </div>
                           </div>
                           <div class="overflow-hidden">
                               <p class="text-sm font-medium {active_text} truncate" title="{part['title']}">{part['title']}</p>
                               <p class="text-[10px] text-gray-500">{part['size']}</p>
                           </div>
                        </a>
                        """
                    
                    playlist_html = f"""
                    <div class="glass-panel p-4 rounded-xl mb-6 animate-enter">
                        <h3 class="text-primary font-bold mb-3 flex items-center gap-2">
                            <span class="material-symbols-outlined">playlist_play</span>
                            Series Parts
                        </h3>
                        <div class="flex flex-col gap-2 max-h-96 overflow-y-auto pr-2 custom-scrollbar">
                           {list_items}
                        </div>
                    </div>
                    """
            except Exception as e:
                LOGGER.error(f"Error generating playlist: {e}")

        if tag == 'video':
            async with aiopen(ospath.join(tpath, 'video.html')) as r:
                poster = f"/api/thumb/{chat_id}?id={id}"
                html = (await r.read()).replace('<!-- Filename -->', filename).replace("<!-- Theme -->", theme.lower()).replace('<!-- Poster -->', poster).replace('<!-- Size -->', size).replace('<!-- Username -->', StreamBot.me.username).replace('<!-- Playlist -->', playlist_html).replace('<!-- ID -->', str(id))
        else:
            async with aiopen(ospath.join(tpath, 'dl.html')) as r:
                html = (await r.read()).replace('<!-- Filename -->', filename).replace("<!-- Theme -->", theme.lower()).replace('<!-- Size -->', size)
    return html