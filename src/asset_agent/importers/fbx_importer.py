"""FBX file importer — stub for future implementation."""

from __future__ import annotations

from pathlib import Path

from asset_agent.importers.base import BaseImporter


class FbxImporter(BaseImporter):
    """Placeholder for FBX import support (not yet implemented)."""

    @property
    def supported_extensions(self) -> frozenset[str]:
        return frozenset({".fbx"})

    def validate_file(self, path: Path) -> None:
        raise NotImplementedError("FBX import is not yet supported.")

    def build_import_args(self, path: Path) -> list[str]:
        raise NotImplementedError("FBX import is not yet supported.")
