"""Defaults for this local-only application; environment overrides live here."""
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FROZEN = getattr(sys, "frozen", False)
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else ROOT
DATA_DIR = Path(os.environ.get("MIXTAPE_DATA_DIR", APP_DIR / "data")).resolve()
OUTPUT_DIR = Path(os.environ.get("MIXTAPE_OUTPUT_DIR", APP_DIR / "outputs")).resolve()
HOST = "127.0.0.1"
PORT = int(os.environ.get("MIXTAPE_PORT", "0" if FROZEN else "8765"))
WINDOW_TITLE = "Mixtape"
WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 880
WINDOW_MIN_SIZE = (760, 600)
STARTUP_SECONDS = 15
SHUTDOWN_SECONDS = 5
LOG_MAX_BYTES = 1_000_000
FFMPEG = os.environ.get("MIXTAPE_FFMPEG") or (str(ROOT / "tools" / "ffmpeg.exe") if FROZEN else shutil.which("ffmpeg"))
FFPROBE = os.environ.get("MIXTAPE_FFPROBE") or (str(ROOT / "tools" / "ffprobe.exe") if FROZEN else shutil.which("ffprobe"))
NODE = os.environ.get("MIXTAPE_NODE") or (str(ROOT / "tools" / "node.exe") if FROZEN else shutil.which("node"))
YTDLP = str(ROOT / "tools" / "yt-dlp.exe") if FROZEN else None
NETWORK_TIMEOUT = int(os.environ.get("MIXTAPE_NETWORK_TIMEOUT", "20"))
METADATA_TIMEOUT = int(os.environ.get("MIXTAPE_METADATA_TIMEOUT", "120"))
METADATA_WORKERS = 4
PROCESS_TIMEOUT = int(os.environ.get("MIXTAPE_PROCESS_TIMEOUT", "7200"))
POLL_SECONDS = 0.25
DOWNLOAD_RETRIES = 2
SILENCE_DB = -50
SILENCE_SECONDS = 0.12
EDGE_TOLERANCE = 0.04
ENCODE_TOLERANCE = 0.15
SAMPLE_RATE = 44100
CHANNELS = 2
BITRATE = "192k"
MP3_BITRATES = ("128k", "192k", "256k", "320k")
COVER_SIZE = 800
IMAGE_MAX_BYTES = 20 * 1024 * 1024
IMAGE_MAX_PIXELS = 40_000_000
REQUEST_MAX_BYTES = 8 * 1024 * 1024
LINKS_MAX_CHARS = 200_000
TAG_MAX_CHARS = 200
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"}
YOUTUBE_VIDEO_URL = "https://www.youtube.com/watch?v={video_id}"

if not 0 <= PORT <= 65535 or min(NETWORK_TIMEOUT, METADATA_TIMEOUT, PROCESS_TIMEOUT) <= 0:
    raise ValueError("Check the port and timeout environment variables.")
