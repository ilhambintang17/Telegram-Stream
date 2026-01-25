from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import DESCENDING
from bson import ObjectId
from bot.config import Telegram
import re
import asyncio

class Database:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Database, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        
        # Defer client creation to avoid event loop mixing
        self._client = None
        self._db = None
        self._initialized = True

    @property
    def mongo_client(self):
        if self._client is None:
            MONGODB_URI = Telegram.DATABASE_URL
            self._client = AsyncIOMotorClient(MONGODB_URI)
            # Motor 3.x+ automatically uses the running loop when operations are awaited
        return self._client

    @property
    def db(self):
        if self._db is None:
            self._db = self.mongo_client["surftg"]
        return self._db

    @property
    def collection(self):
        return self.db["playlist"]

    @property
    def config(self):
        return self.db["config"]

    @property
    def files(self):
        return self.db["files"]

    # Helper to ensure indexes are created (call this from __main__ or lazy check)
    async def create_indexes(self):
        # Create Indexes
        await self.files.create_index([("title", "text"), ("chat_id", 1)])
        await self.collection.create_index([("name", "text"), ("parent_folder", 1)])


    async def create_folder(self, parent_id, folder_name, thumbnail):
        folder = {"parent_folder": parent_id, "name": folder_name,
                  "thumbnail": thumbnail, "type": "folder"}
        await self.collection.insert_one(folder)

    async def delete(self, document_id):
        try:
            has_child_documents = await self.collection.count_documents(
                {'parent_folder': document_id}) > 0
            if has_child_documents:
                await self.collection.delete_many(
                    {'parent_folder': document_id})
            result = await self.collection.delete_one({'_id': ObjectId(document_id)})
            return result.deleted_count > 0
        except Exception as e:
            print(f'An error occurred: {e}')
            return False

    async def edit(self, id, name, thumbnail):
        result = await self.collection.update_one({"_id": ObjectId(id)}, {
            "$set": {"name": name, "thumbnail": thumbnail}})
        return result.modified_count > 0

    async def search_DbFolder(self, query):
        words = re.findall(r'\w+', query.lower())
        regex_pattern = '.*'.join(f'(?=.*{re.escape(word)})' for word in words)
        regex_query = {'$regex': f'.*{regex_pattern}.*', '$options': 'i'}
        myquery = {'type': 'folder', 'name': regex_query}
        cursor = self.collection.find(myquery).sort('_id', DESCENDING)
        mydoc = await cursor.to_list(length=None)
        return [{'_id': str(x['_id']), 'name': x['name']} for x in mydoc]

    async def add_json(self, data):
        if data:
            await self.collection.insert_many(data)

    async def get_Dbfolder(self, parent_id="root", page=1, per_page=50):
        query = {"parent_folder": parent_id, "type": "folder"} if parent_id != 'root' else {
            "parent_folder": 'root', "type": "folder"}
        
        cursor = self.collection.find(query)
        if parent_id != 'root':
            offset = (int(page) - 1) * per_page
            cursor = cursor.skip(offset).limit(per_page)
        
        return await cursor.to_list(length=per_page if parent_id != 'root' else None)

    async def get_dbFiles(self, parent_id=None, page=1, per_page=50):
        query = {"parent_folder": parent_id, "type": "file"}
        offset = (int(page) - 1) * per_page
        cursor = self.collection.find(query).sort(
            'file_id', DESCENDING).skip(offset).limit(per_page)
        return await cursor.to_list(length=per_page)

    async def get_info(self, id):
        query = {'_id': ObjectId(id)}
        if document := await self.collection.find_one(query):
            return document.get('name', None)
        else:
            return None

    async def search_dbfiles(self, id, query, page=1, per_page=50):
        words = re.findall(r'\w+', query.lower())
        regex_pattern = '.*'.join(f'(?=.*{re.escape(word)})' for word in words)
        regex_query = {'$regex': f'.*{regex_pattern}.*', '$options': 'i'}
        query = {'type': 'file', 'parent_folder': id, 'name': regex_query}
        offset = (int(page) - 1) * per_page
        cursor = self.collection.find(query).sort(
            'file_id', DESCENDING).skip(offset).limit(per_page)
        return await cursor.to_list(length=per_page)

    async def update_config(self, theme, auth_channel):
        bot_id = Telegram.BOT_TOKEN.split(":", 1)[0]
        config = await self.config.find_one({"_id": bot_id})
        if config is None:
            result = await self.config.insert_one(
                {"_id": bot_id, "theme": theme, "auth_channel": auth_channel})
            return result.inserted_id is not None
        else:
            result = await self.config.update_one({"_id": bot_id}, {
                "$set": {"theme": theme, "auth_channel": auth_channel}})
            return result.modified_count > 0

    async def get_variable(self, key):
        bot_id = Telegram.BOT_TOKEN.split(":", 1)[0]
        config = await self.config.find_one({"_id": bot_id})
        return config.get(key) if config is not None else None

    async def list_tgfiles(self, id, page=1, per_page=50):
        query = {'chat_id': id}
        offset = (int(page) - 1) * per_page
        cursor = self.files.find(query).sort(
            'msg_id', DESCENDING).skip(offset).limit(per_page)
        return await cursor.to_list(length=per_page)

    async def add_tgfiles(self, chat_id, file_id, hash, name, size, file_type):
        if await self.files.find_one({"chat_id": chat_id, "hash": hash}):
            return
        file = {"chat_id": chat_id, "msg_id": file_id,
                "hash": hash, "title": name, "size": size, "type": file_type}
        await self.files.insert_one(file)

    async def search_tgfiles(self, id, query, page=1, per_page=50):
        words = re.findall(r'\w+', query.lower())
        regex_pattern = '.*'.join(f'(?=.*{re.escape(word)})' for word in words)
        regex_query = {'$regex': f'.*{regex_pattern}.*', '$options': 'i'}
        query = {'chat_id': id, 'title': regex_query}
        offset = (int(page) - 1) * per_page
        cursor = self.files.find(query).sort(
            'msg_id', DESCENDING).skip(offset).limit(per_page)
        return await cursor.to_list(length=per_page)
    
    async def add_btgfiles(self, data):
        if data:
            await self.files.insert_many(data)

    async def delete_file(self, chat_id, msg_id, hash):
        """Delete a file entry from the files collection."""
        try:
            result = await self.files.delete_one({
                "chat_id": str(chat_id),
                "msg_id": int(msg_id),
                "hash": hash
            })
            return result.deleted_count > 0
        except Exception as e:
            print(f'Error deleting file: {e}')
            return False
