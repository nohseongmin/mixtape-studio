import json
from pathlib import Path
import subprocess
import sys

import config as c


def test_automatic_lookup_rows_retry_and_blank_start_without_saved_cover(tmp_path):
    with subprocess.Popen([sys.executable, str(Path(__file__).with_name("frontend_worker.py")), str(tmp_path)],
                          cwd=c.ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          creationflags=subprocess.CREATE_NO_WINDOW) as process:
        try:
            _, stderr = process.communicate(timeout=60)
            result_file = tmp_path / "result.json"
            assert result_file.exists(), stderr.decode(errors="replace")
            result = json.loads(result_file.read_text())
            assert result["ok"], result.get("error")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=c.SHUTDOWN_SECONDS)
