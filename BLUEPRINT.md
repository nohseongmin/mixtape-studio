# Architecture

Mixtape is a Windows desktop app with a local HTML/CSS/JavaScript interface.
It combines individual YouTube video audio tracks into one tagged MP3.

| File | Responsibility |
| --- | --- |
| `Mixtape.pyw` | Console-free entry point, rotating log, visible startup errors |
| `launch.py` | Native WebView2 window, loopback server, instance mutex, process lifetime |
| `app.py` | Validated API, in-memory jobs, concurrent lookups, sequential rendering |
| `audio.py` | Link validation, child processes, extraction, trimming, encoding, ID3 |
| `config.py` | Defaults, limits, bundle paths, environment overrides |
| `static/` | English UI, link rows, duration estimate, ordering, crop editor |
| `build.py`, `Mixtape.spec`, `build-tools.json` | Checksum-pinned tools and one-file packaging |

## Lifetime

The window owns the local server and child processes through a Windows Job Object
with `KILL_ON_JOB_CLOSE`. Normal close requests cancellation and server shutdown.
Closing the job handle also terminates remaining descendants. The window stays
hidden until its document is loaded and the app script is present, avoiding an
empty white window. Duplicate launches focus the existing instance.

Frozen resources live in PyInstaller's extraction directory. Working files and
MP3s live beside the executable, or in explicitly configured directories. The
download worker is a separate bundled yt-dlp executable: a frozen application
must not invoke its own executable as though it were a Python interpreter.

## Data flow

1. Typing pauses for 700 ms before lookup. Multi-link pastes create separate rows.
2. Supported links are canonicalized. Successful results from earlier jobs in the
   same process are reused. Up to four distinct new videos are checked at once.
3. Selected audio is downloaded and decoded to trimmed 44.1 kHz stereo PCM. Only
   silence touching the start or end is removed. Internal rests remain intact.
4. PCM tracks are concatenated and encoded once at the chosen bitrate. The output
   duration is checked against the trimmed track total.
5. ID3v2.3 tags and optional square JPEG cover art are written. The MP3 moves to
   `outputs`, and temporary job files are cleaned up.

## Boundaries

- No persistent playlist, cover, or video-metadata database.
- No fixed duration or 300-track ceiling; request and disk limits still apply.
- One active analysis or render job per process.
- No playlist expansion, account cookies, DRM handling, crossfade, or normalization.
- Localhost only, with host/origin checks, custom request header, CSP, request
  limits, strict schemas, and validated subprocess arguments.
- No shell commands for media tools. Streams are redirected; consoles are hidden.

Defaults live in `config.py`. Dependencies and external build tools are pinned.
Tests use generated audio and deterministic metadata, with actual WebView2 for UI
behavior and actual FFmpeg for the audio pipeline.
