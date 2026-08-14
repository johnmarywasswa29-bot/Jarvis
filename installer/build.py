"""Release packaging script for Jarvis."""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _ensure_empty(dir_path: Path):
    if dir_path.exists():
        shutil.rmtree(dir_path)
    dir_path.mkdir(parents=True, exist_ok=True)


def build():
    exe_name = "Jarvis"
    entry = REPO / "jarvis.py"
    dist_dir = REPO / "Release"
    work_dir = REPO / "build"
    spec_dir = REPO / "installer"

    for p in [dist_dir, work_dir, spec_dir]:
        p.mkdir(parents=True, exist_ok=True)

    # Ensure required bundled directories/files exist.
    # Only clear runtime logs; preserve source plugin files.
    for d in [REPO / "logs"]:
        _ensure_empty(d)

    add_data = [
        f"{REPO / 'config.yaml'};.",
        f"{REPO / 'requirements.txt'};.",
        f"{REPO / 'README.md'};.",
        f"{REPO / 'assets'};assets",
        f"{REPO / 'knowledge'};knowledge",
        f"{REPO / 'plugins'};plugins",
        f"{REPO / 'data'};data",
        f"{REPO / 'logs'};logs",
    ]

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", exe_name,
        "--onefile",
        "--console",
    ]
    icon_path = REPO / "assets" / "jarvis.ico"
    if icon_path.exists():
        cmd.extend(["--icon", str(icon_path)])
    cmd.extend(["--additional-hooks-dir", str(REPO / "installer" / "hooks")])
    for item in add_data:
        cmd.extend(["--add-data", item])
    cmd.extend([
        "--hidden-import", "PySide6",
        "--hidden-import", "PySide6.QtCore",
        "--hidden-import", "PySide6.QtGui",
        "--hidden-import", "PySide6.QtWidgets",
        "--hidden-import", "ddgs",
        "--hidden-import", "duckduckgo_search",
        "--exclude-module", "webrtcvad",
        "--exclude-module", "torch",
        "--exclude-module", "torchvision",
        "--exclude-module", "torchaudio",
        "--exclude-module", "tensorboard",
        str(entry),
        "--distpath", str(dist_dir),
        "--workpath", str(work_dir),
        "--specpath", str(spec_dir),
    ])

    print("[build] packaging started")
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.stdout[-1200:])
    if p.returncode != 0:
        print(p.stderr[-1200:])
        raise SystemExit(1)
    print(f"[build] packaged in {time.time()-t0:.2f}s")

    post = dist_dir / f"{exe_name}.exe"
    if post.exists():
        print(f"[build] artifact: {post}")
    else:
        raise SystemExit(f"[build] missing artifact: {post}")


def release():
    build()


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] in ("build", "release"):
        build()
    else:
        print("usage: build.py build|release")
