from os.path import splitext
import re
from bot.config import Telegram
from bot.helper.database import Database
from bot.telegram import StreamBot, UserBot
from bot.helper.file_size import get_readable_file_size
from bot.helper.cache import get_cache, save_cache
from asyncio import gather

db = Database()


async def fetch_message(chat_id, message_id):
    try:
        message = await StreamBot.get_messages(chat_id, message_id)
        return message
    except Exception as e:
        return None


async def get_messages(chat_id, first_message_id, last_message_id, batch_size=50):
    messages = []
    current_message_id = first_message_id
    while current_message_id <= last_message_id:
        batch_message_ids = list(range(current_message_id, min(current_message_id + batch_size, last_message_id + 1)))
        tasks = [fetch_message(chat_id, message_id) for message_id in batch_message_ids]
        batch_messages = await gather(*tasks)
        for message in batch_messages:
            if message:
                if file := message.video or message.document:
                    title = file.file_name or message.caption or file.file_id
                    title, _ = splitext(title)
                    title = re.sub(r'[.,|_\',]', ' ', title)
                    messages.append({"msg_id": message.id, "title": title,
                                     "hash": file.file_unique_id[:6], "size": get_readable_file_size(file.file_size),
                                     "type": file.mime_type, "chat_id": str(chat_id)})
        current_message_id += batch_size
    return messages


async def get_files(chat_id, page=1):
    if Telegram.SESSION_STRING == '':
        return await db.list_tgfiles(id=chat_id, page=page)
    if cache := get_cache(chat_id, int(page)):
        return cache
    posts = []
    async for post in UserBot.get_chat_history(chat_id=int(chat_id), limit=50, offset=(int(page) - 1) * 50):
        file = post.video or post.document
        if not file:
            continue
        title = file.file_name or post.caption or file.file_id
        title, _ = splitext(title)
        title = re.sub(r'[.,|_\',]', ' ', title)
        posts.append({"msg_id": post.id, "title": title,
                    "hash": file.file_unique_id[:6], "size": get_readable_file_size(file.file_size), "type": file.mime_type})
    save_cache(chat_id, {"posts": posts}, page)
    return posts

async def posts_file(posts, chat_id):
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
        </a>
        
        <div class="p-3">
            <a href="/watch/{chat_id}?id={id}&hash={hash}" class="block group-hover:text-primary transition-colors duration-200">
                <h3 class="text-white font-medium text-sm line-clamp-2 leading-snug min-h-[2.5rem]" title="{title}">{title}</h3>
            </a>
            
            <div class="flex items-center justify-between mt-3 px-1">
                <span class="px-2 py-0.5 rounded bg-white/5 text-gray-400 text-[10px] uppercase font-bold tracking-wider border border-white/5">
                    {type}
                </span>
                
                <!-- Admin Checkbox -->
                <input type="checkbox" class="admin-only form-checkbox rounded border-gray-600 bg-gray-700 text-primary focus:ring-primary w-4 h-4 cursor-pointer"
                    onchange="checkSendButton()" id="selectCheckbox"
                    data-id="{id}|{hash}|{title}|{size}|{type}|{img}">
            </div>
        </div>
    </div>
"""
    return ''.join(phtml.format(chat_id=str(chat_id).replace("-100", ""), id=post["msg_id"], img=f"/api/thumb/{chat_id}?id={post['msg_id']}", title=post["title"], hash=post["hash"], size=post['size'], type=post['type']) for post in posts)
