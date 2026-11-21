"""YouTube extraction and a disk-backed, single-encode audio pipeline."""
import base64
import binascii
import io
import json
import logging
import math
import re
import subprocess
import sys
import time
from pathlib import Path
from threading import Event
from urllib.parse import parse_qs, urlsplit

from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1
from mutagen.mp3 import MP3
from PIL import Image, UnidentifiedImageError

import config as c

logger = logging.getLogger(__name__)
Image.MAX_IMAGE_PIXELS = c.IMAGE_MAX_PIXELS


class TaskError(Exception):
    """An actionable, safe-to-display processing error."""


class Cancelled(TaskError):
    pass


def parse_links(text: str) -> list[str]:
    links = text.split()
    if not links:
        raise TaskError("Add at least one YouTube video link.")
    result = []
    for index, link in enumerate(links, 1):
        try:
            url = urlsplit(link)
            if url.scheme not in {"http", "https"} or url.hostname not in c.YOUTUBE_HOSTS:
                raise ValueError
            if url.username or url.password or url.port not in {None, 80, 443}:
                raise ValueError
            parts = url.path.strip("/").split("/")
            if url.hostname in {"youtu.be", "www.youtu.be"} and len(parts) == 1:
                video_id = parts[0]
            elif url.path == "/watch":
                video_id = parse_qs(url.query).get("v", [""])[0]
            elif len(parts) == 2 and parts[0] in {"shorts", "live", "embed"}:
                video_id = parts[1]
            else:
                raise ValueError
            if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                raise ValueError
        except ValueError as exc:
            raise TaskError(f"Check link {index}. Only individual YouTube video links are supported.") from exc
        result.append(c.YOUTUBE_VIDEO_URL.format(video_id=video_id))
    return result


def run_process(args: list[str], cancel: Event, timeout: int = c.PROCESS_TIMEOUT) -> tuple[str, str]:
    if cancel.is_set():
        raise Cancelled("Task cancelled.")
    started = time.monotonic()
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags) as process:
            try:
                while True:
                    if cancel.is_set():
                        raise Cancelled("Task cancelled.")
                    if time.monotonic() - started > timeout:
                        raise TaskError("Processing timed out. Check your connection and video length, then try again.")
                    try:
                        stdout, stderr = process.communicate(timeout=c.POLL_SECONDS)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
    except OSError as exc:
        logger.error("Unable to start media tool: %s", type(exc).__name__)
        raise TaskError("Unable to start a media tool. Check the application files and tool configuration.") from exc
    out, err = stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace")
    if process.returncode:
        logger.error("Media tool failed with exit code %s", process.returncode)
        lower = err.lower()
        if "sign in" in lower or "confirm you’re not a bot" in lower or "403" in lower:
            raise TaskError("YouTube restricted access. Try again later or use another public video.")
        if any(word in lower for word in ("private video", "unavailable", "not available", "removed", "members-only")):
            raise TaskError("This video is unavailable, private, deleted, or region restricted.")
        if any(word in lower for word in ("resolve", "timed out", "connection", "network")):
            raise TaskError("Unable to connect. Check your internet connection and try again.")
        if "no space" in lower or "disk full" in lower:
            raise TaskError("Not enough disk space. Free up some space and try again.")
        raise TaskError("Media processing failed. Check that the video is available and the media tools are installed.")
    return out, err


def youtube_args() -> list[str]:
    # pythonw has no console streams; the child gets explicit pipes and CREATE_NO_WINDOW.
    python = Path(sys.executable)
    if python.name.lower() == "pythonw.exe":
        python = python.with_name("python.exe")
    args = ([c.YTDLP] if c.YTDLP else [str(python), "-m", "yt_dlp"]) + ["--ignore-config", "--no-cache-dir", "--no-playlist", "--no-progress", "--no-warnings",
            "--socket-timeout", str(c.NETWORK_TIMEOUT), "--retries", str(c.DOWNLOAD_RETRIES), "--extractor-retries", "1"]
    if c.FFMPEG:
        args += ["--ffmpeg-location", str(Path(c.FFMPEG).parent)]
    if c.NODE:
        args += ["--js-runtimes", f"node:{c.NODE}"]
    return args


def inspect_video(url: str, cancel: Event) -> dict:
    out, _ = run_process(youtube_args() + ["--skip-download", "--dump-single-json", "--", url], cancel, c.METADATA_TIMEOUT)
    try:
        info = json.loads(out)
        duration = float(info.get("duration") or 0)
        if info.get("is_live") or info.get("live_status") in {"is_upcoming", "is_live", "post_live"}:
            raise TaskError("Live or scheduled streams are not supported. Use a completed upload.")
        if not math.isfinite(duration) or duration <= 0:
            raise TaskError("Unable to determine the video duration. Try another video.")
        return {"url": url, "title": str(info.get("title") or "Untitled video"), "duration": duration}
    except (ValueError, TypeError, AttributeError) as exc:
        raise TaskError("Unable to read video information. Please try again.") from exc


