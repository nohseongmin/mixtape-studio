"""Defaults for this local-only application; environment overrides live here."""
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("MIXTAPE_DATA_DIR", ROOT / "data")).resolve()
OUTPUT_DIR = Path(os.environ.get("MIXTAPE_OUTPUT_DIR", ROOT / "outputs")).resolve()
HOST = "127.0.0.1"
PORT = int(os.environ.get("MIXTAPE_PORT", "8765"))
FFMPEG = os.environ.get("MIXTAPE_FFMPEG") or shutil.which("ffmpeg")
FFPROBE = os.environ.get("MIXTAPE_FFPROBE") or shutil.which("ffprobe")
NODE = os.environ.get("MIXTAPE_NODE") or shutil.which("node")
NETWORK_TIMEOUT = int(os.environ.get("MIXTAPE_NETWORK_TIMEOUT", "20"))
METADATA_TIMEOUT = int(os.environ.get("MIXTAPE_METADATA_TIMEOUT", "120"))
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
COVER_SIZE = 800
IMAGE_MAX_BYTES = 20 * 1024 * 1024
IMAGE_MAX_PIXELS = 40_000_000
REQUEST_MAX_BYTES = 8 * 1024 * 1024
LINKS_MAX_CHARS = 200_000
TAG_MAX_CHARS = 200
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"}
YOUTUBE_VIDEO_URL = "https://www.youtube.com/watch?v={video_id}"

if not 0 <= PORT <= 65535 or min(NETWORK_TIMEOUT, METADATA_TIMEOUT, PROCESS_TIMEOUT) <= 0:
    raise ValueError("포트와 시간 제한 환경변수를 확인하세요.")
