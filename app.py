import copy
import logging
import re
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

import audio
import config as c

logger = logging.getLogger(__name__)
jobs = {}
lock = threading.RLock()
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mixtape")
active_job = None
is_stopping = False


def cancel_jobs():
    global is_stopping
    with lock:
        is_stopping = True
        for job in jobs.values():
            job["cancel"].set()


@asynccontextmanager
async def lifespan(app):
    global is_stopping
    is_stopping = False
    c.DATA_DIR.mkdir(parents=True, exist_ok=True)
    c.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    yield
    cancel_jobs()
    executor.shutdown(wait=True, cancel_futures=True)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[c.HOST, "localhost"])


@app.middleware("http")
async def local_only(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin != str(request.base_url).rstrip("/")):
        return JSONResponse({"detail": "Please try again from the local application window."}, status_code=403)
    if request.method == "POST":
        if request.headers.get("x-mixtape-request") != "1":
            return JSONResponse({"detail": "Requests must come from the application window."}, status_code=403)
        try:
            if int(request.headers.get("content-length", "0")) > c.REQUEST_MAX_BYTES:
                return JSONResponse({"detail": "The input is too large."}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "Invalid request."}, status_code=400)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > c.REQUEST_MAX_BYTES:
                return JSONResponse({"detail": "The input is too large."}, status_code=413)
        request._body = bytes(body)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; media-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse({"detail": "Check the input format and length. Title, artist, and album accept up to 200 characters each."}, status_code=422)


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AnalyzeInput(InputModel):
    links: str = Field(min_length=1, max_length=c.LINKS_MAX_CHARS, strict=True)


class RenderInput(InputModel):
    analysis_id: uuid.UUID
    track_ids: list[uuid.UUID] = Field(min_length=1)
    title: str = Field(min_length=1, max_length=c.TAG_MAX_CHARS, strict=True)
    artist: str = Field(default="", max_length=c.TAG_MAX_CHARS, strict=True)
    album: str = Field(default="", max_length=c.TAG_MAX_CHARS, strict=True)
    cover: str | None = Field(default=None, max_length=c.REQUEST_MAX_BYTES)
    bitrate: str = Field(default=c.BITRATE, strict=True)

    @field_validator("bitrate")
    @classmethod
    def supported_bitrate(cls, value):
        if value not in c.MP3_BITRATES:
            raise ValueError("Unsupported MP3 bitrate.")
        return value


def update_job(job_id, **values):
    with lock:
        jobs[job_id].update(values)


def start_job(kind, task, payload):
    global active_job
    with lock:
        if is_stopping:
            raise HTTPException(503, "The application is shutting down.")
        if active_job is not None:
            raise HTTPException(409, "Another task is running. Wait for it to finish or cancel it first.")
        job_id = uuid.uuid4().hex
        jobs[job_id] = {"id": job_id, "kind": kind, "status": "running", "progress": 0,
                        "message": "Getting ready", "tracks": [], "cancel": threading.Event()}
        active_job = job_id
        executor.submit(run_job, job_id, task, payload)
        return {"id": job_id}


def run_job(job_id, task, payload):
    global active_job
    try:
        task(job_id, payload)
        if jobs[job_id]["cancel"].is_set():
            raise audio.Cancelled("Task cancelled.")
        update_job(job_id, status="done", progress=100, message="Done.")
    except audio.Cancelled as exc:
        update_job(job_id, status="cancelled", message=str(exc))
    except audio.TaskError as exc:
        update_job(job_id, status="error", message=f"{jobs[job_id]['message']} — {exc}")
    except Exception as exc:
        # The worker boundary must release the queue even after an unexpected library error.
        logger.error("Job %s failed: %s", job_id, type(exc).__name__)
        update_job(job_id, status="error", message="Processing could not finish. Check disk space and the application setup, then try again.")
    finally:
        with lock:
            active_job = None


def analyze_job(job_id, links):
    cancel = jobs[job_id]["cancel"]
    tracks = [{"id": uuid.uuid4().hex, "url": url, "title": "Waiting for video details", "duration": 0,
               "pending": True} for url in links]
    with lock:
        known = {track["url"]: {key: track[key] for key in ("url", "title", "duration")}
                 for job in jobs.values() if job["kind"] == "analyze"
                 for track in job["tracks"] if not track.get("pending") and not track.get("error")}
    missing = [url for url in dict.fromkeys(links) if url not in known]

    def publish():
        for track in tracks:
            if track["url"] in known:
                track.update(known[track["url"]], pending=False)
        completed = sum(not track["pending"] for track in tracks)
        update_job(job_id, tracks=copy.deepcopy(tracks), expected_duration=sum(t["duration"] for t in tracks),
                   progress=100 * completed / len(tracks),
                   message=f"{completed}/{len(tracks)} · checked")

    publish()
    with ThreadPoolExecutor(max_workers=c.METADATA_WORKERS, thread_name_prefix="metadata") as pool:
        futures = {pool.submit(audio.inspect_video, url, cancel): url for url in missing}
        try:
            for future in as_completed(futures):
                if cancel.is_set():
                    raise audio.Cancelled("Task cancelled.")
                url = futures[future]
                try:
                    track = future.result()
                except audio.Cancelled:
                    raise
                except audio.TaskError as exc:
                    track = {"url": url, "title": "Video lookup failed", "duration": 0, "error": str(exc)}
                known[url] = track
                publish()
        except Exception:
            cancel.set()
            for future in futures:
                future.cancel()
            raise


