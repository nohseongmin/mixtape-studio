import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlsplit

import pytest
import win32api
import win32con
import win32event

import app
import audio
import config as c
import launch


@pytest.mark.parametrize("mode", ["idle", "download", "convert", "forced"])
def test_window_lifetime_owns_server_and_descendants(tmp_path, mode):
    handles = []
    with subprocess.Popen([sys.executable, str(Path(__file__).with_name("desktop_worker.py")), str(tmp_path), mode],
                          cwd=c.ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          creationflags=subprocess.CREATE_NO_WINDOW) as process:
        try:
            deadline = time.monotonic() + 12
            while not (tmp_path / "ready.json").exists():
                if process.poll() is not None:
                    pytest.fail(process.communicate()[1].decode(errors="replace"))
                if time.monotonic() > deadline:
                    pytest.fail("Desktop harness did not start")
                time.sleep(0.03)
            state = json.loads((tmp_path / "ready.json").read_text())
            known = {state["gui_pid"]}
            if mode != "idle":
                known.update(state["descendants"])
                known.add(state["worker_pid"])
            for pid in known:
                handles.append(win32api.OpenProcess(win32con.SYNCHRONIZE, False, pid))
            if mode == "forced":
                process.kill()
            else:
                (tmp_path / "close").touch()
            process.wait(timeout=c.SHUTDOWN_SECONDS + 3)
            assert all(win32event.WaitForSingleObject(handle, 2000) == win32event.WAIT_OBJECT_0 for handle in handles)
            with socket.socket() as connection:
                assert connection.connect_ex((c.HOST, urlsplit(state["url"]).port)) != 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            for handle in handles:
                handle.Close()


def test_shutdown_rejects_new_work(monkeypatch):
    monkeypatch.setattr(app, "is_stopping", False)
    app.cancel_jobs()
    with pytest.raises(app.HTTPException) as error:
        app.start_job("analyze", lambda *_: None, [])
    assert error.value.status_code == 503


def test_pythonw_uses_hidden_console_child_with_pipes(monkeypatch):
    monkeypatch.setattr(sys, "executable", str(Path(sys.executable).with_name("pythonw.exe")))
    assert Path(audio.youtube_args()[0]).name == "python.exe"


def test_packaged_download_uses_bundled_tool_and_disables_disk_cache(monkeypatch, tmp_path):
    tool = str(tmp_path / "yt-dlp.exe")
    monkeypatch.setattr(c, "YTDLP", tool)
    monkeypatch.setattr(c, "FFMPEG", str(tmp_path / "ffmpeg.exe"))
    args = audio.youtube_args()
    assert args[0] == tool
    assert "-m" not in args
    assert "--no-cache-dir" in args
    assert args[args.index("--ffmpeg-location") + 1] == str(tmp_path)


@pytest.mark.parametrize("result", ["ready", "timeout", "wrong_page", "script_error", "closed"])
def test_window_waits_for_page_or_reports_failure(monkeypatch, result):
    loaded = Mock()
    loaded.wait.return_value = result != "timeout"
    closed = Mock()
    closed.is_set.return_value = result == "closed"
    window = Mock(events=SimpleNamespace(loaded=loaded, closed=closed))
    window.evaluate_js.return_value = result != "wrong_page"
    if result == "script_error":
        window.evaluate_js.side_effect = RuntimeError("Renderer unavailable")
    message = Mock()
    monkeypatch.setattr(launch.win32api, "MessageBox", message)

    launch.show_when_ready(window)

    if result == "ready":
        window.show.assert_called_once()
        message.assert_not_called()
        window.destroy.assert_not_called()
    elif result == "closed":
        window.show.assert_not_called()
        message.assert_not_called()
    else:
        window.show.assert_not_called()
        message.assert_called_once()
        window.destroy.assert_called_once()
