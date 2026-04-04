"""OBJ file importer."""

from __future__ import annotations

from pathlib import Path

from asset_agent.exceptions import ImportError_
from asset_agent.importers.base import BaseImporter


class ObjImporter(BaseImporter):
    """Handles Wavefront .obj file imports."""

    @property
    def supported_extensions(self) -> frozenset[str]:
        return frozenset({".obj"})

    def validate_file(self, path: Path) -> None:
        """Check that *path* exists and looks like a valid OBJ file.

        Args:
            path: Path to the ``.obj`` file.

        Raises:
            ImportError_: If the file is missing or has the wrong extension.
        """
        if not path.exists():
            raise ImportError_(str(path), "File does not exist.")
        if path.suffix.lower() not in self.supported_extensions:
            raise ImportError_(str(path), f"Unsupported extension '{path.suffix}'.")

    def build_import_args(self, path: Path) -> list[str]:
        """Return arguments for the Blender script to import this OBJ.

        Args:
            path: Absolute path to the ``.obj`` file.
        """
        return ["--import-type", "OBJ", "--import-path", str(path.resolve())]
