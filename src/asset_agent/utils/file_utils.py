"""Filesystem helpers shared across modules."""

from __future__ import annotations

from pathlib import Path

IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".tga", ".tiff", ".tif", ".exr", ".bmp"}
)


def collect_images(directory: Path, *, recursive: bool = True) -> list[Path]:
    """Return all image files under *directory* whose extension is in the whitelist.

    Args:
        directory: Root folder to scan.
        recursive: If ``True`` (default), descend into subdirectories.

    Returns:
        Sorted list of matching ``Path`` objects.
    """
    pattern = "**/*" if recursive else "*"
    return sorted(
        p for p in directory.glob(pattern) if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )


def ensure_directory(path: Path) -> Path:
    """Create *path* (and parents) if it doesn't exist, then return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path
