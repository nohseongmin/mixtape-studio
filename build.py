"""Build the Windows x64 executable with checksum-pinned media tools."""
import hashlib
from importlib.metadata import distributions
import json
import logging
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.request import urlopen
from urllib.parse import urlsplit
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
DOWNLOADS = BUILD / "downloads"
TOOLS = BUILD / "tools"
NOTICES = BUILD / "licenses"
DOWNLOAD_TIMEOUT = 120
logger = logging.getLogger(__name__)


def main():
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        raise RuntimeError("Build on 64-bit Windows with 64-bit Python.")
    for directory in (DOWNLOADS, TOOLS, NOTICES):
        directory.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / "build-tools.json").read_text(encoding="utf-8"))
    for name, tool in manifest.items():
        path = DOWNLOADS / Path(urlsplit(tool["url"]).path).name
        if not path.exists():
            logger.info("Downloading %s", name)
            with urlopen(tool["url"], timeout=DOWNLOAD_TIMEOUT) as response, path.open("wb") as output:
                shutil.copyfileobj(response, output)
        with path.open("rb") as source:
            checksum = hashlib.file_digest(source, "sha256").hexdigest()
        if checksum != tool["sha256"]:
            raise RuntimeError(f"Checksum mismatch: {path}. Remove this download and retry.")
        if path.suffix == ".zip":
            with ZipFile(path) as archive:
                for member in archive.infolist():
                    entry = Path(member.filename)
                    if entry.suffix.lower() in {".exe", ".dll"} and entry.name != "ffplay.exe":
                        with archive.open(member) as source, (TOOLS / entry.name).open("wb") as target:
                            shutil.copyfileobj(source, target)
                    elif entry.name.upper().startswith(("LICENSE", "COPYING")):
                        (NOTICES / f"{name}-{entry.name}").write_bytes(archive.read(member))
        else:
            shutil.copyfile(path, TOOLS / path.name)
    for name in ("ffmpeg.exe", "ffprobe.exe", "yt-dlp.exe", "node.exe"):
        if not (TOOLS / name).is_file():
            raise RuntimeError(f"Required bundled tool is missing: {name}")
    for package in distributions():
        name = package.metadata["Name"]
        for entry in package.files or []:
            if any(part.lower().startswith(("license", "copying", "notice")) for part in entry.parts):
                source = Path(package.locate_file(entry))
                if source.is_file():
                    target = NOTICES / name / Path(*entry.parts[1:])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copyfile(python_license, NOTICES / "Python-LICENSE.txt")
    shutil.copyfile(ROOT / "THIRD_PARTY_NOTICES.md", NOTICES / "THIRD_PARTY_NOTICES.md")
    shutil.copyfile(ROOT / "LICENSE", NOTICES / "Mixtape-LICENSE.txt")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", str(ROOT / "Mixtape.spec")],
                   cwd=ROOT, check=True)
    executable = ROOT / "dist" / "Mixtape.exe"
    with executable.open("rb") as source:
        checksum = hashlib.file_digest(source, "sha256").hexdigest()
    (ROOT / "dist" / "SHA256SUMS.txt").write_text(f"{checksum}  Mixtape.exe\n", encoding="utf-8")
    shutil.make_archive(str(ROOT / "dist" / "Mixtape-licenses"), "zip", NOTICES)
    logger.info("Built %s (%.1f MB)", executable, executable.stat().st_size / 1024**2)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
