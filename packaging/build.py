"""Build the desktop app for the system this runs on (Windows, macOS or Linux).

    python packaging/build.py

Needs the app's requirements plus PyInstaller, and Node.js 20+ with npm on PATH. Output:
    dist/MrJobConsultant/                                   the app, ready to run
    dist/MrJobConsultant-<version>-<os>-<arch>.zip|.tar.gz  the same, packed for a release

PyInstaller can only build for the system it runs on, so .github/workflows/build.yml runs this on
each of them.
"""
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGE = ROOT / "build" / "stage"
DIST = ROOT / "dist"
NAME = "MrJobConsultant"


def version() -> str:
    return re.search(r'__version__ = "([^"]+)"', (ROOT / "app" / "__init__.py").read_text()).group(1)


def target() -> tuple[str, str]:
    """(os, arch) as used in file names and by onnxruntime-node: ("windows", "x64"), ("macos", "arm64")…"""
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    return system, arch


def run(cmd: list, **kw) -> None:
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.run(cmd, check=True, **kw)


def stage_worker(system: str, arch: str) -> None:
    """The YouTube worker with a clean, production-only npm install, trimmed to this platform."""
    dest = STAGE / "youtube"
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    source = ROOT / "app" / "youtube"
    scripts = sorted(source.glob("*.mjs"))  # every worker script (worker.mjs, media.mjs…), so none is left out
    for path in scripts + [source / "package.json", source / "package-lock.json"]:
        shutil.copy2(path, dest / path.name)
    npm = shutil.which("npm") or sys.exit("npm not found: install Node.js 20+")
    run([npm, "ci", "--omit=dev", "--no-audit", "--no-fund"], cwd=dest)

    modules = dest / "node_modules"
    # transformers.js carries its own copy of onnxruntime-web; the package itself is only for browsers.
    shutil.rmtree(modules / "onnxruntime-web", ignore_errors=True)
    ort_os = {"windows": "win32", "macos": "darwin", "linux": "linux"}[system]
    for napi in (modules / "onnxruntime-node" / "bin").glob("napi-v*"):
        for os_dir in napi.iterdir():
            if os_dir.name != ort_os:
                shutil.rmtree(os_dir)
                continue
            for arch_dir in os_dir.iterdir():
                if arch_dir.name != arch:
                    shutil.rmtree(arch_dir)


def stage_node() -> None:
    node = shutil.which("node") or sys.exit("node not found: install Node.js 20+")
    out = subprocess.run([node, "--version"], capture_output=True, text=True, check=True).stdout.strip()
    if int(out.lstrip("v").split(".")[0]) < 20:
        sys.exit(f"Node.js 20+ is needed, found {out}")
    dest = STAGE / "node"
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    target_file = dest / ("node.exe" if os.name == "nt" else "node")
    shutil.copy2(Path(node).resolve(), target_file)
    target_file.chmod(0o755)
    print(f"bundling Node.js {out}")


def pyinstaller() -> Path:
    run([sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / "mrjc.spec"), "--noconfirm", "--clean",
         "--distpath", str(DIST), "--workpath", str(ROOT / "build" / "pyinstaller")],
        env={**os.environ, "MRJC_STAGE": str(STAGE)})
    return DIST / NAME


def self_check(app_dir: Path) -> None:
    exe = app_dir / (f"{NAME}.exe" if os.name == "nt" else NAME)
    with tempfile.TemporaryDirectory() as data:
        run([str(exe), "--check"], env={**os.environ, "MRJC_DATA_DIR": data}, timeout=180)


def pack(app_dir: Path, system: str, arch: str) -> Path:
    base = DIST / f"{NAME}-{version()}-{system}-{arch}"
    if system == "linux":
        return Path(shutil.make_archive(str(base), "gztar", DIST, NAME))
    return Path(shutil.make_archive(str(base), "zip", DIST, NAME))


def main() -> None:
    system, arch = target()
    print(f"building {NAME} {version()} for {system}-{arch}")
    stage_worker(system, arch)
    stage_node()
    app_dir = pyinstaller()
    self_check(app_dir)
    archive = pack(app_dir, system, arch)
    size = sum(f.stat().st_size for f in app_dir.rglob("*") if f.is_file())
    print(f"built {app_dir} ({size / 1e6:.0f} MB) and {archive.name} ({archive.stat().st_size / 1e6:.0f} MB)")
    if gh_out := os.environ.get("GITHUB_OUTPUT"):
        with open(gh_out, "a") as f:
            f.write(f"archive={archive}\n")


if __name__ == "__main__":
    main()
