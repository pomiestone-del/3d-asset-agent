"""GLB export orchestration (host-side).

This module does NOT call bpy directly — it delegates to the Blender
subprocess via ``blender_runner``.  The actual ``bpy.ops.export_scene.gltf``
call lives in ``blender_scripts/utils.py:export_glb``.

The class below exists so the ``agent.py`` orchestrator has a clean interface
for building export arguments and interpreting results.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from asset_agent.utils.logging import get_logger

logger = get_logger("exporters.glb")


@dataclass
class GlbExportSettings:
    """Parameters controlling GLB export behaviour.

    These map 1-to-1 to the flags understood by
    ``blender_scripts/process_asset.py`` and ultimately to
    ``bpy.ops.export_scene.gltf`` keyword arguments.
    """

    apply_modifiers: bool = True
    export_tangents: bool = True
    image_format: str = "AUTO"

    def as_blender_args(self) -> list[str]:
        """Serialize to CLI flags consumed by the Blender script.

        Currently, these settings are baked into
        ``blender_scripts/utils.py:export_glb``.  This method is a
        forward-looking hook for when per-export overrides are needed.
        """
        return []


def build_textures_payload(
    texture_map_dict: dict[str, Any],
) -> list[dict[str, Any]]:
    """Convert a ``TextureMap.as_dict()`` result into the JSON payload
    expected by the Blender processing script.

    Args:
        texture_map_dict: Mapping of ``{channel: TextureMatch}``.

    Returns:
        List of dicts ready for ``json.dumps``.
    """
    payload: list[dict[str, Any]] = []
    for channel, match in texture_map_dict.items():
        payload.append({
            "channel": channel,
            "path": str(match.path.resolve()),
            "color_space": match.color_space,
            "is_glossiness": match.is_glossiness,
        })
    return payload
