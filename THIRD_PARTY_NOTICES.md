# Third-party software

Mixtape is distributed under GPL-3.0-or-later. Its dependencies keep their own
copyrights and licenses. The release includes a separate `Mixtape-licenses.zip`;
the same license texts are embedded in the executable under `licenses/`.
PyInstaller extracts these files to its temporary `_MEI...` directory while the app runs.

| Component | Release / source | License |
| --- | --- | --- |
| Python | [CPython](https://github.com/python/cpython) — see the release source manifest for the exact build | PSF |
| pywebview | [6.2.1](https://github.com/r0x0r/pywebview) | BSD-3-Clause |
| FastAPI / Uvicorn / Starlette | Exact versions in `constraints.txt` | MIT / BSD |
| Mutagen | [1.48.1](https://github.com/quodlibet/mutagen) | GPL-2.0-or-later |
| Pillow | [13.0.0](https://github.com/python-pillow/Pillow) | MIT-CMU |
| yt-dlp executable | [2026.10.26](https://github.com/yt-dlp/yt-dlp/releases/tag/2026.10.26) | GPL-3.0-or-later for the bundled executable; yt-dlp source is Unlicense |
| Node.js | [v26.13.0](https://nodejs.org/dist/v26.13.0/) | MIT and bundled third-party notices |
| FFmpeg / FFprobe | [BtbN n9.0.2-45-g1e65c6cd5d, win64 LGPL shared](https://github.com/BtbN/FFmpeg-Builds/releases/tag/autobuild-2026-11-03-12-50) | LGPL-3.0-or-later and included dependency licenses |
| PyInstaller bootloader | [6.22.3](https://github.com/pyinstaller/pyinstaller) | GPL with the bootloader distribution exception |

FFmpeg is an unmodified, separate command-line program; Mixtape does not link to
its libraries. Its shared DLLs are included beside the FFmpeg executables. You
may substitute compatible tools with `MIXTAPE_FFMPEG`, `MIXTAPE_FFPROBE`, and
`MIXTAPE_NODE`; modifying or debugging these components is not restricted by
Mixtape. No DRM, account cookies, or access-control bypass is included.

The exact download URLs and SHA-256 hashes are in `build-tools.json`. FFmpeg's
source revision is `1e65c6cd5d`; the corresponding BtbN build recipe is revision
`5ece62897c650b863c1ddda7b6964845344aef2d` of
[FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds). That repository includes
dependency recipes, patches, configure options, and build instructions. Runtime
Python dependency versions are pinned in `constraints.txt` and `requirements.txt`.
Release assets also include exact Python dependency source archives, CPython,
Node.js, yt-dlp, FFmpeg, and the BtbN build recipes. `docs/source-manifest.json`
records their upstream URLs and SHA-256 hashes. The separate build-input archive
preserves the checksum-pinned tool downloads even after upstream retention expires.

Microsoft WebView2 and the Windows/.NET system components are prerequisites and
are not redistributed inside Mixtape. Their respective Microsoft terms apply.
