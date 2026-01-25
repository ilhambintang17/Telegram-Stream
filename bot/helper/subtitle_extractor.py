"""
Subtitle Extractor using FFmpeg
Extracts embedded subtitle tracks from MKV files
"""

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any

# Maximum bytes to download for subtitle extraction
# MKV container stores metadata at the beginning, 100MB is usually enough
MAX_DOWNLOAD_SIZE = 100 * 1024 * 1024  # 100 MB


class SubtitleTrackInfo:
    """Information about a subtitle track in a video."""
    
    def __init__(self, index: int, codec: str, language: str = "und", title: str = ""):
        self.index = index
        self.codec = codec
        self.language = language
        self.title = title
    
    def __repr__(self):
        return f"SubtitleTrack(index={self.index}, codec={self.codec}, lang={self.language}, title={self.title})"


async def detect_subtitle_tracks(video_path: Path) -> List[SubtitleTrackInfo]:
    """
    Detect all subtitle tracks in a video file using FFprobe.
    
    Args:
        video_path: Path to the video file
        
    Returns:
        List of SubtitleTrackInfo objects
    """
    try:
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            "-select_streams", "s",  # Only subtitle streams
            str(video_path)
        ]
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            logging.warning(f"FFprobe failed: {stderr.decode()}")
            return []
        
        data = json.loads(stdout.decode())
        streams = data.get("streams", [])
        
        tracks = []
        for stream in streams:
            track = SubtitleTrackInfo(
                index=stream.get("index", 0),
                codec=stream.get("codec_name", "unknown"),
                language=stream.get("tags", {}).get("language", "und"),
                title=stream.get("tags", {}).get("title", "")
            )
            tracks.append(track)
        
        logging.info(f"Found {len(tracks)} subtitle tracks in {video_path.name}")
        return tracks
        
    except Exception as e:
        logging.error(f"Error detecting subtitle tracks: {e}")
        return []


async def extract_subtitle(video_path: Path, stream_index: int, output_path: Path) -> bool:
    """
    Extract a specific subtitle track from video to ASS format.
    
    Args:
        video_path: Path to the video file
        stream_index: Stream index of the subtitle track
        output_path: Path to save the extracted .ass file
        
    Returns:
        True if extraction successful, False otherwise
    """
    try:
        cmd = [
            "ffmpeg",
            "-y",  # Overwrite output
            "-v", "warning",
            "-i", str(video_path),
            "-map", f"0:{stream_index}",
            "-c:s", "ass",  # Convert to ASS format
            str(output_path)
        ]
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        _, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            logging.error(f"FFmpeg extraction failed: {stderr.decode()}")
            return False
        
        if output_path.exists() and output_path.stat().st_size > 0:
            logging.info(f"Subtitle extracted successfully: {output_path.name}")
            return True
        else:
            logging.warning("FFmpeg completed but output file is empty or missing")
            return False
            
    except Exception as e:
        logging.error(f"Error extracting subtitle: {e}")
        return False


async def download_partial_video(
    chat_id: int,
    msg_id: int,
    secure_hash: str,
    file_id,
    file_size: int,
    tg_connect,
    client_index: int,
    output_path: Path,
    max_bytes: int = MAX_DOWNLOAD_SIZE
) -> bool:
    """
    Download partial video file for subtitle extraction.
    
    This downloads only the first portion of the video, which is usually
    sufficient for subtitle extraction since MKV stores metadata at the start.
    
    Args:
        chat_id: Telegram chat ID
        msg_id: Message ID
        secure_hash: Secure hash for verification
        file_id: Pyrogram FileId object
        file_size: Total file size
        tg_connect: ByteStreamer instance
        client_index: Client index for load balancing
        output_path: Path to save the partial download
        max_bytes: Maximum bytes to download
        
    Returns:
        True if download successful, False otherwise
    """
    
    try:
        # Calculate how much to download
        download_size = min(file_size, max_bytes)
        chunk_size = 1024 * 1024  # 1MB chunks
        
        logging.info(f"Starting partial download: {download_size / 1024 / 1024:.1f} MB of {file_size / 1024 / 1024:.1f} MB")
        
        with open(output_path, 'wb') as f:
            downloaded = 0
            part_count = (download_size + chunk_size - 1) // chunk_size
            
            async for chunk in tg_connect.yield_file(
                file_id, client_index, 0, 0, download_size, part_count, chunk_size
            ):
                f.write(chunk)
                downloaded += len(chunk)
                
                if downloaded >= download_size:
                    break
        
        if output_path.exists() and output_path.stat().st_size > 0:
            logging.info(f"Partial download complete: {downloaded / 1024 / 1024:.1f} MB")
            return True
        else:
            logging.warning("Download completed but file is empty")
            return False
            
    except Exception as e:
        logging.error(f"Error downloading partial video: {e}")
        return False


