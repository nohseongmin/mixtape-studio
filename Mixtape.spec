# PyInstaller one-file packaging: https://pywebview.flowrl.com/guide/freezing
from pathlib import Path

root = Path(SPECPATH)
a = Analysis(
    [str(root / "Mixtape.pyw")],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / "static"), "static"), (str(root / "assets" / "mixtape.ico"), "assets"),
           (str(root / "build" / "tools"), "tools"), (str(root / "build" / "licenses"), "licenses")],
    hiddenimports=["uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
                   "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on"],
    excludes=["pytest", "tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "yt_dlp"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="Mixtape", debug=False, strip=False, upx=False, console=False,
    icon=str(root / "assets" / "mixtape.ico"),
)
