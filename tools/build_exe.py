"""Build SpikeSight.exe, so nobody has to install Python to use it.

    python tools/build_exe.py

Produces ``dist/SpikeSight/SpikeSight.exe`` and zips the folder up as
``dist/SpikeSight-<version>-windows.zip``, ready to hand to someone.

Notes on the choices here:

* **onedir, not onefile.** A one-file build unpacks itself to a temp directory
  on every launch, which is slow and is the single biggest trigger for
  antivirus false positives. A folder starts instantly and looks like what it
  is.
* **windowed, not console.** A black console window sitting behind the app is
  confusing for the people this build is aimed at. Startup failures are shown
  in a dialog box instead - see ``_fatal`` in ``spikesight/__main__.py``.
* **explicit hidden imports.** uvicorn loads its protocol implementations by
  string name, so PyInstaller's static analysis cannot see them.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spikesight import __version__  # noqa: E402

DIST = ROOT / "dist"
BUILD = ROOT / "build"
APP_NAME = "SpikeSight"
ICON = ROOT / "packaging" / "spikesight.ico"

# uvicorn resolves these at runtime through strings, so they have to be named.
HIDDEN_IMPORTS = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
]

# Everything the app reads at runtime that is not a .py file.
DATA = [
    (ROOT / "web", "web"),
    (ROOT / "spikesight" / "data", "spikesight/data"),
    # The tray icon loads this from disk at runtime, so it has to be unpacked
    # rather than only embedded in the exe's resources.
    (ROOT / "packaging" / "spikesight.ico", "packaging"),
    # Rendered inside the app's Settings panel, so it has to be unpacked.
    (ROOT / "CHANGELOG.md", "."),
]


def check_inputs() -> None:
    missing = [str(src) for src, _ in DATA if not src.exists()]
    if not ICON.exists():
        missing.append(f"{ICON}  (run: python tools/make_icon.py)")
    if missing:
        raise SystemExit("Missing build inputs:\n  " + "\n  ".join(missing))


def build() -> Path:
    for stale in (DIST / APP_NAME, BUILD):
        if stale.exists():
            shutil.rmtree(stale, ignore_errors=True)

    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", APP_NAME,
        "--windowed",
        "--icon", str(ICON),
        "--distpath", str(DIST),
        "--workpath", str(BUILD),
        "--specpath", str(BUILD),
    ]
    for module in HIDDEN_IMPORTS:
        command += ["--hidden-import", module]
    for source, target in DATA:
        command += ["--add-data", f"{source}{__import__('os').pathsep}{target}"]
    command.append(str(ROOT / "packaging" / "launch.py"))

    print("Running PyInstaller...\n  " + " ".join(command[:8]) + " ...\n")
    subprocess.run(command, check=True, cwd=ROOT)
    return DIST / APP_NAME


def add_readme(folder: Path) -> None:
    lines = [
        "SpikeSight",
        "========",
        "",
        "GETTING STARTED",
        "",
        "  1. Start VALORANT, or at least the Riot Client.",
        "  2. Double-click SpikeSight.exe in this folder.",
        "  3. A SpikeSight window opens. Queue up - the scoreboard fills itself in.",
        "",
        "  Close the SpikeSight window to quit. Running it again while it is",
        "  already open just brings the window back.",
        "",
        "  Want an icon on your desktop? Open the gear menu inside SpikeSight",
        "  and click 'Create desktop shortcut'.",
        "",
        "  Want the pre-match overlay? Turn it on in the gear menu, and set",
        "  VALORANT's display mode to Windowed Fullscreen so it can show.",
        "",
        "",
        "THE FIRST LAUNCH MAY WARN YOU",
        "",
        "  Windows may show a blue 'Windows protected your PC' box. That",
        "  appears for any program without a paid code-signing certificate.",
        "  Click 'More info', then 'Run anyway'.",
        "",
        "",
        "IF SOMETHING GOES WRONG",
        "",
        "  SpikeSight writes a log of its last run to:",
        "",
        "    %LOCALAPPDATA%\\SpikeSight\\spikesight.log",
        "",
        "  Paste that path into the File Explorer address bar to find it, and",
        "  send the file to whoever gave you SpikeSight. It says what failed.",
        "",
        "  'Port 8787 is already in use' - another program has that port.",
        "  Open %LOCALAPPDATA%\\SpikeSight\\config.toml in Notepad and change",
        "  port = 8787 to something else, such as 8790.",
        "",
        "",
        "IF YOUR FRAME RATE DROPS WHILE PLAYING",
        "",
        "  Any window left open over a game costs frames - Windows has to",
        "  compose the desktop instead of letting the game draw straight to",
        "  the screen. Minimize SpikeSight while you play, or switch on",
        "  'Minimize after agent select' in the gear menu to have it done for",
        "  you at the start of every match.",
        "",
        "  If it stays slow after the window is gone, alt-tab out of VALORANT",
        "  and back in - that usually restores it. Open the gear menu inside",
        "  SpikeSight and read the Performance section for the rest.",
        "",
        "",
        "WHAT IT DOES AND DOES NOT DO",
        "",
        "  SpikeSight only reads. It cannot queue, dodge, lock an agent, or",
        "  change anything in your account. Nothing is uploaded anywhere.",
        "  Your notes stay on this PC, in %LOCALAPPDATA%\\SpikeSight.",
        "",
        "  Delete this folder to uninstall. Nothing is added to Program Files",
        "  or to the registry.",
        "",
        "  Not affiliated with Riot Games.",
        "",
    ]
    # CRLF, because this is read in Notepad.
    (folder / "READ ME FIRST.txt").write_text(
        "\r\n".join(lines), encoding="utf-8"
    )


def make_zip(folder: Path) -> Path:
    archive = DIST / f"{APP_NAME}-{__version__}-windows.zip"
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                zf.write(path, Path(APP_NAME) / path.relative_to(folder))
    return archive


def write_checksums(archive: Path) -> Path:
    """Publish a hash beside the zip, so a download can be checked.

    This says the file is the one that was built, nothing more - a hash
    anybody can recompute proves the bytes were not tampered with in transit,
    but says nothing about what is inside them. That is what the source and
    the build workflow are for.
    """
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    listing = DIST / "SHA256SUMS.txt"
    listing.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return listing


def main() -> int:
    check_inputs()
    folder = build()
    add_readme(folder)
    archive = make_zip(folder)

    checksums = write_checksums(archive)

    exe = folder / f"{APP_NAME}.exe"
    folder_size = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
    print("\nBuild complete.")
    print(f"  App    : {exe}")
    print(f"  Folder : {folder_size / 1024 / 1024:.1f} MB")
    print(f"  Zip    : {archive}  ({archive.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"  SHA256 : {checksums.read_text(encoding='utf-8').split()[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
