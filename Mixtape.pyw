"""Console-free shortcut target; keep import failures visible."""
import ctypes
import logging
from logging.handlers import RotatingFileHandler
import sys

try:
    import config as c

    c.DATA_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(c.DATA_DIR / "desktop.log", maxBytes=c.LOG_MAX_BYTES, backupCount=1, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(message)s")
    from launch import main

    main()
except Exception:
    logging.exception("Unable to launch Mixtape")
    ctypes.windll.user32.MessageBoxW(None, "Unable to start Mixtape. Check the desktop log in the app data folder and verify WebView2 is installed.", "Mixtape", 0x10)
    sys.exit(1)
