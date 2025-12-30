from asyncio import gather, create_task
from bot.helper.database import Database
from bot.telegram import StreamBot
from bot.config import Telegram

db = Database()

async def get_chats():
    AUTH_CHANNEL = await db.get_variable('auth_channel')
    if AUTH_CHANNEL is None or AUTH_CHANNEL.strip() == '':
        AUTH_CHANNEL = Telegram.AUTH_CHANNEL
    else:
        AUTH_CHANNEL = [channel.strip() for channel in AUTH_CHANNEL.split(",")]
    
    return [{"chat-id": chat.id, "title": chat.title or chat.first_name, "type": chat.type.name} for chat in await gather(*[create_task(StreamBot.get_chat(int(channel_id))) for channel_id in AUTH_CHANNEL])]


async def posts_chat(channels):
    phtml = """
    <div class="glass-panel text-center p-6 rounded-2xl hover:bg-white/5 transition-all duration-300 group relative overflow-hidden">
        <a href="/channel/{cid}" class="block">
             <div class="w-24 h-24 mx-auto mb-4 relative rounded-full p-1 bg-gradient-to-br from-primary to-purple-600 shadow-xl shadow-primary/20 group-hover:shadow-primary/40 transition-shadow">
                 <img src="{img}" loading="lazy" class="w-full h-full rounded-full object-cover border-2 border-background-dark/50" alt="{title}">
             </div>
             <h3 class="text-white font-bold text-lg mb-1 truncate px-2">{title}</h3>
             <span class="inline-block px-3 py-1 rounded-full bg-white/5 text-primary text-xs font-semibold uppercase tracking-wider border border-white/5">
                {ctype}
             </span>
        </a>
    </div>
"""
    return ''.join(phtml.format(cid=str(channel["chat-id"]).replace("-100", ""), img=f"/api/thumb/{channel['chat-id']}", title=channel["title"], ctype=channel['type']) for channel in channels)


async def post_playlist(playlists):
    dhtml = """
    <div class="glass-panel text-center p-6 rounded-2xl hover:bg-white/5 transition-all duration-300 group relative">
        <a href="" onclick="openEditPopupForm(event, '{img}', '{ctype}', '{cid}', '{title}')"
            class="admin-only absolute top-3 right-3 p-2 rounded-lg bg-black/40 text-gray-400 hover:text-white hover:bg-primary z-10 transition-colors" 
            data-bs-toggle="modal" data-bs-target="#editFolderModal">
            <span class="material-symbols-outlined text-sm">edit</span>
        </a>

        <a href="/playlist?db={cid}" class="block">
             <div class="w-24 h-24 mx-auto mb-4 relative rounded-2xl p-1 bg-gradient-to-br from-yellow-500/80 to-orange-600/80 shadow-lg group-hover:scale-105 transition-transform">
                 <img src="{img}" loading="lazy" class="w-full h-full rounded-xl object-cover shadow-inner" alt="{title}">
             </div>
             
             <h3 class="text-white font-bold text-lg mb-1 truncate px-2">{title}</h3>
             <span class="inline-flex items-center gap-1 px-3 py-1 rounded-full bg-white/5 text-yellow-500 text-xs font-medium border border-white/5">
                <span class="material-symbols-outlined text-[14px]">folder</span> Folder
             </span>
        </a>
    </div>
    """

    return ''.join(dhtml.format(cid=playlist["_id"], img=playlist["thumbnail"], title=playlist["name"], ctype=playlist['parent_folder']) for playlist in playlists)


