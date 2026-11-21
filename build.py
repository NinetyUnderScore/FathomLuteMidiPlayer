#!/usr/bin/env python3
"""Build the standalone Windows executable, dist/Lute.exe.

    pip install pyinstaller
    python build.py

The exe is a single file. Copy it anywhere and put a songs/ folder next to
it: the app scans that folder (and creates it on first run if missing).
Nothing in songs/ is bundled into the exe.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit(
            "PyInstaller is not installed.\n\n    pip install pyinstaller")

    cmd = [sys.executable, "-m", "PyInstaller",
           os.path.join(HERE, "lute.spec"),
           "--noconfirm", "--clean"]
    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=HERE)
    if result.returncode:
        raise SystemExit(result.returncode)

    exe = os.path.join(HERE, "dist", "Lute.exe")
    if not os.path.exists(exe):
        raise SystemExit("Build finished but dist/Lute.exe is missing.")

    # Make the songs folder next to the exe easy to find. It stays empty; add
    # your own .mid/.midi files there.
    songs = os.path.join(HERE, "dist", "songs")
    os.makedirs(songs, exist_ok=True)

    print()
    print("Built:", exe)
    print("Songs:", songs)
    print("Put .mid / .midi files in that folder, then run Lute.exe.")


if __name__ == "__main__":
    main()
