import copy
import logging
import re
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

import audio
import config as c

logger = logging.getLogger(__name__)
jobs = {}
lock = threading.RLock()
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mixtape")
active_job = None


@asynccontextmanager
async def lifespan(app):
    c.DATA_DIR.mkdir(parents=True, exist_ok=True)
    c.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    yield
    with lock:
        for job in jobs.values():
            job["cancel"].set()
    executor.shutdown(wait=True, cancel_futures=True)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[c.HOST, "localhost"])


@app.middleware("http")
async def local_only(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (origin and origin != str(request.base_url).rstrip("/")):
        return JSONResponse({"detail": "이 앱의 로컬 화면에서 다시 시도해주세요."}, status_code=403)
    if request.method == "POST":
        if request.headers.get("x-mixtape-request") != "1":
            return JSONResponse({"detail": "앱 화면에서 요청해주세요."}, status_code=403)
        try:
            if int(request.headers.get("content-length", "0")) > c.REQUEST_MAX_BYTES:
                return JSONResponse({"detail": "입력 데이터가 너무 커요."}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "잘못된 요청이에요."}, status_code=400)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > c.REQUEST_MAX_BYTES:
                return JSONResponse({"detail": "입력 데이터가 너무 커요."}, status_code=413)
        request._body = bytes(body)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; media-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse({"detail": "입력 형식이나 길이를 확인해주세요. 제목·가수·앨범은 각각 200자까지 입력할 수 있어요."}, status_code=422)


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


def update_job(job_id, **values):
    with lock:
        jobs[job_id].update(values)


def start_job(kind, task, payload):
    global active_job
    with lock:
        if active_job is not None:
            raise HTTPException(409, "다른 작업이 진행 중이에요. 완료하거나 취소한 뒤 다시 시도하세요.")
        job_id = uuid.uuid4().hex
        jobs[job_id] = {"id": job_id, "kind": kind, "status": "running", "progress": 0,
                        "message": "작업 준비 중", "tracks": [], "cancel": threading.Event()}
        active_job = job_id
        executor.submit(run_job, job_id, task, payload)
        return {"id": job_id}


def run_job(job_id, task, payload):
    global active_job
    try:
        task(job_id, payload)
        if jobs[job_id]["cancel"].is_set():
            raise audio.Cancelled("작업을 취소했어요.")
        update_job(job_id, status="done", progress=100, message="완료했어요.")
    except audio.Cancelled as exc:
        update_job(job_id, status="cancelled", message=str(exc))
    except audio.TaskError as exc:
        update_job(job_id, status="error", message=f"{jobs[job_id]['message']} — {exc}")
    except Exception as exc:
        # The worker boundary must release the queue even after an unexpected library error.
        logger.error("Job %s failed: %s", job_id, type(exc).__name__)
        update_job(job_id, status="error", message="처리를 마치지 못했어요. 저장 공간과 실행 환경을 확인한 뒤 다시 시도하세요.")
    finally:
        with lock:
            active_job = None


def analyze_job(job_id, links):
    tracks = []
    for index, url in enumerate(links):
        update_job(job_id, progress=100 * index / len(links), message=f"{index + 1}/{len(links)} · 영상 정보 확인 중")
        try:
            track = audio.inspect_video(url, jobs[job_id]["cancel"])
        except audio.Cancelled:
            raise
        except audio.TaskError as exc:
            track = {"url": url, "title": f"{index + 1}번째 영상", "duration": 0, "error": str(exc)}
        tracks.append({"id": uuid.uuid4().hex, **track})
        update_job(job_id, tracks=list(tracks), expected_duration=sum(item["duration"] for item in tracks))