async def extract_subtitle_from_telegram(
    chat_id: int,
    msg_id: int,
    secure_hash: str,
    file_id,
    file_size: int,
    tg_connect,
    client_index: int,
    track_index: int = 0
) -> Optional[bytes]:
    """
    Download partial video from Telegram and extract subtitle.
    
    This is the main function to call for subtitle extraction.
    It downloads just enough of the video to extract the subtitle track.
    
    Args:
        chat_id: Telegram chat ID
        msg_id: Message ID  
        secure_hash: Secure hash for verification
        file_id: Pyrogram FileId object
        file_size: Total file size
        tg_connect: ByteStreamer instance
        client_index: Client index for load balancing
        track_index: Which subtitle track to extract (0 = first)
        
    Returns:
        Subtitle content as bytes, or None if extraction failed
    """
    temp_dir = tempfile.mkdtemp(prefix="subtitle_")
    video_path = Path(temp_dir) / "video.mkv"
    subtitle_path = Path(temp_dir) / "subtitle.ass"
    
    try:
        # Step 1: Download partial video
        download_success = await download_partial_video(
            chat_id, msg_id, secure_hash, file_id, file_size,
            tg_connect, client_index, video_path
        )
        
        if not download_success:
            logging.error("Failed to download video for subtitle extraction")
            return None
        
        # Step 2: Detect subtitle tracks
        tracks = await detect_subtitle_tracks(video_path)
        
        if not tracks:
            logging.info("No subtitle tracks found in video")
            return None
        
        # Select the requested track (or first available)
        if track_index >= len(tracks):
            track_index = 0
        
        target_track = tracks[track_index]
        logging.info(f"Extracting subtitle track: {target_track}")
        
        # Step 3: Extract subtitle
        extract_success = await extract_subtitle(
            video_path, target_track.index, subtitle_path
        )
        
        if not extract_success:
            logging.error("Failed to extract subtitle from video")
            return None
        
        # Step 4: Read and return subtitle content
        subtitle_content = subtitle_path.read_bytes()
        logging.info(f"Subtitle extraction complete: {len(subtitle_content)} bytes")
        
        return subtitle_content
        
    finally:
        # Cleanup temp files
        try:
            if video_path.exists():
                video_path.unlink()
            if subtitle_path.exists():
                subtitle_path.unlink()
            os.rmdir(temp_dir)
        except Exception as e:
            logging.warning(f"Failed to cleanup temp files: {e}")


async def get_subtitle_track_list(
    chat_id: int,
    msg_id: int,
    secure_hash: str,
    file_id,
    file_size: int,
    tg_connect,
    client_index: int
) -> List[Dict[str, Any]]:
    """
    Get list of available subtitle tracks without extracting.
    
    Returns:
        List of dicts with track info (index, language, title, codec)
    """
    temp_dir = tempfile.mkdtemp(prefix="subtitle_probe_")
    video_path = Path(temp_dir) / "video.mkv"
    
    try:
        # Download just 50MB for faster probing
        download_success = await download_partial_video(
            chat_id, msg_id, secure_hash, file_id, file_size,
            tg_connect, client_index, video_path,
            max_bytes=50 * 1024 * 1024
        )
        
        if not download_success:
            return []
        
        tracks = await detect_subtitle_tracks(video_path)
        
        return [
            {
                "index": t.index,
                "language": t.language,
                "title": t.title,
                "codec": t.codec
            }
            for t in tracks
        ]
        
    finally:
        try:
            if video_path.exists():
                video_path.unlink()
            os.rmdir(temp_dir)
        except:
            pass


async def extract_subtitle_from_local_file(
    video_path: Path,
    track_index: int = 0
) -> Optional[bytes]:
    """
    Extract subtitle from a local video file.
    Does NOT require a Telegram download.
    """
    temp_dir = tempfile.mkdtemp(prefix="subtitle_local_")
    subtitle_path = Path(temp_dir) / "subtitle.ass"
    
    try:
        # Detect tracks
        tracks = await detect_subtitle_tracks(video_path)
        if not tracks:
            logging.info(f"No subtitle tracks found in {video_path}")
            return None
        
        # Select track
        if track_index >= len(tracks):
            track_index = 0
        
        target_track = tracks[track_index]
        logging.info(f"Extracting local subtitle track: {target_track}")
        
        # Extract
        success = await extract_subtitle(video_path, target_track.index, subtitle_path)
        if not success:
            return None
            
        return subtitle_path.read_bytes()
        
    finally:
        try:
            if subtitle_path.exists():
                subtitle_path.unlink()
            os.rmdir(temp_dir)
        except Exception as e:
            logging.warning(f"Failed to cleanup temp files: {e}")
