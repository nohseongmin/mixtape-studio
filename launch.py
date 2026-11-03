"""Bind an available loopback port and open the app once it is ready."""
import logging
import json
import socket
import threading
import time
import urllib.error
import urllib.request
import webbrowser

import uvicorn

import config as c

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def open_when_ready(url):
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"{url}/api/config", timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.25)
    logger.error("브라우저를 자동으로 열지 못했습니다. 표시된 로컬 주소를 직접 열어주세요.")


if __name__ == "__main__":
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        try:
            server_socket.bind((c.HOST, c.PORT))
        except OSError:
            url = f"http://{c.HOST}:{c.PORT}"
            try:
                with urllib.request.urlopen(f"{url}/api/config", timeout=2) as response:
                    if json.load(response).get("app") == "mixtape-studio":
                        webbrowser.open(url)
                        raise SystemExit(0)
            except (urllib.error.URLError, TimeoutError, ValueError):
                logger.info("지정한 포트에서 실행 중인 Mixtape를 찾지 못했습니다.")
            logger.error("로컬 포트를 열 수 없습니다. 이미 실행 중인 Mixtape 창을 확인하거나 MIXTAPE_PORT를 다른 번호로 지정해주세요.")
            raise SystemExit(1)
        url = f"http://{c.HOST}:{server_socket.getsockname()[1]}"
        logger.info("Mixtape: %s — 이 창을 닫으면 앱이 종료됩니다.", url)
        threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()
        uvicorn.Server(uvicorn.Config("app:app", host=c.HOST, access_log=False)).run(sockets=[server_socket])
