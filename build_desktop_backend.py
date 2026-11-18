"""Build the platform-specific Python sidecar used by the Rust desktop shell."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
DEST = ROOT / "apps" / "desktop" / "src-tauri" / "resources" / "backend"
BUILD = ROOT / "build" / "pyinstaller"
HOOKS = ROOT / "packaging" / "pyinstaller-hooks"
MAX_SIDECAR_BYTES = 250 * 1024 * 1024
LAZY_RUNTIME_MODULES = (
    "sympy",
    "requests",
    "ddgs",
    "bs4",
    "pypdf",
    "docx",
    "openpyxl",
    "pptx",
    "bleach",
    "markdown",
    "reportlab",
    "matplotlib.mathtext",
    "PIL.Image",
)


def main() -> int:
    if not (ROOT / "static" / "index.html").is_file():
        raise SystemExit("ClaudeX static frontend was not found.")
    DEST.mkdir(parents=True, exist_ok=True)
    for old in DEST.glob("claudex-backend*"):
        if old.is_file():
            old.unlink()

    separator = os.pathsep
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile",
        "--name", "claudex-backend", "--distpath", str(DEST), "--workpath", str(BUILD),
        "--specpath", str(BUILD), "--add-data", f"{ROOT / 'static'}{separator}static",
        "--additional-hooks-dir", str(HOOKS), "--exclude-module", "pytest",
        "--exclude-module", "sympy.testing", "--exclude-module", "matplotlib.tests",
        *[argument for module in LAZY_RUNTIME_MODULES for argument in ("--hidden-import", module)],
        str(ROOT / "app.py"),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    executable = DEST / ("claudex-backend.exe" if os.name == "nt" else "claudex-backend")
    size = executable.stat().st_size
    print(f"Python backend sidecar: {size / (1024 * 1024):.1f} MiB")
    if size > MAX_SIDECAR_BYTES:
        executable.unlink(missing_ok=True)
        raise SystemExit("Sidecar exceeds 250 MiB; this build does not meet the requested package-size ceiling.")
    print("This checks the Python sidecar only. Measure the final installer separately before claiming the total app is under 250 MiB.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
