"""
Media Cache Manager for Video/Audio Streaming
Implements LFU-based eviction policy with access counting
"""

import asyncio
import hashlib
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Set

from pymongo import MongoClient, ASCENDING
from bot.config import Telegram

# Supported media types for caching
CACHEABLE_EXTENSIONS = {
    'video': ['.mp4', '.mkv', '.webm', '.avi', '.mov', '.flv', '.wmv'],
    'audio': ['.mp3', '.m4a', '.flac', '.wav', '.ogg', '.aac']
}

CACHEABLE_MIMETYPES = {
    'video/mp4', 'video/x-matroska', 'video/webm', 'video/avi', 
    'video/quicktime', 'video/x-flv', 'video/x-ms-wmv',
    'audio/mpeg', 'audio/mp4', 'audio/flac', 'audio/wav', 
    'audio/ogg', 'audio/aac'
}


class MediaCache:
    """
    LFU-based media cache manager.
    
    Score formula: access_count * K_FACTOR + recency_bonus
    Files with lowest score are evicted first when cache is full.
    """
    
    K_FACTOR = 10  # Weight for access frequency
    RECENCY_DECAY_HOURS = 24  # Hours before recency bonus decays
    
    def __init__(self):
        self.enabled = False
        self.cache_dir = None
        self.max_size_bytes = 0
        self.collection = None
        self.downloading: Set[str] = set()  # Track files being downloaded
        self._download_lock = asyncio.Lock()  # Lock for atomic check-and-add
        
        try:
            if not Telegram.CACHE_ENABLED:
                logging.info("Media cache disabled by config")
                return
            
            self.cache_dir = Path(Telegram.CACHE_DIR)
            self.max_size_bytes = Telegram.CACHE_MAX_SIZE_GB * 1024 * 1024 * 1024
            
            # MongoDB connection
            self.mongo_client = MongoClient(Telegram.DATABASE_URL)
            self.db = self.mongo_client["surftg"]
            self.collection = self.db["media_cache"]
            
            # Create indexes
            self.collection.create_index("cache_key", unique=True)
            self.collection.create_index([("score", ASCENDING)])
            
            # Ensure cache directory exists
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            
            self.enabled = True
            logging.info(f"Media cache initialized: {self.cache_dir} (max: {Telegram.CACHE_MAX_SIZE_GB}GB)")
            
        except Exception as e:
            logging.error(f"Media cache initialization failed: {e}")
            self.enabled = False
    
    def is_downloading(self, chat_id: int, msg_id: int, secure_hash: str) -> bool:
        """Check if file is currently being downloaded."""
        cache_key = self._generate_cache_key(chat_id, msg_id, secure_hash)
        return cache_key in self.downloading
    
    async def start_background_download(
        self,
        chat_id: int,
        msg_id: int,
        secure_hash: str,
        file_id,
        file_size: int,
        mime_type: str,
        file_name: str,
        tg_connect,
        client_index: int
    ) -> None:
        """Start background download using separate client."""
        if not self.enabled:
            return
        
        cache_key = self._generate_cache_key(chat_id, msg_id, secure_hash)
        
        # Use lock to prevent race condition (atomic check-and-add)
        async with self._download_lock:
            # Skip if already downloading or cached
            if cache_key in self.downloading:
                logging.debug(f"Already downloading: {file_name}")
                return
            
            if self.is_cached(chat_id, msg_id, secure_hash):
                logging.debug(f"Already cached: {file_name}")
                return
            
            if not self._is_cacheable(mime_type, file_name):
                return
            
            # Mark as downloading INSIDE the lock
            self.downloading.add(cache_key)
        
        logging.info(f"Starting background download: {file_name} ({file_size / 1024 / 1024:.1f}MB)")
        
        # Start async download task
        asyncio.create_task(self._download_file(
            cache_key, chat_id, msg_id, secure_hash, file_id, 
            file_size, mime_type, file_name, tg_connect, client_index
        ))
    
    async def _download_file(
        self,
        cache_key: str,
        chat_id: int,
        msg_id: int,
        secure_hash: str,
        file_id,
        file_size: int,
        mime_type: str,
        file_name: str,
        tg_connect,
        client_index: int
    ) -> None:
        """Actually download the file to cache with client rotation."""
        from bot.telegram import multi_clients
        from bot.server.custom_dl import ByteStreamer
        import asyncio

        # Determine extension
        if file_name:
            ext = Path(file_name).suffix.lower()
        else:
            ext_map = {
                'video/mp4': '.mp4', 'video/x-matroska': '.mkv',
                'video/webm': '.webm', 'audio/mpeg': '.mp3',
            }
            ext = ext_map.get(mime_type, '.bin')
        
        filename = self._generate_filename(cache_key, ext)
        file_path = self.cache_dir / filename
        
        # Retry loop for client rotation
        max_retries = len(multi_clients)
        current_client_index = client_index
        current_tg_connect = tg_connect

        for attempt in range(max_retries):
            try:
                # Ensure directory exists
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                
                # Fetch FRESH file properties
                try:
                    fresh_file_id = await current_tg_connect.get_file_properties(chat_id=chat_id, message_id=msg_id)
                except Exception as e:
                    if "FLOOD_WAIT" in str(e):
                        raise e # Re-raise to be caught by outer handler for rotation
                    logging.error(f"Failed to get file properties: {e}")
                    # If we can't even get properties, maybe try next client?
                    raise e

                logging.info(f"Background download: got fresh file_id for {file_name} (Client {current_client_index})")
                
                # Ensure space
                await self._ensure_space(file_size)
                
                # Download full file
                chunk_size = 1024 * 1024  # 1MB chunks
                offset = 0
                total_written = 0
                last_logged_percent = 0
                
                with open(file_path, 'wb') as f:
                    async for chunk in current_tg_connect.yield_file(
                        fresh_file_id, current_client_index, offset, 0, file_size % chunk_size or chunk_size,
                        (file_size + chunk_size - 1) // chunk_size, chunk_size
                    ):
                        if chunk:
                            f.write(chunk)
                            total_written += len(chunk)
                            
                            # Log progress every 10%
                            if file_size > 0:
                                current_percent = int((total_written / file_size) * 100)
                                if current_percent >= last_logged_percent + 10 or current_percent == 100:
                                    logging.info(f"Downloading [{file_name}]: {current_percent}% ({total_written / 1024 / 1024:.1f}MB / {file_size / 1024 / 1024:.1f}MB)")
                                    last_logged_percent = current_percent
                
                # Verify file size
                if file_path.exists():
                    actual_size = file_path.stat().st_size
                else:
                    actual_size = total_written
                
                if actual_size >= file_size * 0.99:
                    # Success! Save metadata and break loop
                    now = datetime.utcnow()
                    score = self._calculate_score(1, now)
                    
                    self.collection.update_one(
                        {"cache_key": cache_key},
                        {
                            "$set": {
                                "cache_key": cache_key,
                                "file_path": str(file_path),
                                "file_size": actual_size,
                                "mime_type": mime_type,
                                "file_name": file_name,
                                "access_count": 1,
                                "last_access": now,
                                "created_at": now,
                                "score": score
                            }
                        },
                        upsert=True
                    )
                    logging.info(f"Background download complete: {file_name} ({actual_size / 1024 / 1024:.1f}MB)")
                    self.downloading.discard(cache_key)
                    return # Exit function on success

                else:
                    logging.warning(f"Incomplete download: {file_name} ({actual_size}/{file_size})")
                    if file_path.exists():
                        file_path.unlink()
                        
            except Exception as e:
                err_str = str(e)
                if "FLOOD_WAIT" in err_str or "flood" in err_str.lower():
                    wait_time = 5 # Default fallback
                    # Try to extract wait time if possible, but simplest is just rotate
                    logging.warning(f"FloodWait on Client {current_client_index}: {e}. Switching client...")
                    
                    # Rotate Client
                    current_client_index = (current_client_index + 1) % len(multi_clients)
                    current_client = multi_clients[current_client_index]
                    current_tg_connect = ByteStreamer(current_client)
                    
                    # Small delay before retry
                    await asyncio.sleep(1)
                    continue # Try next client
                
                elif isinstance(e, asyncio.CancelledError):
                    logging.debug(f"Background download cancelled: {file_name}")
                    if file_path.exists():
                        file_path.unlink()
                    break # Don't retry on cancel
                else:
                    logging.error(f"Background download error (Client {current_client_index}): {e}")
                    if file_path.exists():
                        file_path.unlink()
                    # Try next client for other errors too? Maybe.
                    # For now, let's retry only on FloodWait or similar network issues.
                    # But if "Client Request Interrupted" happens, maybe we should retry?
                    # Let's retry for ANY exception that isn't Cancelled, up to max_retries
                    logging.info(f"Retrying with next client due to error...")
                    current_client_index = (current_client_index + 1) % len(multi_clients)
                    current_client = multi_clients[current_client_index]
                    current_tg_connect = ByteStreamer(current_client)
                    await asyncio.sleep(1)
                    continue

        # If loop finishes without return, we failed
        logging.error(f"All download attempts failed for {file_name}")
        self.downloading.discard(cache_key)

    def _generate_cache_key(self, chat_id: int, msg_id: int, secure_hash: str) -> str:
        """Generate unique cache key for a media file."""
        return f"{chat_id}:{msg_id}:{secure_hash}"
    
    def _generate_filename(self, cache_key: str, extension: str) -> str:
        """Generate safe filename from cache key."""
        hash_name = hashlib.md5(cache_key.encode()).hexdigest()
        return f"{hash_name}{extension}"
    
    def _is_cacheable(self, mime_type: str, file_name: str = None) -> bool:
        """Check if file type is cacheable."""
        if mime_type and mime_type in CACHEABLE_MIMETYPES:
            return True
        if file_name:
            ext = Path(file_name).suffix.lower()
            for exts in CACHEABLE_EXTENSIONS.values():
                if ext in exts:
                    return True
        return False
    
    def _calculate_score(self, access_count: int, last_access: datetime) -> float:
        """Calculate eviction score (lower = evict first)."""
        # Base score from access count
        score = access_count * self.K_FACTOR
        
        # Add recency bonus (decays over time)
        hours_since_access = (datetime.utcnow() - last_access).total_seconds() / 3600
        recency_bonus = max(0, 100 - (hours_since_access / self.RECENCY_DECAY_HOURS * 10))
        
        return score + recency_bonus
    
    def get_cached_path(self, chat_id: int, msg_id: int, secure_hash: str) -> Optional[Path]:
        """Get cached file path if exists."""
        if not self.enabled:
            return None
            
        cache_key = self._generate_cache_key(chat_id, msg_id, secure_hash)
        doc = self.collection.find_one({"cache_key": cache_key})
        
        logging.debug(f"Cache lookup: key={cache_key}, found_in_db={doc is not None}")
        
        if doc:
            file_path = doc.get("file_path")
            file_exists = os.path.exists(file_path) if file_path else False
            logging.info(f"Cache check: {file_path}, exists={file_exists}")
            
            if file_exists:
                return Path(file_path)
            else:
                # File missing from disk, clean up DB entry
                logging.warning(f"Cache file missing, cleaning DB: {cache_key}")
                self.collection.delete_one({"cache_key": cache_key})
        
        return None
    
    def is_cached(self, chat_id: int, msg_id: int, secure_hash: str) -> bool:
        """Check if media is cached."""
        return self.get_cached_path(chat_id, msg_id, secure_hash) is not None
    
    async def record_access(self, chat_id: int, msg_id: int, secure_hash: str) -> None:
        """Record file access to update score."""
        cache_key = self._generate_cache_key(chat_id, msg_id, secure_hash)
        
        now = datetime.utcnow()
        result = self.collection.find_one_and_update(
            {"cache_key": cache_key},
            {
                "$inc": {"access_count": 1},
                "$set": {"last_access": now}
            },
            return_document=True
        )
        
        # Trigger Smart Pre-Caching if this is a "new" access (or simply every time, logic handles dupes)
        # To avoid spamming, maybe check if we just updated.
        # But we need filename. 'result' contains OLD document (default return of find_one_and_update)
        # If result is None, it means it wasn't in cache DB yet (maybe first stream?)
        # Actually start_background_download inserts it later.
        # This record_access is called by stream_from_cache.
        
        if result:
            filename = result.get('file_name')
            if filename:
                 asyncio.create_task(self.smart_pre_cache(chat_id, filename))
        
        if result:
            # Update score
            new_score = self._calculate_score(result["access_count"], now)
            self.collection.update_one(
                {"cache_key": cache_key},
                {"$set": {"score": new_score}}
            )
    
    async def add_to_cache(
        self, 
        chat_id: int, 
        msg_id: int, 
        secure_hash: str,
        file_data: bytes,
        mime_type: str,
        file_name: str = None
    ) -> Optional[Path]:
        """
        Save file to cache.
        Returns the cached file path or None if caching failed/disabled.
        """
        if not self.enabled:
            return None
        
        if not self._is_cacheable(mime_type, file_name):
            logging.debug(f"File not cacheable: {mime_type}")
            return None
        
        cache_key = self._generate_cache_key(chat_id, msg_id, secure_hash)
        
        # Determine extension
        if file_name:
            ext = Path(file_name).suffix.lower()
        elif mime_type:
            ext_map = {
                'video/mp4': '.mp4', 'video/x-matroska': '.mkv',
                'video/webm': '.webm', 'audio/mpeg': '.mp3',
                'audio/mp4': '.m4a', 'audio/flac': '.flac'
            }
            ext = ext_map.get(mime_type, '.bin')
        else:
            ext = '.bin'
        
        filename = self._generate_filename(cache_key, ext)
        file_path = self.cache_dir / filename
        
        # Check if we need to make room
        file_size = len(file_data)
        await self._ensure_space(file_size)
        
        try:
            # Write file
            with open(file_path, 'wb') as f:
                f.write(file_data)
            
            now = datetime.utcnow()
            score = self._calculate_score(1, now)
            
            # Save metadata
            self.collection.update_one(
                {"cache_key": cache_key},
                {
                    "$set": {
                        "cache_key": cache_key,
                        "file_path": str(file_path),
                        "file_size": file_size,
                        "mime_type": mime_type,
                        "file_name": file_name,
                        "access_count": 1,
                        "last_access": now,
                        "created_at": now,
                        "score": score
                    }
                },
                upsert=True
            )
            
            logging.info(f"Cached: {file_name or cache_key} ({file_size / 1024 / 1024:.1f}MB)")
            return file_path
            
        except Exception as e:
            logging.error(f"Cache write error: {e}")
            if file_path.exists():
                file_path.unlink()
            return None
    
    async def add_to_cache_streaming(
        self,
        chat_id: int,
        msg_id: int, 
        secure_hash: str,
        mime_type: str,
        file_name: str = None
    ) -> Optional[Path]:
        """
        Prepare cache file for streaming write.
        Returns file path to write to, or None if not cacheable.
        """
        if not self.enabled:
            return None
        
        if not self._is_cacheable(mime_type, file_name):
            return None
        
        cache_key = self._generate_cache_key(chat_id, msg_id, secure_hash)
        
        # Determine extension
        if file_name:
            ext = Path(file_name).suffix.lower()
        else:
            ext_map = {
                'video/mp4': '.mp4', 'video/x-matroska': '.mkv',
                'video/webm': '.webm', 'audio/mpeg': '.mp3',
            }
            ext = ext_map.get(mime_type, '.bin')
        
        filename = self._generate_filename(cache_key, ext)
        file_path = self.cache_dir / filename
        
        return file_path
    
    async def finalize_cache(
        self,
        chat_id: int,
        msg_id: int,
        secure_hash: str,
        file_path: Path,
        file_size: int,
        mime_type: str,
        file_name: str = None
    ) -> None:
        """Finalize cache entry after streaming write completes."""
        if not file_path.exists():
            return
        
        cache_key = self._generate_cache_key(chat_id, msg_id, secure_hash)
        now = datetime.utcnow()
        score = self._calculate_score(1, now)
        
        self.collection.update_one(
            {"cache_key": cache_key},
            {
                "$set": {
                    "cache_key": cache_key,
                    "file_path": str(file_path),
                    "file_size": file_size,
                    "mime_type": mime_type,
                    "file_name": file_name,
                    "access_count": 1,
                    "last_access": now,
                    "created_at": now,
                    "score": score
                }
            },
            upsert=True
        )
        
        logging.info(f"Cache finalized: {file_name or cache_key} ({file_size / 1024 / 1024:.1f}MB)")
    
    def get_cache_size(self) -> int:
        """Get current cache size in bytes."""
        total = 0
        for doc in self.collection.find({}, {"file_size": 1}):
            total += doc.get("file_size", 0)
        return total
    
    async def _ensure_space(self, needed_bytes: int) -> None:
        """Ensure there's enough space, evicting files if necessary."""
        current_size = self.get_cache_size()
        target_size = self.max_size_bytes - needed_bytes
        
        if current_size <= target_size:
            return
        
        logging.info(f"Cache eviction triggered: Container full (limit {self.max_size_bytes/1024/1024/1024:.2f}GB). Need {needed_bytes/1024/1024:.1f}MB, current {current_size/1024/1024/1024:.2f}GB")
        
        # Get files sorted by score (lowest first = evict first)
        cursor = self.collection.find().sort("score", ASCENDING)
        
        for doc in cursor:
            if current_size <= target_size:
                break
            
            file_path = Path(doc["file_path"])
            file_size = doc["file_size"]
            
            try:
                if file_path.exists():
                    file_path.unlink()
                self.collection.delete_one({"_id": doc["_id"]})
                current_size -= file_size
                logging.info(f"Evicted: {doc.get('file_name', doc['cache_key'])} (score: {doc['score']:.1f})")
            except Exception as e:
                logging.error(f"Eviction error: {e}")
    
    async def cleanup(self) -> Dict[str, Any]:
        """Periodic cleanup task."""
        if not self.enabled:
            return {"status": "disabled"}
        
        # Remove orphaned DB entries (file doesn't exist on disk)
        removed = 0
        for doc in self.collection.find():
            if not os.path.exists(doc["file_path"]):
                self.collection.delete_one({"_id": doc["_id"]})
                removed += 1
        
        # Recalculate all scores
        now = datetime.utcnow()
        for doc in self.collection.find():
            new_score = self._calculate_score(doc["access_count"], doc["last_access"])
            self.collection.update_one(
                {"_id": doc["_id"]},
                {"$set": {"score": new_score}}
            )
        
        current_size = self.get_cache_size()
        
        return {
            "status": "ok",
            "cache_size_gb": current_size / 1024 / 1024 / 1024,
            "max_size_gb": Telegram.CACHE_MAX_SIZE_GB,
            "orphans_removed": removed,
            "files_cached": self.collection.count_documents({})
        }


    async def smart_pre_cache(self, chat_id: int, current_filename: str):
        """
        Predict and pre-cache the next episode based on current filename.
        Patterns:
        - "Title - 04 [1080p]..." -> "Title - 05 [1080p]..."
        - "Title--04 720p" -> "Title--05 720p"
        """
        import re
        from bot.helper.database import Database
        
        logging.info(f"Smart Pre-Caching: Analyzing {current_filename}")
        
        # Regex to find episode number (supports 01, 1, etc.)
        # Groups: 1=Prefix, 2=EpisodeNum, 3=Suffix
        patterns = [
            r'(.* - )(\d{2,3})( \[.*)',     # Title - 04 [1080p]
            r'(.*--)(\d{2,3})(.*)',         # Title--04 720p or Title--04
            r'(.* )(\d{1,3})( .*)'          # Generic Title 04 Suffix
        ]
        
        next_filename_pattern = None
        
        for pattern in patterns:
            match = re.match(pattern, current_filename)
            if match:
                prefix = match.group(1)
                ep_num_str = match.group(2)
                suffix = match.group(3)
                
                try:
                    curr_ep = int(ep_num_str)
                    next_ep = curr_ep + 1
                    
                    # Pad with zero if original was padded (e.g. 04 -> 05)
                    next_ep_str = str(next_ep).zfill(len(ep_num_str))
                    
                    # Create regex for next file search
                    # We use regex search in DB because exact suffix might differ slightly
                    next_filename_pattern = f"^{re.escape(prefix)}{next_ep_str}.*"
                    logging.info(f"Smart Pre-Caching: Prediction matched. Looking for pattern: {next_filename_pattern}")
                    break
                except ValueError:
                    continue
        
        if not next_filename_pattern:
            logging.debug("Smart Pre-Caching: Could not predict next episode pattern.")
            return

        # Search for Next Episode in Database
        db = Database()
        # Use regex search to find the file
        # Note: Depending on DB volume, simple regex might be slow without text index.
        # But this runs in background task.
        found_files = await db.search_tgfiles(chat_id, next_filename_pattern, page=1, per_page=1)
        
        if not found_files:
             # Also try searching in 'files' collection by regex directly if search_tgfiles abstraction is limited
             # But let's assume search_tgfiles works with regex query we constructed
             # Wait, search_tgfiles implementation splits query by space. 
             # We should probably use a direct find here for precision.
             pass

        # Let's retry direct DB find for precision since search_tgfiles does token splitting
        try:
            regex_query = {"chat_id": str(chat_id), "title": {"$regex": next_filename_pattern, "$options": "i"}}
            next_file = await db.files.find_one(regex_query)
            
            if next_file:
                logging.info(f"Smart Pre-Caching: Found next episode: {next_file.get('title')}")
                # Check if already cached
                next_msg_id = next_file['msg_id']
                next_hash = next_file['hash']
                
                if self.is_cached(chat_id, next_msg_id, next_hash):
                     logging.info("Smart Pre-Caching: Next episode is already cached. Skipping.")
                     return

                # Check if already downloading using is_downloading check in start_background_download
                # We need file_id and info to start download
                # We need to construct a "fake" file_id object or fetch it from Telegram
                # Fetching properties is needed.
                
                # We spawn a background task to fetch props and start download
                asyncio.create_task(self._trigger_pre_download(chat_id, next_msg_id, next_hash, next_file['title']))
                
            else:
                logging.info("Smart Pre-Caching: Next episode not found in DB.")
                
        except Exception as e:
            logging.error(f"Smart Pre-Caching Error: {e}")

    async def _trigger_pre_download(self, chat_id, msg_id, secure_hash, filename):
        """Helper to fetch props and start download for pre-caching."""
        from bot.telegram import multi_clients, work_loads
        from bot.server.custom_dl import ByteStreamer
        from bot.server.stream_routes import class_cache
        
        try:
            # Select client
            index = min(work_loads, key=work_loads.get)
            client = multi_clients[index]
            
            if client in class_cache:
                tg_connect = class_cache[client]
            else:
                tg_connect = ByteStreamer(client)
                class_cache[client] = tg_connect
                
            # Get File Props
            file_id = await tg_connect.get_file_properties(chat_id, msg_id)
            
            if file_id.unique_id[:6] != secure_hash:
                 return

            file_size = file_id.file_size
            mime_type = file_id.mime_type or "video/mp4" # Fallback
            
            # Start Download
            # Use a separate background client index to avoid interfering with current stream choice logic if possible
            bg_index = (index + 1) % len(multi_clients)
            
            # Re-use start_background_download logic
            # We need to pass the bg_connect matching bg_index
            bg_client = multi_clients[bg_index]
            if bg_client in class_cache:
                bg_connect = class_cache[bg_client]
            else:
                bg_connect = ByteStreamer(bg_client)
                class_cache[bg_client] = bg_connect

            await self.start_background_download(
                chat_id, msg_id, secure_hash, file_id, file_size, mime_type, filename, bg_connect, bg_index
            )
            logging.info(f"Smart Pre-Caching: STARTED download for {filename}")
            
        except Exception as e:
            logging.error(f"Pre-cache trigger failed: {e}")

# Global cache instance
media_cache = MediaCache()
