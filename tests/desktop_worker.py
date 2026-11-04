"""Headless lifecycle harness: real server, worker processes and Windows Job Object."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import audio
import config as c
import launch

directory = Path(sys.argv[1])
mode = sys.argv[2]
c.ROOT = directory
c.DATA_DIR = directory / "data"
c.OUTPUT_DIR = directory / "outputs"
c.PORT = 0
close_handlers = []
state = {}


class ClosingEvent:
    def __iadd__(self, handler):
        close_handlers.append(handler)
        return self


def create_window(title, url, **kwargs):
    state["url"] = url
    return SimpleNamespace(events=SimpleNamespace(closing=ClosingEvent()))


def start(*args, **kwargs):
    with urlopen(state["url"] + "/api/config", timeout=3) as response:
        assert json.load(response)["app"] == "mixtape-studio"
    state["gui_pid"] = os.getpid()
    if mode != "idle":
        # A detached descendant exercises the case that killing only Popen misses.
        child_code = ("import subprocess,sys,os,time,json; "
                      "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'],"
                      "creationflags=subprocess.CREATE_NO_WINDOW); "
                      "print(json.dumps([os.getpid(),child.pid]),flush=True); time.sleep(120)")
        helper = subprocess.Popen([sys.executable, "-c", child_code], stdout=subprocess.PIPE,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        state["descendants"] = json.loads(helper.stdout.readline())
        original_popen = audio.subprocess.Popen

        def record_process(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            state["worker_pid"] = process.pid
            return process

        audio.subprocess.Popen = record_process
        args = ([c.FFMPEG, "-nostdin", "-re", "-f", "lavfi", "-i", "sine=frequency=440", "-f", "null", "-"]
                if mode == "convert" else [sys.executable, "-c", "import time; time.sleep(120)"])

        def worker(job_id, payload):
            cancel = app.jobs[job_id]["cancel"]
            audio.run_process(args, cancel)

        job = app.start_job("render", worker, None)
        state["job_id"] = job["id"]
        deadline = time.monotonic() + 5
        while "worker_pid" not in state:
            if time.monotonic() > deadline:
                raise TimeoutError("Worker did not start")
            time.sleep(0.02)
    (directory / "ready.json").write_text(json.dumps(state))
    deadline = time.monotonic() + 20
    while not (directory / "close").exists():
        if time.monotonic() > deadline:
            raise TimeoutError("Test never requested close")
        time.sleep(0.02)
    for handler in close_handlers:
        handler()
    if mode != "idle":
        assert app.jobs[state["job_id"]]["cancel"].is_set()


launch.webview.create_window = create_window
launch.webview.start = start
launch.win32api.MessageBox = lambda *args: (_ for _ in ()).throw(AssertionError("Unexpected desktop error"))
launch.main()
