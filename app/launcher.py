"""Start Mr. Job Consultant as a desktop app: pick the data folder, start the server, open the browser.

This is the entry point of the Windows, Linux and macOS builds (packaging/build.py), and also works
from source:  .venv\\Scripts\\python -m app.launcher

Where data lives
  from source   ./data, as before
  a build       the usual per-user folder, since a build's own files are read-only:
                  Windows  %APPDATA%\\MrJobConsultant
                  macOS    ~/Library/Application Support/MrJobConsultant
                  Linux    $XDG_DATA_HOME/MrJobConsultant (~/.local/share/MrJobConsultant)
  either way    MRJC_DATA_DIR overrides it
"""
import argparse
import json
import os
import shutil
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "MrJobConsultant"
DEFAULT_PORT = 8765
FROZEN = getattr(sys, "frozen", False)
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
SEED_FILES = ("public_job_portals.json", "ai_settings.json")


def user_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_NAME


def prepare_data() -> Path:
    """The data folder, ready to use. Sets MRJC_DATA_DIR so the server uses it too."""
    if os.environ.get("MRJC_DATA_DIR"):
        data = Path(os.environ["MRJC_DATA_DIR"]).expanduser()
    elif FROZEN:
        data = user_data_dir()
    else:
        data = BUNDLE / "data"
    data.mkdir(parents=True, exist_ok=True)
    seed = BUNDLE / "data"
    if seed.resolve() != data.resolve():
        # The job-portal directory ships with each version (a build has no crawler), so a newer one
        # replaces it. ai_settings.json is the person's own once it exists, so it's only copied once.
        portals = seed / "public_job_portals.json"
        if portals.exists():
            shutil.copy2(portals, data / portals.name)
        settings = seed / "ai_settings.json"
        if settings.exists() and not (data / settings.name).exists():
            shutil.copy2(settings, data / settings.name)
    os.environ["MRJC_DATA_DIR"] = str(data)
    return data


def _ours(port: int) -> bool:
    """Is this app already answering on that port?"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=1.5) as r:
            return json.load(r).get("app") == APP_NAME
    except Exception:
        return False


def _free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def pick_port(preferred: int) -> int:
    if _free(preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def check() -> int:
    """Smoke test for builds: every part the app needs is inside the bundle. Prints a report."""
    report: dict = {"frozen": FROZEN, "bundle": str(BUNDLE)}
    ok = True
    try:
        from app import __version__, videos
        from app.server import STATIC_DIR, app  # noqa: F401  (imports every module the app uses)
        report["version"] = __version__
        report["static"] = (STATIC_DIR / "index.html").is_file()
        node = videos._find_node()
        report["node"] = node
        report["video_worker"] = bool(node) and videos._Worker.node_or_raise() == node
        import yt_dlp  # noqa: F401
        report["yt_dlp"] = True
        for name in ("docx", "fpdf", "pypdf", "trafilatura", "gradio_client", "huggingface_hub"):
            __import__(name)
        report["libraries"] = True
        # Exercise the bundled native audio decoder, ONNX runtime and Whisper's VAD asset without
        # downloading the speech model. Import-only checks would miss a missing DLL or model file.
        import io
        import wave
        import numpy as np
        from faster_whisper import WhisperModel  # noqa: F401
        from faster_whisper.audio import decode_audio
        from faster_whisper.vad import get_speech_timestamps
        sample = io.BytesIO()
        with wave.open(sample, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\0\0" * 16000)
        sample.seek(0)
        decoded = decode_audio(sample)
        get_speech_timestamps(np.asarray(decoded, dtype=np.float32))
        report["interview_transcription"] = True
        vision = videos.WORKER.parent / "node_modules" / "@mediapipe" / "tasks-vision"
        report["interview_vision"] = all(p.is_file() for p in (
            STATIC_DIR / "models" / "pose_landmarker_lite.task", vision / "vision_bundle.mjs",
            vision / "wasm" / "vision_wasm_internal.wasm", vision / "wasm" / "vision_wasm_nosimd_internal.wasm"))
        ok &= report["interview_vision"]
        if node:  # the worker really starts and answers (a bad op fails fast, without touching YouTube)
            import subprocess
            out = subprocess.run([node, str(videos.WORKER)], input='{"id": 1, "op": "ping"}\n', text=True,
                                 capture_output=True, timeout=60, cwd=videos.WORKER.parent).stdout
            report["worker_answers"] = '"id":1' in out
            ok &= report["worker_answers"]
        ok &= report["static"] and report["video_worker"]
    except Exception as e:
        report["error"] = f"{type(e).__name__}: {e}"
        ok = False
    report["ok"] = ok
    print(json.dumps(report, indent=2))
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME, description="Mr. Job Consultant")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MRJC_PORT", DEFAULT_PORT)))
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
    parser.add_argument("--check", action="store_true", help="check the build is complete, then exit")
    parser.add_argument("--data-dir", help="keep data in this folder instead of the usual one")
    args = parser.parse_args()
    if args.data_dir:
        os.environ["MRJC_DATA_DIR"] = args.data_dir

    data = prepare_data()
    if args.check:
        return check()
    if _ours(args.port):  # started twice: just show the running one
        webbrowser.open(f"http://127.0.0.1:{args.port}")
        print(f"Mr. Job Consultant is already running at http://127.0.0.1:{args.port}")
        return 0

    import uvicorn
    from app import __version__
    from app.server import app

    port = pick_port(args.port)
    url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))

    def open_when_ready():
        while not server.started:
            time.sleep(0.2)
        print(f"Mr. Job Consultant {__version__} is running at {url}\nYour data is in {data}\n"
              "Keep this window open while you use the app; close it to stop.")
        if not args.no_browser:
            webbrowser.open(url)

    threading.Thread(target=open_when_ready, daemon=True).start()
    server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
