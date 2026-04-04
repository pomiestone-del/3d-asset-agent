"""FBX file importer."""
from __future__ import annotations

from pathlib import Path

from asset_agent.exceptions import ImportError_
from asset_agent.importers.base import BaseImporter


class FbxImporter(BaseImporter):
    """Validate and prepare FBX files for import through Blender."""

    @property
    def supported_extensions(self) -> frozenset[str]:
        return frozenset({".fbx"})

    def validate_file(self, path: Path) -> None:
        if not path.exists():
            raise ImportError_(str(path), "file does not exist")
        if path.suffix.lower() != ".fbx":
            raise ImportError_(str(path), f"expected .fbx, got '{path.suffix}'")

    def build_import_args(self, path: Path) -> list[str]:
        return ["--model", str(path.resolve())]
