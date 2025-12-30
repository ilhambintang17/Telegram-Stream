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
    phtml = """
    <div class="glass-panel rounded-xl overflow-hidden hover:scale-[1.02] transition-transform duration-300 group">
        <div class="relative aspect-video bg-gray-900">
             <img src="{img}" loading="lazy" class="w-full h-full object-cover transition-transform duration-700 group-hover:scale-110" alt="{title}">
             <div class="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300"></div>
             
             <div class="absolute bottom-2 right-2 bg-black/60 backdrop-blur-md px-2 py-1 rounded text-xs font-medium text-white">
                {size}
             </div>

            <a href=""
                onclick="openPostEditPopupForm(event, '{img}', '{type}', '{size}', '{title}', '{cid}', '{ctype}')"
                class="admin-only absolute top-2 right-2 p-1.5 rounded bg-black/50 text-white hover:bg-primary transition-colors z-20" 
                data-bs-toggle="modal" data-bs-target="#editModal">
                <span class="material-symbols-outlined text-[16px]">edit</span>
            </a>
        </div>
        
        <div class="p-4">
            <h3 class="text-white font-semibold text-sm line-clamp-2 leading-snug mb-2 min-h-[2.5rem]" title="{title}">{title}</h3>
            
            <div class="flex items-center justify-between gap-2 mt-3">
                <span class="px-2 py-0.5 rounded bg-primary/20 text-primary text-[10px] uppercase font-bold tracking-wider border border-primary/20">
                    {type}
                </span>
                
                <a href="/watch/{chat_id}?id={id}&hash={hash}" class="flex items-center gap-1 text-xs font-medium text-gray-400 group-hover:text-white transition-colors">
                    <span class="material-symbols-outlined text-[16px]">play_circle</span>
                    Play
                </a>
            </div>
        </div>
    </div>
"""
    return ''.join(phtml.format(cid=post["_id"], chat_id=str(post["chat_id"]).replace("-100", ""), id=post["file_id"], img=post["thumbnail"], title=post["name"], hash=post["hash"], size=post['size'], type=post['file_type'], ctype=post["parent_folder"]) for post in posts)
