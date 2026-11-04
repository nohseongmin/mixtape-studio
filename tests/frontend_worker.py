"""Exercise the actual WebView/DOM with deterministic video metadata."""
import json
from pathlib import Path
import shutil
import sys
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audio
import config as c
import launch

directory = Path(sys.argv[1])
(directory / "assets").mkdir()
shutil.copyfile(c.ROOT / "assets" / "mixtape.ico", directory / "assets" / "mixtape.ico")
c.ROOT = directory
c.DATA_DIR = directory / "data"
c.OUTPUT_DIR = directory / "outputs"
c.PORT = 0
calls = []
videos = [f"https://www.youtube.com/watch?v={letter * 11}" for letter in "abcd"]


def inspect(url, cancel):
    calls.append(url)
    if url == videos[3] and calls.count(url) == 1:
        raise audio.TaskError("Please try again")
    return {"url": url, "title": f"Test track {videos.index(url) + 1}", "duration": 60 * (videos.index(url) + 1)}


def exercise(window):
    def until(script):
        deadline = time.monotonic() + c.STARTUP_SECONDS
        while not window.evaluate_js(script):
            if time.monotonic() > deadline:
                raise AssertionError(f"Timed out: {script}")
            time.sleep(0.05)

    def enter(index, url):
        window.evaluate_js(f"""(() => {{
            const input = document.querySelectorAll('.track-link')[{index}];
            input.value = {json.dumps(url)};
            input.dispatchEvent(new Event('input', {{bubbles: true}}));
        }})()""")

    def analyzed():
        until("!state.analysisTimer && !state.busy && !!state.analysisId && state.tracks.filter(t => t.input.trim()).every(t => t.id && !t.pending)")

    try:
        assert window.events.loaded.wait(c.STARTUP_SECONDS)
        until("!!state.config && state.tracks.length === 1")
        enter(0, "https://www.youtube.com/watch?v=a")
        window.evaluate_js(f"""(() => {{
            const data = new DataTransfer();
            data.setData('text/plain', {json.dumps(chr(10).join(videos[:2]))});
            document.querySelector('.track-link').dispatchEvent(new ClipboardEvent('paste',
                {{clipboardData: data, bubbles: true, cancelable: true}}));
            document.getElementById('title').value = 'Test mix';
            document.getElementById('title').dispatchEvent(new Event('input'));
        }})()""")
        assert window.evaluate_js("state.tracks.length === 2")
        analyzed()
        assert window.evaluate_js("!document.getElementById('analyze') && !document.body.textContent.includes('will not check again')")
        assert window.evaluate_js("document.getElementById('expected').textContent === '00:03:00'")
        window.evaluate_js("document.getElementById('add-link').click()")
        enter(2, videos[2])
        assert window.evaluate_js("document.getElementById('expected').textContent === '00:03:00'")
        assert window.evaluate_js("document.getElementById('render').disabled")
        analyzed()
        assert calls.count(videos[0]) == calls.count(videos[1]) == calls.count(videos[2]) == 1
        enter(1, videos[0])
        analyzed()
        assert window.evaluate_js("document.getElementById('expected').textContent === '00:05:00'")
        assert calls.count(videos[0]) == 1
        window.evaluate_js("document.querySelectorAll('.track-actions')[2].querySelector('button').click()")
        assert window.evaluate_js("state.tracks[1].title === 'Test track 3'")
        window.evaluate_js("document.querySelectorAll('.track-actions')[1].querySelectorAll('button')[2].click()")
        assert window.evaluate_js("document.getElementById('expected').textContent === '00:02:00'")
        window.evaluate_js("document.getElementById('add-link').click()")
        enter(2, videos[3])
        analyzed()
        assert window.evaluate_js("!!state.tracks[2].error")
        window.evaluate_js("document.querySelector('[aria-label=\"Retry track 3\"]').click()")
        analyzed()
        assert window.evaluate_js("!state.tracks[2].error && !document.getElementById('render').disabled")
        assert calls.count(videos[3]) == 2
        assert window.evaluate_js("document.getElementById('bitrate').value === '192k' && document.getElementById('bitrate').options.length === 4")
        window.evaluate_js("""(() => {
            window.originalFetch = window.fetch;
            window.fetch = (url, options) => {
                if (url !== '/api/render') return window.originalFetch(url, options);
                window.renderRequest = JSON.parse(options.body);
                return Promise.resolve(new Response(JSON.stringify({detail: 'test intercepted'}), {status: 422}));
            };
            document.getElementById('bitrate').value = '320k';
            document.getElementById('render').click();
        })()""")
        until("!!window.renderRequest && !state.busy")
        assert window.evaluate_js("window.renderRequest.bitrate === '320k'")
        window.evaluate_js("window.fetch = window.originalFetch")
        window.evaluate_js("""(() => {
            const canvas = document.createElement('canvas');
            canvas.width = canvas.height = 16;
            const image = new Image();
            image.onload = () => { openCrop(image); document.getElementById('apply-crop').click(); };
            image.src = canvas.toDataURL('image/png');
        })()""")
        until("!!state.cover && !document.getElementById('cover-preview').hidden")
        assert window.evaluate_js("localStorage.length === 0 && sessionStorage.length === 0")
        # Existing versions left links, tags and a cover in this key. Upgrade must discard all of them.
        window.evaluate_js("localStorage.setItem('mixtape-draft-v1', JSON.stringify({tracks: state.tracks, title: 'old title', artist: 'old artist', album: 'old album', cover: state.cover}))")
        before = list(calls)
        launch.app.jobs.clear()
        window.events.loaded.clear()
        window.run_js("location.reload()")
        assert window.events.loaded.wait(c.STARTUP_SECONDS)
        until("!!state.config && state.tracks.length === 1")
        assert window.evaluate_js("!state.analysisId && !state.busy && document.getElementById('expected').textContent === '00:00:00'")
        assert window.evaluate_js("!state.tracks[0].input && !state.cover && document.getElementById('cover-preview').hidden && ['title','artist','album'].every(id => !document.getElementById(id).value)")
        assert window.evaluate_js("localStorage.length === 0 && sessionStorage.length === 0")
        assert calls == before
        assert window.evaluate_js("document.getElementById('bitrate').value === '192k'")
        enter(0, videos[0] + " " + videos[1])
        until("!state.analysisTimer && !document.getElementById('notice').hidden")
        assert window.evaluate_js("!state.busy && !document.getElementById('notice').hidden")
        assert calls == before
        (directory / "result.json").write_text(json.dumps({"ok": True, "calls": calls}))
    except Exception:
        (directory / "result.json").write_text(json.dumps({"ok": False, "error": traceback.format_exc()}))
    finally:
        window.destroy()


audio.inspect_video = inspect
launch.show_when_ready = exercise
launch.main()
