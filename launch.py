"""One native window owns the local server and every child process."""
import hashlib
import logging
import socket
import threading
import time
import uuid

import uvicorn
import webview
import win32api
import win32con
import win32event
import win32gui
import win32job
import winerror

import app
import config as c

logger = logging.getLogger(__name__)


def show_when_ready(window):
    try:
        if not window.events.loaded.wait(c.STARTUP_SECONDS):
            raise TimeoutError("WebView2 page loading timed out")
        if not window.evaluate_js("!!document.getElementById('links') && typeof init === 'function'"):
            raise RuntimeError("Mixtape page did not initialize")
        if not window.events.closed.is_set():
            window.show()
            logger.info("Desktop page loaded and window shown")
    except Exception:
        logger.exception("Desktop page failed to load")
        if not window.events.closed.is_set():
            try:
                win32api.MessageBox(0, "Unable to load the window. Close the app and try again.\n"
                                    "Details were written to the desktop log in the app data folder.",
                                    c.WINDOW_TITLE, win32con.MB_ICONERROR)
            finally:
                window.destroy()


def own_process_tree():
    # Windows also kills descendants if the GUI crashes or is forcibly terminated.
    job = win32job.CreateJobObject(None, f"Mixtape-{uuid.uuid4().hex}")
    limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    limits["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
    win32job.AssignProcessToJobObject(job, win32api.GetCurrentProcess())
    return job


def main():
    identity = hashlib.sha256(str(c.APP_DIR if c.FROZEN else c.ROOT).casefold().encode()).hexdigest()[:16]
    instance = win32event.CreateMutex(None, False, f"Local\\Mixtape-{identity}")
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        try:
            window_handle = win32gui.FindWindow(None, c.WINDOW_TITLE)
            if window_handle:
                if win32gui.IsIconic(window_handle):
                    win32gui.ShowWindow(window_handle, win32con.SW_RESTORE)
                try:
                    win32gui.SetForegroundWindow(window_handle)
                except win32gui.error:
                    win32gui.FlashWindow(window_handle, True)
        finally:
            instance.Close()
        return

    process_tree = own_process_tree()
    server = None
    server_thread = None
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server_socket.bind((c.HOST, c.PORT))
        url = f"http://{c.HOST}:{server_socket.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app.app, host=c.HOST, access_log=False, log_config=None,
                                              timeout_graceful_shutdown=c.SHUTDOWN_SECONDS))
        server_thread = threading.Thread(target=server.run, kwargs={"sockets": [server_socket]}, daemon=True)
        server_thread.start()
        deadline = time.monotonic() + c.STARTUP_SECONDS
        while not server.started:
            if not server_thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("Unable to start the local server.")
            time.sleep(c.POLL_SECONDS)

        def closing():
            app.cancel_jobs()
            server.should_exit = True

        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["ALLOW_FILE_URLS"] = False
        window = webview.create_window(c.WINDOW_TITLE, url, width=c.WINDOW_WIDTH, height=c.WINDOW_HEIGHT,
                                       min_size=c.WINDOW_MIN_SIZE, background_color="#f5f4ef", hidden=True)
        window.events.closing += closing
        logger.info("Waiting for desktop page; process tree is owned by this app")
        webview.start(show_when_ready, (window,), gui="edgechromium", private_mode=False,
                      storage_path=str(c.DATA_DIR / "webview"),
                      icon=str(c.ROOT / "assets" / "mixtape.ico"))
    except Exception:
        # A console-free entry point still needs a visible startup failure.
        logger.exception("Desktop startup failed")
        win32api.MessageBox(0, "Unable to start the app. Check for another running version and verify WebView2 is installed.\n"
                            "Details were written to the desktop log in the app data folder.", c.WINDOW_TITLE, win32con.MB_ICONERROR)
    finally:
        app.cancel_jobs()
        if server:
            server.should_exit = True
        if server_thread:
            server_thread.join(c.SHUTDOWN_SECONDS)
            if server_thread.is_alive():
                logger.warning("Graceful shutdown timed out; stopping the entire process tree")
        server_socket.close()
        instance.Close()
        logger.info("Window closed; terminating remaining child processes")
        logging.shutdown()
        process_tree.Close()
