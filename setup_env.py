"""Environment setup & dependency checker for 3D Asset Agent.

Run on a new machine to verify all prerequisites are met and auto-install
what's missing::

    python setup_env.py

Can also be imported and called programmatically::

    from setup_env import check_environment
    ok = check_environment(auto_install=True)
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config" / "default.yaml"

# Blender version range we support
MIN_BLENDER = (4, 0)
MAX_BLENDER = (5, 99)  # future-proof

# Common Blender install locations on Windows
_BLENDER_SEARCH_PATHS = [
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Blender Foundation",
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Blender Foundation",
    Path.home() / "AppData" / "Local" / "Blender Foundation",
]


def _print(icon: str, msg: str):
    print(f"  {icon} {msg}")


def _ok(msg: str):
    _print("[OK]", msg)


def _fail(msg: str):
    _print("[!!]", msg)


def _info(msg: str):
    _print("[..]", msg)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_python() -> bool:
    v = sys.version_info
    _ok(f"Python {v.major}.{v.minor}.{v.micro} ({sys.executable})")
    if v < (3, 10):
        _fail("Python >= 3.10 required.")
        return False
    return True


def check_pip_packages(auto_install: bool = False) -> bool:
    """Check that the project's Python dependencies are installed."""
    try:
        import typer, yaml, rich, pydantic, streamlit, requests, dotenv  # noqa: F401
        _ok("All Python packages installed.")
        return True
    except ImportError as exc:
        missing = exc.name
        _fail(f"Missing Python package: {missing}")
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
    # Also check PATH
    found = shutil.which("blender")
    return Path(found) if found else None


def _get_blender_version(exe: Path) -> tuple[int, ...] | None:
    try:
        out = subprocess.check_output([str(exe), "--version"], text=True, timeout=10)
        m = re.search(r"Blender\s+(\d+)\.(\d+)\.(\d+)", out)
        if m:
            return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except Exception:
        pass
    return None


def check_blender(auto_fix: bool = False) -> bool:
    """Verify Blender is installed and config/default.yaml points to it."""
    # 1. Read configured path
    configured_path = None
    if CONFIG_PATH.exists():
        import yaml
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        configured_path = cfg.get("blender", {}).get("executable")

    # 2. Check configured path first
    if configured_path and Path(configured_path).is_file():
        exe = Path(configured_path)
        ver = _get_blender_version(exe)
        if ver:
            _ok(f"Blender {ver[0]}.{ver[1]}.{ver[2]} ({exe})")
            if ver[:2] < MIN_BLENDER:
                _fail(f"Blender >= {MIN_BLENDER[0]}.{MIN_BLENDER[1]} required.")
                return False
            return True

    # 3. Configured path invalid — try to find Blender
    _info("Configured Blender path not found, searching...")
    exe = _find_blender_exe()

    if exe is None:
        _fail("Blender not found. Install from https://www.blender.org/download/")
        _fail("Or: winget install BlenderFoundation.Blender")
        return False

    ver = _get_blender_version(exe)
    ver_str = f"{ver[0]}.{ver[1]}.{ver[2]}" if ver else "unknown"
    _ok(f"Found Blender {ver_str} at {exe}")

    if ver and ver[:2] < MIN_BLENDER:
        _fail(f"Blender >= {MIN_BLENDER[0]}.{MIN_BLENDER[1]} required.")
        return False

    # 4. Auto-fix config
    if auto_fix and CONFIG_PATH.exists():
        import yaml
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        cfg.setdefault("blender", {})["executable"] = str(exe)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, default_flow_style=False)
        _ok(f"Updated config/default.yaml → {exe}")

    return True


def check_git() -> bool:
    git = shutil.which("git")
    if git:
        _ok(f"Git ({git})")
        return True
    _fail("Git not found. Install: winget install Git.Git")
    return False


def check_gh_cli() -> bool:
    """GitHub CLI is optional but recommended."""
    gh = shutil.which("gh")
    if not gh:
        # Check common Windows install path
        candidate = Path(r"C:\Program Files\GitHub CLI\gh.exe")
        if candidate.is_file():
            gh = str(candidate)
    if gh:
        _ok(f"GitHub CLI ({gh})")
        return True
    _info("GitHub CLI not found (optional). Install: winget install GitHub.cli")
    return True  # Not required


def check_env_file() -> bool:
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        _ok(".env file exists")
        return True
    _info(".env not found (Slack notifications disabled). Create with:")
    _info("  echo SLACK_WEBHOOK_URL=https://hooks.slack.com/... > .env")
    return True  # Not required


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

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
    if all(results):
        print("  All checks passed! Run start.bat to launch.")
    else:
        print("  Some checks failed. Fix the issues above and re-run.")
    print()

    return all(results)


if __name__ == "__main__":
    auto = "--auto" in sys.argv or "-y" in sys.argv
    ok = check_environment(auto_install=auto)
    sys.exit(0 if ok else 1)
