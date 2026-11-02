# PyInstaller recipe for the desktop builds. Run it through packaging/build.py, which first stages
# the YouTube worker (with its npm packages for this platform only) and the Node.js binary in
# MRJC_STAGE, then checks and zips the result.
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
APP = ROOT / "app"
STAGE = Path(os.environ["MRJC_STAGE"])
NODE = STAGE / "node" / ("node.exe" if os.name == "nt" else "node")

datas = [
    (str(APP / "static"), "app/static"),
    (str(APP / "title_families.json"), "app"),
    (str(APP / "hf_space"), "app/hf_space"),              # uploaded by "Set up my ZeroGPU Space"
    (str(APP / "hf_space_writer"), "app/hf_space_writer"),
    (str(STAGE / "youtube"), "app/youtube"),              # worker.mjs + node_modules
    (str(ROOT / "data" / "public_job_portals.json"), "data"),  # seeds the per-user data folder
    (str(ROOT / "data" / "ai_settings.json"), "data"),
]
# Packages that read their own data files at run time.
for package in ("docx", "trafilatura", "justext", "courlan", "htmldate", "tldextract", "gradio_client",
                "fpdf", "certifi", "yt_dlp"):
    try:
        datas += collect_data_files(package)
    except Exception:
        pass

hiddenimports = (collect_submodules("uvicorn")        # picks its loop and protocol modules by name
                 + collect_submodules("python_multipart")
                 + collect_submodules("yt_dlp.extractor"))

a = Analysis(
    [str(APP / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[(str(NODE), "node")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["playwright", "ddgs", "tkinter", "matplotlib", "torch", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MrJobConsultant",
    console=True,  # the window shows the address and stays open while the app runs
)
coll = COLLECT(exe, a.binaries, a.datas, name="MrJobConsultant")