def render_job(job_id, payload):
    tracks, tags, cover, bitrate = payload
    cancel = jobs[job_id]["cancel"]
    directory = c.DATA_DIR / job_id
    directory.mkdir(parents=True)
    output = c.OUTPUT_DIR / f"{job_id}.mp3"
    completed = []
    try:
        for index, track in enumerate(tracks):
            update_job(job_id, progress=90 * index / len(tracks), message=f"{index + 1}/{len(tracks)} · Downloading audio: {track['title']}")
            track_dir = directory / str(index)
            track_dir.mkdir()
            source = audio.download_audio(track["url"], track_dir, cancel)
            update_job(job_id, message=f"{index + 1}/{len(tracks)} · Trimming edge silence: {track['title']}")
            result = audio.trim_audio(source, directory / f"{index}.wav", cancel)
            source.unlink()
            completed.append({**track, **result})
            update_job(job_id, tracks=list(completed), trimmed_duration=sum(item["duration"] for item in completed),
                       removed=sum(item["removed"] for item in completed))
        (directory / "tracks.txt").write_text("".join(f"file '{index}.wav'\n" for index in range(len(tracks))), encoding="utf-8")
        update_job(job_id, progress=92, message="Combining all tracks into one MP3")
        duration = audio.merge_audio(directory, output, cancel, bitrate=bitrate)
        if abs(duration - sum(item["duration"] for item in completed)) > c.ENCODE_TOLERANCE:
            raise audio.TaskError("The output duration does not match the track total. The incomplete file was discarded. Please try again.")
        update_job(job_id, progress=98, message="Writing title, artist, album, and cover")
        audio.write_tags(output, tags, cover)
        if cancel.is_set():
            raise audio.Cancelled("Task cancelled.")
        filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", tags["title"]).strip(" .") or "mixtape"
        update_job(job_id, duration=duration, size=output.stat().st_size, filename=f"{filename}.mp3", output=output, bitrate=bitrate)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    finally:
        try:
            shutil.rmtree(directory)
        except OSError as exc:
            logger.warning("Temporary audio cleanup failed: %s", type(exc).__name__)
            update_job(job_id, cleanup_warning="Some temporary files could not be removed. Close the app before cleaning the data folder.")


@app.get("/api/config")
def configuration():
    missing = [name for name, path in (("FFmpeg", c.FFMPEG), ("FFprobe", c.FFPROBE), ("Node.js", c.NODE)) if not path]
    return {"app": "mixtape-studio", "missing": missing, "cover_size": c.COVER_SIZE, "image_max_bytes": c.IMAGE_MAX_BYTES,
            "image_max_pixels": c.IMAGE_MAX_PIXELS, "links_max_chars": c.LINKS_MAX_CHARS,
            "tag_max_chars": c.TAG_MAX_CHARS, "silence_db": c.SILENCE_DB, "bitrate": c.BITRATE,
            "bitrates": c.MP3_BITRATES, "active_job": active_job}


@app.post("/api/analyze")
def analyze(data: AnalyzeInput):
    try:
        links = audio.parse_links(data.links)
    except audio.TaskError as exc:
        raise HTTPException(422, str(exc)) from exc
    return start_job("analyze", analyze_job, links)


@app.post("/api/render")
def render(data: RenderInput):
    if configuration()["missing"]:
        raise HTTPException(503, "Required media tools are missing. See the setup instructions.")
    with lock:
        analysis = jobs.get(data.analysis_id.hex)
        if not analysis or analysis["kind"] != "analyze" or analysis["status"] != "done":
            raise HTTPException(422, "Wait for the video links to be checked first.")
        available = {item["id"]: item for item in analysis["tracks"]}
        ids = [value.hex for value in data.track_ids]
        if len(set(ids)) != len(ids) or any(value not in available for value in ids):
            raise HTTPException(422, "The track list is invalid. Check the video links again.")
        tracks = [available[value] for value in ids]
        if any(item.get("error") for item in tracks):
            raise HTTPException(422, "Remove unavailable videos or check their links again.")
    try:
        cover = audio.decode_cover(data.cover)
    except audio.TaskError as exc:
        raise HTTPException(422, str(exc)) from exc
    tags = {name: getattr(data, name) for name in ("title", "artist", "album")}
    return start_job("render", render_job, (tracks, tags, cover, data.bitrate))


@app.get("/api/jobs/{job_id}")
def get_job(job_id: uuid.UUID):
    with lock:
        job = jobs.get(job_id.hex)
        if not job:
            raise HTTPException(404, "Task not found. If you restarted the app, enter the links again.")
        return copy.deepcopy({key: value for key, value in job.items() if key not in {"cancel", "output"}})


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: uuid.UUID):
    with lock:
        job = jobs.get(job_id.hex)
        if not job:
            raise HTTPException(404, "Task not found.")
        if job["status"] == "running":
            job["cancel"].set()
    return {"ok": True}


@app.get("/api/jobs/{job_id}/download")
def download(job_id: uuid.UUID):
    with lock:
        job = jobs.get(job_id.hex)
        if not job or job["kind"] != "render" or job["status"] != "done" or not job["output"].is_file():
            raise HTTPException(404, "No completed MP3 file is available.")
        return FileResponse(job["output"], media_type="audio/mpeg", filename=job["filename"])


app.mount("/", StaticFiles(directory=c.ROOT / "static", html=True), name="static")
