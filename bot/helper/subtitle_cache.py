"""
Subtitle Cache Manager for MKV Subtitle Extraction
Stores extracted .ass subtitle files with automatic cleanup
"""

import asyncio
import hashlib
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Cache configuration
CACHE_DIR = Path("cache/subtitles")
CACHE_TTL_DAYS = 7  # Subtitle files are kept for 7 days


class SubtitleCache:
    """Manages cached subtitle files extracted from MKV videos."""
    
    def __init__(self):
        self.cache_dir = CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._processing_locks: dict[str, asyncio.Lock] = {}
        self._processing_tasks: set[str] = set()
        logging.info(f"Subtitle cache initialized at {self.cache_dir}")
    
    def _generate_cache_key(self, chat_id: int, msg_id: int, secure_hash: str, track_index: int = 0) -> str:
        """Generate unique cache key for a subtitle file."""
        raw_key = f"{chat_id}_{msg_id}_{secure_hash}_{track_index}"
        return hashlib.md5(raw_key.encode()).hexdigest()
    
    def _get_cache_path(self, cache_key: str) -> Path:
        """Get the file path for a cached subtitle."""
        return self.cache_dir / f"{cache_key}.ass"
    
    def get_cached_subtitle(self, chat_id: int, msg_id: int, secure_hash: str, track_index: int = 0) -> Optional[Path]:
        """
        Get cached subtitle path if exists and not expired.
        
        Returns:
            Path to cached .ass file or None if not cached
        """
        cache_key = self._generate_cache_key(chat_id, msg_id, secure_hash, track_index)
        cache_path = self._get_cache_path(cache_key)
        
        if cache_path.exists():
            # Check if file is too old
            mtime = datetime.fromtimestamp(cache_path.stat().st_mtime)
            if datetime.now() - mtime < timedelta(days=CACHE_TTL_DAYS):
                logging.info(f"Subtitle cache HIT: {cache_key}")
                return cache_path
            else:
                # Expired, remove it
                logging.info(f"Subtitle cache EXPIRED: {cache_key}")
                cache_path.unlink(missing_ok=True)
        
        return None
    
    async def cache_subtitle(self, chat_id: int, msg_id: int, secure_hash: str, 
                             content: bytes, track_index: int = 0) -> Path:
        """
        Save subtitle content to cache.
        
        Args:
            chat_id: Telegram chat ID
            msg_id: Message ID
            secure_hash: Secure hash for verification
            content: Subtitle file content (bytes)
            track_index: Subtitle track index (for multi-track videos)
            
        Returns:
            Path to cached file
        """
        cache_key = self._generate_cache_key(chat_id, msg_id, secure_hash, track_index)
        cache_path = self._get_cache_path(cache_key)
        
        # Write to temp file first, then rename (atomic operation)
        temp_path = cache_path.with_suffix('.tmp')
        try:
            temp_path.write_bytes(content)
            temp_path.rename(cache_path)
            logging.info(f"Subtitle cached: {cache_key} ({len(content)} bytes)")
            return cache_path
        except Exception as e:
            logging.error(f"Failed to cache subtitle: {e}")
            temp_path.unlink(missing_ok=True)
            raise
    
    def is_processing(self, chat_id: int, msg_id: int, secure_hash: str) -> bool:
        """Check if subtitle extraction is currently in progress."""
        cache_key = self._generate_cache_key(chat_id, msg_id, secure_hash)
        return cache_key in self._processing_tasks
    
    async def get_lock(self, chat_id: int, msg_id: int, secure_hash: str) -> asyncio.Lock:
        """Get or create a lock for processing a specific subtitle."""
        cache_key = self._generate_cache_key(chat_id, msg_id, secure_hash)
        if cache_key not in self._processing_locks:
            self._processing_locks[cache_key] = asyncio.Lock()
        return self._processing_locks[cache_key]
    
    def mark_processing(self, chat_id: int, msg_id: int, secure_hash: str, processing: bool):
        """Mark a subtitle as being processed or finished."""
        cache_key = self._generate_cache_key(chat_id, msg_id, secure_hash)
        if processing:
            self._processing_tasks.add(cache_key)
        else:
            self._processing_tasks.discard(cache_key)
    
    async def cleanup_old_files(self):
        """Remove expired subtitle files from cache."""
        if not self.cache_dir.exists():
            return
        
        cutoff = datetime.now() - timedelta(days=CACHE_TTL_DAYS)
        removed_count = 0
        
        for file_path in self.cache_dir.glob("*.ass"):
            try:
                mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                if mtime < cutoff:
                    file_path.unlink()
                    removed_count += 1
            except Exception as e:
                logging.warning(f"Failed to cleanup {file_path}: {e}")
        
        if removed_count > 0:
            logging.info(f"Subtitle cache cleanup: removed {removed_count} expired files")
    
    async def periodic_cleanup(self):
        """Background task to periodically clean up old files."""
        while True:
            await asyncio.sleep(6 * 60 * 60)  # Run every 6 hours
            await self.cleanup_old_files()


# Global subtitle cache instance
subtitle_cache = SubtitleCache()
