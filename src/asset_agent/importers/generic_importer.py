"""Generic model importer — validates file existence and extension."""

from __future__ import annotations

from pathlib import Path

from asset_agent.exceptions import ImportError_
from asset_agent.importers.base import BaseImporter

# All extensions supported by the Blender-side import_model()
_ALL_SUPPORTED: frozenset[str] = frozenset({
    ".obj", ".fbx", ".blend", ".gltf", ".glb",
    ".stl", ".3ds", ".dxf", ".x3d", ".x3dv",
})


class GenericImporter(BaseImporter):
    """Validates any model file whose extension is handled by Blender."""

    @property
    def supported_extensions(self) -> frozenset[str]:
        return _ALL_SUPPORTED

    def validate_file(self, path: Path) -> None:
        if not path.exists():
            raise ImportError_(str(path), "File does not exist.")
        if path.suffix.lower() not in self.supported_extensions:
            raise ImportError_(
                str(path),
                f"Unsupported extension '{path.suffix}'. "
                f"Supported: {', '.join(sorted(self.supported_extensions))}",
            )

    def build_import_args(self, path: Path) -> list[str]:
        return ["--import-path", str(path.resolve())]
