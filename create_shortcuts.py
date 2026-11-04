"""Install native shortcuts without a command prompt or a file-association dependency."""
import logging
from pathlib import Path
import sys

from win32com.client import Dispatch

import config as c

logging.basicConfig(level=logging.INFO, format="%(message)s")
shell = Dispatch("WScript.Shell")
for directory in (c.ROOT, Path(shell.SpecialFolders("Desktop"))):
    shortcut = shell.CreateShortcut(str(directory / "Mixtape.lnk"))
    shortcut.TargetPath = str(Path(sys.executable).with_name("pythonw.exe"))
    shortcut.Arguments = f'"{c.ROOT / "Mixtape.pyw"}"'
    shortcut.WorkingDirectory = str(c.ROOT)
    shortcut.IconLocation = str(c.ROOT / "assets" / "mixtape.ico")
    shortcut.Description = "Mixtape — closing the window stops all tasks."
    shortcut.Save()
    logging.info("Created: %s", directory / "Mixtape.lnk")
