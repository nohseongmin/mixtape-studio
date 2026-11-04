import array
import base64
import io
import math
import re
import shutil
import sys
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from mutagen.id3 import ID3
from mutagen.mp3 import MP3
from PIL import Image
from pydantic import ValidationError

import app
import audio
import config as c

VIDEO_A = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
VIDEO_B = "https://www.youtube.com/watch?v=bbbbbbbbbbb"
HEADERS = {"X-Mixtape-Request": "1"}


def make_wave(path, segments, rate=44100):
    samples = array.array("h")
    for seconds, frequency in segments:
        samples.extend(int(12000 * math.sin(2 * math.pi * frequency * index / rate)) if frequency else 0
                       for index in range(round(seconds * rate)))
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(samples.tobytes())
    return path


def cover_url(size=(100, 100)):
    buffer = io.BytesIO()
    Image.new("RGB", size, (35, 130, 90)).save(buffer, "JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def wait_job(client, job_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        result = client.get(f"/api/jobs/{job_id}").json()
        if result["status"] != "running":
            return result
        time.sleep(0.02)
    pytest.fail("Job did not terminate")


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(c, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(c, "OUTPUT_DIR", tmp_path / "outputs")
    monkeypatch.setattr(app, "executor", ThreadPoolExecutor(max_workers=1))
    monkeypatch.setattr(app, "jobs", {})
    monkeypatch.setattr(app, "active_job", None)
    with TestClient(app.app, base_url="http://127.0.0.1") as test_client:
        yield test_client


def test_links_keep_order_duplicates_and_more_than_300():
    assert audio.parse_links(f"https://youtu.be/aaaaaaaaaaa?si=abc\nhttps://music.youtube.com/watch?v=bbbbbbbbbbb&list=ignored\n{VIDEO_A}") == [VIDEO_A, VIDEO_B, VIDEO_A]
    assert len(audio.parse_links("\n".join([VIDEO_A] * 301))) == 301


@pytest.mark.parametrize("url", ["", "file:///tmp/audio.mp3", "https://youtube.com.evil.test/watch?v=aaaaaaaaaaa",
                                 "https://user:pass@youtube.com/watch?v=aaaaaaaaaaa", "https://youtube.com:8080/watch?v=aaaaaaaaaaa",
                                 "https://youtube.com/playlist?list=test", "https://youtube.com/watch?v=../bad"])
def test_invalid_urls_rejected(url):
    with pytest.raises(audio.TaskError):
        audio.parse_links(url)


def test_edge_trimming_preserves_internal_pause(tmp_path):
    source = make_wave(tmp_path / "input.wav", [(0.4, 0), (0.4, 440), (0.3, 0), (0.4, 440), (0.5, 0)])
    output = tmp_path / "trimmed.wav"
    result = audio.trim_audio(source, output, threading.Event())
    assert result["duration"] == pytest.approx(1.1, abs=0.01)
    assert result["removed"] == pytest.approx(0.9, abs=0.01)
    _, log = audio.run_process([c.FFMPEG, "-i", str(output), "-af", "silencedetect=noise=-50dB:d=0.12", "-f", "null", "-"], threading.Event())
    assert float(re.search(r"silence_start: ([\d.]+)", log)[1]) == pytest.approx(0.4, abs=0.001)
    assert float(re.search(r"silence_end: ([\d.]+)", log)[1]) == pytest.approx(0.7, abs=0.001)


def test_all_silent_track_is_an_error(tmp_path):
    source = make_wave(tmp_path / "silence.wav", [(1, 0)])
    with pytest.raises(audio.TaskError, match="non-silent audio"):
        audio.trim_audio(source, tmp_path / "trimmed.wav", threading.Event())


def test_non_silent_edges_remain():
    assert audio.edge_bounds("silence_start: 1\nsilence_end: 2", 3) == (0, 3)
    assert audio.edge_bounds("", 3) == (0, 3)


def test_cover_validated_and_reencoded():
    with Image.open(io.BytesIO(audio.decode_cover(cover_url()))) as image:
        assert image.size == (c.COVER_SIZE, c.COVER_SIZE)
        assert image.format == "JPEG"
    for invalid in (cover_url((200, 100)), "data:image/jpeg;base64,!!!", "data:image/svg+xml;base64,AAAA"):
        with pytest.raises(audio.TaskError):
            audio.decode_cover(invalid)


def test_local_request_security_and_input_limits(client):
    assert client.get("/").status_code == 200
    assert "frame-ancestors 'none'" in client.get("/").headers["content-security-policy"]
    assert client.get("/api/config", headers={"Host": "evil.test"}).status_code == 400
    assert client.get("/api/config", headers={"Origin": "https://evil.test"}).status_code == 403
    assert client.post("/api/analyze", json={"links": VIDEO_A}).status_code == 403
    assert client.post("/api/analyze", json={"links": VIDEO_A}, headers={**HEADERS, "Origin": "https://evil.test"}).status_code == 403
    assert client.post("/api/analyze", json={"links": "x" * (c.LINKS_MAX_CHARS + 1)}, headers=HEADERS).status_code == 422
    assert client.post("/api/analyze", json={"links": VIDEO_A}, headers={**HEADERS, "Content-Length": str(c.REQUEST_MAX_BYTES + 1)}).status_code == 413
    assert client.get("/api/jobs/not-a-uuid/download").status_code == 422


@pytest.mark.parametrize("bitrate", c.MP3_BITRATES)
def test_end_to_end_real_encoding_tags_cover_order_and_cleanup(client, monkeypatch, tmp_path, bitrate):
    first = make_wave(tmp_path / "first.wav", [(0.4, 0), (0.4, 440), (0.3, 0), (0.4, 440), (0.5, 0)])
    second = make_wave(tmp_path / "second.wav", [(0.25, 0), (0.6, 880), (0.35, 0)], rate=48000)
    sources = {VIDEO_A: first, VIDEO_B: second}

    def inspect(url, cancel):
        return {"url": url, "title": "First track" if url == VIDEO_A else "Second track", "duration": 2.0 if url == VIDEO_A else 1.2}

    def download(url, directory, cancel):
        target = directory / "source.wav"
        shutil.copyfile(sources[url], target)
        return target

    monkeypatch.setattr(audio, "inspect_video", inspect)
    monkeypatch.setattr(audio, "download_audio", download)
    analyzed = client.post("/api/analyze", json={"links": VIDEO_A + "\n" + VIDEO_B}, headers=HEADERS).json()
    analysis = wait_job(client, analyzed["id"])
    assert analysis["expected_duration"] == 3.2
    request = {"analysis_id": analysis["id"], "track_ids": [track["id"] for track in reversed(analysis["tracks"])],
               "title": "Midnight walk — café", "artist": "Various artists", "album": "My album", "cover": cover_url(), "bitrate": bitrate}
    result_id = client.post("/api/render", json=request, headers=HEADERS).json()["id"]
    result = wait_job(client, result_id)
    assert result["status"] == "done", result
    assert result["duration"] == pytest.approx(1.7, abs=0.08)
    assert result["removed"] == pytest.approx(1.5, abs=0.02)
    output = c.OUTPUT_DIR / f"{result_id}.mp3"
    assert result["bitrate"] == bitrate
    assert MP3(output).info.bitrate == pytest.approx(int(bitrate[:-1]) * 1000, abs=1000)
    tags = ID3(output)
    assert tags.version == (2, 3, 0)
    assert str(tags["TIT2"]) == "Midnight walk — café"
    assert str(tags["TPE1"]) == "Various artists"
    assert str(tags["TALB"]) == "My album"
    with Image.open(io.BytesIO(tags.getall("APIC")[0].data)) as image:
        assert image.size == (c.COVER_SIZE, c.COVER_SIZE)
    decoded = tmp_path / "decoded.wav"
    audio.run_process([c.FFMPEG, "-v", "error", "-i", str(output), "-ac", "1", str(decoded)], threading.Event())
    with wave.open(str(decoded), "rb") as stream:
        rate = stream.getframerate()
        samples = array.array("h", stream.readframes(stream.getnframes()))
    section = samples[round(rate * 0.1):round(rate * 0.3)]
    crossings = sum(left <= 0 < right for left, right in zip(section, section[1:]))
    assert crossings / 0.2 == pytest.approx(880, abs=10)
    assert not any(path.is_dir() for path in c.DATA_DIR.iterdir())
    downloaded = client.get(f"/api/jobs/{result_id}/download")
    assert downloaded.status_code == 200
    assert downloaded.content == output.read_bytes()
    ranged = client.get(f"/api/jobs/{result_id}/download", headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206


def test_301_tracks_and_more_than_one_hour_are_not_truncated(client, monkeypatch):
    monkeypatch.setattr(audio, "inspect_video", lambda url, cancel: {"url": url, "title": "Track", "duration": 240})
    job = client.post("/api/analyze", json={"links": "\n".join([VIDEO_A] * 301)}, headers=HEADERS).json()
    result = wait_job(client, job["id"])
    assert len(result["tracks"]) == 301
    assert result["expected_duration"] == 301 * 240


def test_failed_track_not_silently_skipped(client, monkeypatch):
    def unavailable(url, cancel):
        raise audio.TaskError("Unavailable video")
    monkeypatch.setattr(audio, "inspect_video", unavailable)
    job = client.post("/api/analyze", json={"links": VIDEO_A}, headers=HEADERS).json()
    result = wait_job(client, job["id"])
    assert result["tracks"][0]["error"]
    response = client.post("/api/render", json={"analysis_id": result["id"], "track_ids": [result["tracks"][0]["id"]], "title": "test"}, headers=HEADERS)
    assert response.status_code == 422


def test_cancel_stops_process():
    cancel = threading.Event()
    timer = threading.Timer(0.2, cancel.set)
    timer.start()
    start = time.monotonic()
    with pytest.raises(audio.Cancelled):
        audio.run_process([sys.executable, "-c", "import time; time.sleep(30)"], cancel)
    timer.join()
    assert time.monotonic() - start < 3


def test_busy_queue_cancellation_and_recovery(client, monkeypatch):
    def slow(url, cancel):
        if cancel.wait(5):
            raise audio.Cancelled("Task cancelled.")
        return {"url": url, "title": "Track", "duration": 1}
    monkeypatch.setattr(audio, "inspect_video", slow)
    job = client.post("/api/analyze", json={"links": VIDEO_A}, headers=HEADERS).json()
    assert client.post("/api/analyze", json={"links": VIDEO_A}, headers=HEADERS).status_code == 409
    client.post(f"/api/jobs/{job['id']}/cancel", json={}, headers=HEADERS)
    assert wait_job(client, job["id"])["status"] == "cancelled"
    assert app.active_job is None


def test_incomplete_encode_is_not_delivered(client, monkeypatch, tmp_path):
    source = make_wave(tmp_path / "tone.wav", [(0.5, 440)])
    monkeypatch.setattr(audio, "inspect_video", lambda url, cancel: {"url": url, "title": "Track", "duration": 0.5})

    def download(url, directory, cancel):
        target = directory / "source.wav"
        shutil.copyfile(source, target)
        return target

    def incomplete(directory, output, cancel, *, bitrate):
        output.write_bytes(b"incomplete")
        return 0.1

    monkeypatch.setattr(audio, "download_audio", download)
    monkeypatch.setattr(audio, "merge_audio", incomplete)
    job = client.post("/api/analyze", json={"links": VIDEO_A}, headers=HEADERS).json()
    analysis = wait_job(client, job["id"])
    render = client.post("/api/render", json={"analysis_id": job["id"], "track_ids": [analysis["tracks"][0]["id"]], "title": "test"}, headers=HEADERS).json()
    assert wait_job(client, render["id"])["status"] == "error"
    assert not any(c.OUTPUT_DIR.iterdir())
    assert not any(path.is_dir() for path in c.DATA_DIR.iterdir())
    assert client.get(f"/api/jobs/{render['id']}/download").status_code == 404


def test_metadata_reused_only_within_session_preserves_duplicates(client, monkeypatch):
    calls = []

    def inspect(url, cancel):
        calls.append(url)
        return {"url": url, "title": url[-11:], "duration": 100 if url == VIDEO_A else 200}

    monkeypatch.setattr(audio, "inspect_video", inspect)
    first = client.post("/api/analyze", json={"links": VIDEO_A}, headers=HEADERS).json()
    assert wait_job(client, first["id"])["expected_duration"] == 100
    second = client.post("/api/analyze", json={"links": f"{VIDEO_B}\nhttps://youtu.be/aaaaaaaaaaa\n{VIDEO_A}"}, headers=HEADERS).json()
    result = wait_job(client, second["id"])
    assert calls == [VIDEO_A, VIDEO_B]
    assert [track["url"] for track in result["tracks"]] == [VIDEO_B, VIDEO_A, VIDEO_A]
    assert len({track["id"] for track in result["tracks"]}) == 3
    assert result["expected_duration"] == 400
    assert not any(c.DATA_DIR.iterdir())
    monkeypatch.setattr(app, "jobs", {})
    restarted = client.post("/api/analyze", json={"links": VIDEO_A}, headers=HEADERS).json()
    assert wait_job(client, restarted["id"])["expected_duration"] == 100
    assert calls == [VIDEO_A, VIDEO_B, VIDEO_A]
    assert not any(c.DATA_DIR.iterdir())


def test_metadata_parallel_completion_preserves_order_and_queries_duplicates_once(client, monkeypatch):
    rendezvous = threading.Barrier(2, timeout=3)
    calls = []

    def inspect(url, cancel):
        calls.append(url)
        rendezvous.wait()
        return {"url": url, "title": "Track", "duration": 60}

    monkeypatch.setattr(audio, "inspect_video", inspect)
    job = client.post("/api/analyze", json={"links": f"{VIDEO_A}\n{VIDEO_B}\n{VIDEO_A}"}, headers=HEADERS).json()
    result = wait_job(client, job["id"])
    assert result["status"] == "done"
    assert sorted(calls) == [VIDEO_A, VIDEO_B]
    assert [track["url"] for track in result["tracks"]] == [VIDEO_A, VIDEO_B, VIDEO_A]
    assert result["expected_duration"] == 180


def test_metadata_failed_link_retries_without_refetching_successes(client, monkeypatch):
    calls = []

    def inspect(url, cancel):
        calls.append(url)
        if url == VIDEO_B and calls.count(VIDEO_B) == 1:
            raise audio.TaskError("Temporary failure")
        return {"url": url, "title": "Track", "duration": 60}

    monkeypatch.setattr(audio, "inspect_video", inspect)
    job = client.post("/api/analyze", json={"links": f"{VIDEO_A}\n{VIDEO_B}"}, headers=HEADERS).json()
    assert wait_job(client, job["id"])["tracks"][1]["error"]
    retry = client.post("/api/analyze", json={"links": f"{VIDEO_A}\n{VIDEO_B}"}, headers=HEADERS).json()
    result = wait_job(client, retry["id"])
    assert calls.count(VIDEO_A) == 1
    assert calls.count(VIDEO_B) == 2
    assert not any(track.get("error") or track["pending"] for track in result["tracks"])
    assert result["expected_duration"] == 120


@pytest.mark.parametrize("bitrate", ["500k", "0k", "320k -ar 1", 320, None])
def test_unsupported_bitrate_rejected(bitrate):
    with pytest.raises(ValidationError) as error:
        app.RenderInput(analysis_id="0" * 32, track_ids=["1" * 32], title="test", bitrate=bitrate)
    assert error.value.errors()[0]["loc"] == ("bitrate",)