def download_audio(url: str, directory: Path, cancel: Event) -> Path:
    out, _ = run_process(youtube_args() + ["-f", "bestaudio/best", "--no-simulate", "--print", "after_move:filepath",
                                         "-o", str(directory / "source.%(ext)s"), "--", url], cancel)
    lines = out.strip().splitlines()
    path = Path(lines[-1]).resolve() if lines else directory / "missing"
    if path.parent != directory.resolve() or not path.is_file():
        raise TaskError("The downloaded audio file could not be found.")
    return path


def probe_duration(path: Path, cancel: Event) -> float:
    out, _ = run_process([c.FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)], cancel)
    try:
        duration = float(json.loads(out)["format"]["duration"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError
        return duration
    except (KeyError, ValueError, TypeError) as exc:
        raise TaskError("Unable to read the audio duration.") from exc


def edge_bounds(log: str, duration: float) -> tuple[float, float]:
    """Only boundary silence is removable; internal musical rests stay intact."""
    spans = []
    start = None
    for event, value in re.findall(r"silence_(start|end):\s*(-?[\d.]+(?:e[+-]?\d+)?)", log):
        number = float(value)
        if event == "start":
            start = max(0.0, number)
        elif start is not None:
            spans.append((start, number))
            start = None
    if start is not None:
        spans.append((start, duration))
    left, right = 0.0, duration
    if spans and spans[0][0] <= c.EDGE_TOLERANCE:
        left = min(duration, spans[0][1])
    if spans and spans[-1][1] >= duration - c.EDGE_TOLERANCE:
        right = max(0.0, spans[-1][0])
    if right <= left:
        raise TaskError("No non-silent audio was found. Remove this video and try again.")
    return left, right


def trim_audio(source: Path, target: Path, cancel: Event) -> dict:
    duration = probe_duration(source, cancel)
    # FFmpeg silencedetect reports intervals without altering internal pauses.
    _, log = run_process([c.FFMPEG, "-hide_banner", "-nostdin", "-xerror", "-i", str(source), "-vn", "-af",
                          f"silencedetect=noise={c.SILENCE_DB}dB:d={c.SILENCE_SECONDS}", "-f", "null", "-"], cancel)
    start, end = edge_bounds(log, duration)
    run_process([c.FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-xerror", "-y", "-i", str(source),
                 "-map", "0:a:0", "-af", f"atrim=start={start}:end={end},asetpts=PTS-STARTPTS",
                 "-ar", str(c.SAMPLE_RATE), "-ac", str(c.CHANNELS), "-c:a", "pcm_s16le", "-rf64", "auto", "-map_metadata", "-1", str(target)], cancel)
    actual = probe_duration(target, cancel)
    return {"source_duration": duration, "duration": actual, "removed": max(0.0, duration - actual)}


def decode_cover(data_url: str | None) -> bytes | None:
    if data_url is None:
        return None
    if not data_url.startswith("data:image/jpeg;base64,"):
        raise TaskError("Apply the cover again using the square crop editor.")
    try:
        data = base64.b64decode(data_url.split(",", 1)[1], validate=True)
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "JPEG" or image.width != image.height or image.width > c.COVER_SIZE:
                raise TaskError("The cover must be a square JPEG image.")
            image.load()
            output = io.BytesIO()
            image.convert("RGB").resize((c.COVER_SIZE, c.COVER_SIZE)).save(output, "JPEG", quality=90)
            return output.getvalue()
    except (ValueError, binascii.Error, UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise TaskError("Unable to read the image. Choose another cover.") from exc


def merge_audio(directory: Path, output: Path, cancel: Event, *, bitrate: str = c.BITRATE) -> float:
    run_process([c.FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-xerror", "-y", "-f", "concat", "-safe", "1",
                 "-i", str(directory / "tracks.txt"), "-vn", "-c:a", "libmp3lame", "-b:a", bitrate,
                 "-ar", str(c.SAMPLE_RATE), "-ac", str(c.CHANNELS), "-map_metadata", "-1", str(output)], cancel)
    return MP3(output).info.length


def write_tags(path: Path, tags: dict, cover: bytes | None) -> None:
    metadata = ID3()
    for key, frame in (("title", TIT2), ("artist", TPE1), ("album", TALB)):
        metadata.add(frame(encoding=1, text=tags[key]))
    if cover:
        metadata.add(APIC(encoding=1, mime="image/jpeg", type=3, desc="Cover", data=cover))
    metadata.save(path, v2_version=3)
