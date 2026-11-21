# Verification

## Automated coverage

Use 64-bit Python 3.12 on Windows with FFmpeg, FFprobe, Node.js, and WebView2
available. From the repository root, create the verification environment and
install the pinned development requirements before running the full suite:

```powershell
python -m venv --without-pip .venv
python -m pip --python .venv\Scripts\python.exe install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
```

`requirements-dev.txt` includes `requirements.txt`, which already declares
`pywin32`. A `ModuleNotFoundError: No module named 'win32api'` during desktop test
collection means the test interpreter is missing that dependency. Install the
requirements into the same environment used to run pytest; keep the desktop tests
enabled. Tests do not need a live YouTube connection.

`tests/test_pipeline.py::test_short_non_silent_audio_is_preserved` covers a
0.1-second tone both alone and surrounded by silence. Before the fix, both cases
raise `TaskError` because the remaining duration is below `SILENCE_SECONDS`
(0.12 seconds). After the fix, both must retain the tone; only boundary silence
is removed. The existing all-silent-track test must continue to raise `TaskError`.

- Canonical links, rejected invalid hosts/credentials/ports/IDs, and 301 videos.
- Concurrent metadata lookup, session-only reuse, and retries of failed entries.
- Actual WebView2: paste, auto lookup, edit, reorder, remove, retry, duration,
  bitrate submission, crop editor, and blank state after reload.
- Legacy saved links, tags, and covers are discarded; quality resets to 192 kbps.
- Generated audio: edge trimming, preserved internal pauses, 0.1-second tones
  with and without boundary silence, track order, Unicode ID3v2.3 tags, and
  800 × 800 embedded covers.
- Real encoding at 128, 192, 256, and 320 kbps, with duration validation.
- Bad bitrates, malformed covers, restricted/oversized requests, unavailable videos,
  all-silent audio, cancellation, and truncated output.
- Desktop lifetime: idle close, active download, active conversion, forced exit,
  closed server sockets, and terminated descendant processes.
- Startup readiness and visible loading errors; separate bundled download worker.

## Release checks

Built with 64-bit Python 3.12 and PyInstaller 6.22.3. Tool downloads are checked
against `build-tools.json`. Releases include a checksum and license notices.

- 2026-11-04: **43 tests passed**, using the exact FFmpeg/FFprobe/Node tools bundled
  in the release. JavaScript syntax and Git whitespace checks passed.
- The one-file EXE started with `PATH` reduced to Windows System32 and no Python
  environment variables or external media-tool overrides. Its native English
  window loaded successfully and reported no missing tools.
- Closing the real packaged window terminated both Mixtape processes and all six
  WebView2 descendants. Its server port was no longer listening.
- A live lookup of the Blender Foundation's Big Buck Bunny reported 635 seconds.
  The packaged app downloaded its audio, trimmed the edges, and produced a
  634.044-second, 320,000-bit/s MP3 with Unicode tags and an 800 × 800 JPEG cover.
  The generated file was about 25.4 MB. This sample is test data and is not shipped.
- Test media: [Big Buck Bunny](https://peach.blender.org/about/), Blender Foundation,
  CC BY 3.0; audio extracted and edge silence trimmed for verification.

## Limitations

- Mocked metadata tests cannot predict YouTube availability, rate limits, or
  future extractor changes.
- Higher bitrate cannot improve the original source audio.
- Very quiet boundary audio may meet the silence threshold.
- Windows x64 is supported. Other systems, Windows ARM, and a clean PC without
  WebView2 are not covered.
- Starlette currently emits a TestClient/httpx deprecation warning; checks pass.
- The executable is unsigned; no signing certificate is included.