async def posts_db_file(posts):
    import re
    
    # Grouping Logic
    grouped_posts = []
    series_map = {}
    
    for post in posts:
        # Regex to detect part files: Name.part01.mp4 or Name part 1.mkv
        match = re.search(r'(.*)[ ._]part(\d+)$', post['name'], re.IGNORECASE)
        
        if match:
            series_name = match.group(1).strip()
            part_number = int(match.group(2))
            
            if series_name not in series_map:
                series_map[series_name] = []
            
            # Store part with its number
            post['part_number'] = part_number
            series_map[series_name].append(post)
        else:
            grouped_posts.append(post)
            
    # Process Grouped Series
    for series_name, parts in series_map.items():
        # Sort by part number
        parts.sort(key=lambda x: x.get('part_number', 0))
        
        # Take the first part as representative
        if parts:
            representative = parts[0]
            representative['is_series'] = True
            representative['parts_count'] = len(parts)
            representative['name'] = series_name # Use base name as title
            grouped_posts.append(representative)
    
    # Sorting (Optional: maintain original order or sort by name)
    # The incoming 'posts' might already be sorted by date. We generally append new series at the end or keep flow.
    # For DB file listing, preserving generic order or sorting by name might be best.
    # Let's simple append for now to not break unexpected pagination orders too much, 
    # but strictly speaking we're mixing non-parts and series.
    # If we want to sort by Name:
    # grouped_posts.sort(key=lambda x: x['name'])
    
    phtml = """
    <div class="glass-panel rounded-xl overflow-hidden hover:shadow-xl hover:shadow-primary/10 transition-all duration-300 group relative">
        <a href="/watch/{chat_id}?id={id}&hash={hash}" class="block relative aspect-video bg-gray-900 overflow-hidden cursor-pointer">
             <img src="{img}" loading="lazy" class="w-full h-full object-cover transition-transform duration-700 group-hover:scale-105" alt="{title}">
             
             <!-- Gradient Overlay -->
             <div class="absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-transparent opacity-60 group-hover:opacity-40 transition-opacity"></div>
             
             <!-- Centered Play Icon on Hover -->
             <div class="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-all duration-300 scale-75 group-hover:scale-100">
                <div class="w-12 h-12 bg-primary/90 rounded-full flex items-center justify-center backdrop-blur-sm shadow-lg shadow-black/50">
                    <span class="material-symbols-outlined text-white text-3xl ml-0.5">play_arrow</span>
                </div>
             </div>
             
             <div class="absolute bottom-2 right-2 bg-black/60 backdrop-blur-md px-2 py-0.5 rounded text-[10px] font-bold text-white tracking-wide">
                {size}
             </div>

            <a href=""
                onclick="openPostEditPopupForm(event, '{img}', '{type}', '{size}', '{title}', '{cid}', '{ctype}')"
                class="admin-only absolute top-2 right-2 p-1.5 rounded bg-black/50 text-white hover:bg-primary transition-colors z-20" 
                data-bs-toggle="modal" data-bs-target="#editModal">
                <span class="material-symbols-outlined text-[16px]">edit</span>
            </a>
        </a>
        
        <div class="p-3">
            <a href="/watch/{chat_id}?id={id}&hash={hash}" class="block group-hover:text-primary transition-colors duration-200">
                <h3 class="text-white font-medium text-sm line-clamp-2 leading-snug min-h-[2.5rem]" title="{title}">{title}</h3>
            </a>
            
            <div class="flex items-center justify-between mt-3 px-1">
                <span class="px-2 py-0.5 rounded bg-white/5 text-gray-400 text-[10px] uppercase font-bold tracking-wider border border-white/5 {badge_color}">
                    {type_display}
                </span>
            </div>
        </div>
    </div>
"""
    html_output = ''
    for post in grouped_posts:
        if post.get('is_series'):
            type_display = f"SERIES ({post['parts_count']} Parts)"
            badge_color = "text-yellow-400 border-yellow-400/20 bg-yellow-400/10"
        else:
            type_display = post.get('file_type', 'FILE')
            badge_color = ""
            
        html_output += phtml.format(
            cid=post["_id"], 
            chat_id=str(post["chat_id"]).replace("-100", ""), 
            id=post["file_id"], 
            img=post["thumbnail"], 
            title=post["name"], 
            hash=post["hash"], 
            size=post['size'], 
            type=post['file_type'], 
            ctype=post["parent_folder"],
            type_display=type_display,
            badge_color=badge_color
        )
    return html_output
