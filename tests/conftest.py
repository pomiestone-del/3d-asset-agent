"""Shared pytest fixtures for the 3D asset agent test suite."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
TEXTURES_DIR = FIXTURES_DIR / "textures"

BLENDER_EXE = r"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe"


def _blender_available() -> bool:
    return Path(BLENDER_EXE).is_file() or shutil.which("blender") is not None


requires_blender = pytest.mark.skipif(
    not _blender_available(),
    reason="Blender not found; skipping end-to-end test.",
)


@pytest.fixture()
def fixtures_dir() -> Path:
    """Return the path to the test fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture()
def textures_dir() -> Path:
    """Return the path to the test textures directory."""
    return TEXTURES_DIR


@pytest.fixture()
def cube_obj() -> Path:
    """Return the path to the test cube .obj file."""
    return FIXTURES_DIR / "cube.obj"


@pytest.fixture()
def blender_exe() -> str:
    """Return a usable Blender executable path."""
    if Path(BLENDER_EXE).is_file():
        return BLENDER_EXE
    resolved = shutil.which("blender")
    if resolved:
        return resolved
    pytest.skip("Blender not found")
    return ""  # unreachable, keeps type checker happy
