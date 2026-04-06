"""Environment setup & dependency checker for 3D Asset Agent.

Run on a new machine to verify all prerequisites are met and auto-install
what's missing::

    python setup_env.py
    python setup_env.py --auto   # auto-install missing packages + fix config
"""

from __future__ import annotations

import importlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config" / "default.yaml"

MIN_BLENDER = (4, 0)

# Common Blender install locations on Windows
_BLENDER_SEARCH_PATHS = [
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Blender Foundation",
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Blender Foundation",
    Path.home() / "AppData" / "Local" / "Blender Foundation",
]

# PyPI name → import name (only where they differ)
_IMPORT_NAME_MAP = {
    "pyyaml": "yaml",
    "python-dotenv": "dotenv",
}


def _ok(msg: str):
    print(f"  [OK] {msg}")


def _fail(msg: str):
    print(f"  [!!] {msg}")


def _info(msg: str):
    print(f"  [..] {msg}")


def _ver_str(ver: tuple[int, ...]) -> str:
    return ".".join(map(str, ver))


def check_python() -> bool:
    v = sys.version_info
    _ok(f"Python {v.major}.{v.minor}.{v.micro} ({sys.executable})")
    if v < (3, 10):
        _fail("Python >= 3.10 required.")
        return False
    return True


def check_pip_packages(auto_install: bool = False) -> bool:
    """Check that the project's Python dependencies are installed."""
    # Parse dependency names from pyproject.toml
    pyproject = PROJECT_ROOT / "pyproject.toml"
    dep_names = []
    if pyproject.exists():
        in_deps = False
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("dependencies"):
                in_deps = True
                continue
            if in_deps:
                if line.strip() == "]":
                    break
                m = re.match(r'\s*"([a-zA-Z0-9_-]+)', line)
                if m:
                    dep_names.append(m.group(1).lower())

    if not dep_names:
        dep_names = ["typer", "pyyaml", "rich", "pydantic", "streamlit", "requests", "python-dotenv"]

    missing = []
    for dep in dep_names:
        import_name = _IMPORT_NAME_MAP.get(dep, dep.replace("-", "_"))
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append(dep)

    if not missing:
        _ok("All Python packages installed.")
        return True

    _fail(f"Missing packages: {', '.join(missing)}")
    if auto_install:
        _info("Installing project dependencies (pip install -e .) ...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-e", str(PROJECT_ROOT), "-q"],
        )
        _ok("Dependencies installed.")
        return True
    else:
        _fail(f"Run: pip install -e {PROJECT_ROOT}")
        return False


def _find_blender_exe() -> Path | None:
    """Search common paths for a Blender executable."""
    for base in _BLENDER_SEARCH_PATHS:
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir(), reverse=True):
            exe = child / "blender.exe"
            if exe.is_file():
                return exe
    found = shutil.which("blender")
    return Path(found) if found else None


def _get_blender_version(exe: Path) -> tuple[int, ...] | None:
    try:
        out = subprocess.check_output(
            [str(exe), "--version"], text=True, timeout=10, stderr=subprocess.DEVNULL,
        )
        m = re.search(r"Blender\s+(\d+)\.(\d+)\.(\d+)", out)
        if m:
            return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass
    return None


def check_blender(auto_fix: bool = False) -> bool:
    """Verify Blender is installed and config/default.yaml points to it."""
    import yaml

    # Read config once
    cfg = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    configured_path = cfg.get("blender", {}).get("executable")

    # Check configured path
    if configured_path and Path(configured_path).is_file():
        exe = Path(configured_path)
        ver = _get_blender_version(exe)
        if ver:
            _ok(f"Blender {_ver_str(ver)} ({exe})")
            if ver[:2] < MIN_BLENDER:
                _fail(f"Blender >= {_ver_str(MIN_BLENDER)} required.")
                return False
            return True

    # Configured path invalid — search filesystem
    _info("Configured Blender path not found, searching...")
    exe = _find_blender_exe()

    if exe is None:
        _fail("Blender not found. Install from https://www.blender.org/download/")
        _fail("Or: winget install BlenderFoundation.Blender")
        return False

    ver = _get_blender_version(exe)
    _ok(f"Found Blender {_ver_str(ver) if ver else 'unknown'} at {exe}")

    if ver and ver[:2] < MIN_BLENDER:
        _fail(f"Blender >= {_ver_str(MIN_BLENDER)} required.")
        return False

    if not ver:
        _info("Could not determine Blender version — proceeding anyway.")

    # Auto-fix config (reuse cfg from above)
    if auto_fix and CONFIG_PATH.exists():
        cfg.setdefault("blender", {})["executable"] = str(exe)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, default_flow_style=False)
        _ok(f"Updated config/default.yaml -> {exe}")

    return True


def check_git() -> bool:
    git = shutil.which("git")
    if git:
        _ok(f"Git ({git})")
        return True
    _fail("Git not found. Install: winget install Git.Git")
    return False


def check_gh_cli() -> bool:
    gh = shutil.which("gh")
    if not gh:
        candidate = Path(r"C:\Program Files\GitHub CLI\gh.exe")
        if candidate.is_file():
            gh = str(candidate)
    if gh:
        _ok(f"GitHub CLI ({gh})")
        return True
    _info("GitHub CLI not found (optional). Install: winget install GitHub.cli")
    return True


def check_env_file() -> bool:
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        _ok(".env file exists")
        return True
    _info(".env not found (Slack notifications disabled). Create with:")
    _info("  echo SLACK_WEBHOOK_URL=https://hooks.slack.com/... > .env")
    return True


def check_environment(auto_install: bool = False) -> bool:
    """Run all checks. Returns True if environment is ready."""
    print()
    print("=" * 50)
    print("  3D Asset Agent — Environment Check")
    print("=" * 50)
    print()

    results = [
        check_python(),
        check_pip_packages(auto_install=auto_install),
        check_blender(auto_fix=auto_install),
        check_git(),
        check_gh_cli(),
        check_env_file(),
    ]

    print()
    ok = all(results)
    print("  All checks passed! Run start.bat to launch." if ok
          else "  Some checks failed. Fix the issues above and re-run.")
    print()
    return ok


if __name__ == "__main__":
    auto = "--auto" in sys.argv or "-y" in sys.argv
    ok = check_environment(auto_install=auto)
    sys.exit(0 if ok else 1)
