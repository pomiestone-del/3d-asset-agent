"""Abstract base class for 3D model importers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class BaseImporter(ABC):
    """Interface that every format-specific importer must implement."""

    @property
    @abstractmethod
    def supported_extensions(self) -> frozenset[str]:
        """File extensions this importer handles (lowercase, with leading dot)."""

    @abstractmethod
    def validate_file(self, path: Path) -> None:
        """Pre-import sanity check.

        Args:
            path: Path to the model file.

        Raises:
            ImportError_: If the file is invalid or unreadable.
        """

    @abstractmethod
    def build_import_args(self, path: Path) -> list[str]:
        """Return CLI arguments to pass to the Blender import script.

        Args:
            path: Path to the model file.

        Returns:
            List of string arguments for the Blender subprocess.
        """