def render_job(job_id, payload):
    tracks, tags, cover = payload
    cancel = jobs[job_id]["cancel"]
    directory = c.DATA_DIR / job_id
    directory.mkdir(parents=True)
    output = c.OUTPUT_DIR / f"{job_id}.mp3"
    completed = []
    try:
        for index, track in enumerate(tracks):
            update_job(job_id, progress=90 * index / len(tracks), message=f"{index + 1}/{len(tracks)} · 오디오 다운로드: {track['title']}")
            track_dir = directory / str(index)
            track_dir.mkdir()
            source = audio.download_audio(track["url"], track_dir, cancel)
            update_job(job_id, message=f"{index + 1}/{len(tracks)} · 앞뒤 무음 제거: {track['title']}")
            result = audio.trim_audio(source, directory / f"{index}.wav", cancel)
            source.unlink()
            completed.append({**track, **result})
            update_job(job_id, tracks=list(completed), trimmed_duration=sum(item["duration"] for item in completed),
                       removed=sum(item["removed"] for item in completed))
        (directory / "tracks.txt").write_text("".join(f"file '{index}.wav'\n" for index in range(len(tracks))), encoding="utf-8")
        update_job(job_id, progress=92, message="전체 오디오를 하나의 MP3로 합치는 중")
        duration = audio.merge_audio(directory, output, cancel)
        if abs(duration - sum(item["duration"] for item in completed)) > c.ENCODE_TOLERANCE:
            raise audio.TaskError("완성 파일의 길이가 곡 합계와 달라요. 불완전한 결과를 저장하지 않았어요. 다시 시도하세요.")
        update_job(job_id, progress=98, message="제목·가수·앨범·표지 저장 중")
        audio.write_tags(output, tags, cover)
        if cancel.is_set():
            raise audio.Cancelled("작업을 취소했어요.")
        filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", tags["title"]).strip(" .") or "mixtape"
        update_job(job_id, duration=duration, size=output.stat().st_size, filename=f"{filename}.mp3", output=output)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    finally:
        try:
            shutil.rmtree(directory)
        except OSError as exc:
            logger.warning("Temporary audio cleanup failed: %s", type(exc).__name__)
            update_job(job_id, cleanup_warning="임시 파일 일부를 지우지 못했어요. 앱을 종료한 뒤 data 폴더를 정리해주세요.")


@app.get("/api/config")
def configuration():
    missing = [name for name, path in (("FFmpeg", c.FFMPEG), ("FFprobe", c.FFPROBE), ("Node.js", c.NODE)) if not path]
    return {"app": "mixtape-studio", "missing": missing, "cover_size": c.COVER_SIZE, "image_max_bytes": c.IMAGE_MAX_BYTES,
            "image_max_pixels": c.IMAGE_MAX_PIXELS, "links_max_chars": c.LINKS_MAX_CHARS,
            "tag_max_chars": c.TAG_MAX_CHARS, "silence_db": c.SILENCE_DB, "bitrate": c.BITRATE,
            "active_job": active_job}


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
        raise HTTPException(503, "필수 처리 도구가 없어요. 시작 화면의 설치 안내를 확인하세요.")
    with lock:
        analysis = jobs.get(data.analysis_id.hex)
        if not analysis or analysis["kind"] != "analyze" or analysis["status"] != "done":
            raise HTTPException(422, "먼저 링크 목록을 확인해주세요.")
        available = {item["id"]: item for item in analysis["tracks"]}
        ids = [value.hex for value in data.track_ids]
        if len(set(ids)) != len(ids) or any(value not in available for value in ids):
            raise HTTPException(422, "곡 목록이 올바르지 않아요. 링크를 다시 확인해주세요.")
        tracks = [available[value] for value in ids]
        if any(item.get("error") for item in tracks):
            raise HTTPException(422, "가져올 수 없는 영상을 목록에서 제거하거나 링크를 다시 확인해주세요.")
    try:
        cover = audio.decode_cover(data.cover)
    except audio.TaskError as exc:
        raise HTTPException(422, str(exc)) from exc
    tags = {name: getattr(data, name) for name in ("title", "artist", "album")}
    return start_job("render", render_job, (tracks, tags, cover))


@app.get("/api/jobs/{job_id}")
def get_job(job_id: uuid.UUID):
    with lock:
        job = jobs.get(job_id.hex)
        if not job:
            raise HTTPException(404, "이 작업을 찾을 수 없어요. 앱을 다시 실행했다면 링크를 다시 확인해주세요.")
        return copy.deepcopy({key: value for key, value in job.items() if key not in {"cancel", "output"}})


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: uuid.UUID):
    with lock:
        job = jobs.get(job_id.hex)
        if not job:
            raise HTTPException(404, "작업을 찾을 수 없어요.")
        if job["status"] == "running":
            job["cancel"].set()
    return {"ok": True}


@app.get("/api/jobs/{job_id}/download")
def download(job_id: uuid.UUID):
    with lock:
        job = jobs.get(job_id.hex)
        if not job or job["kind"] != "render" or job["status"] != "done" or not job["output"].is_file():
            raise HTTPException(404, "완성된 MP3 파일이 없어요.")
        return FileResponse(job["output"], media_type="audio/mpeg", filename=job["filename"])


app.mount("/", StaticFiles(directory=c.ROOT / "static", html=True), name="static")
