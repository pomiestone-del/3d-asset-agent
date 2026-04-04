"""MTL (Material Template Library) file parser.

Extracts per-material texture path declarations to assist PBR texture matching.
"""
from __future__ import annotations

import re
from pathlib import Path

# MTL keyword → PBR channel (lowercase keys)
_MTL_KEYWORD_TO_CHANNEL: dict[str, str | None] = {
    "map_kd":   "albedo",
    "map_ka":   "albedo",
    "map_bump": "normal",
    "bump":     "normal",
    "map_ks":   None,
    "map_ns":   "roughness",
    "map_d":    "opacity",
    "disp":     "displacement",
}


def parse_mtl(mtl_path: Path) -> dict[str, dict[str, Path]]:
    """Parse an MTL file and return per-material texture path declarations.

    Args:
        mtl_path: Path to the ``.mtl`` file.

    Returns:
        ``{material_name: {channel_name: absolute_path}}``.
        Only textures whose files exist on disk are included.
        Relative paths are resolved relative to the MTL file's directory.
    """
    if not mtl_path.exists():
        return {}

    base_dir = mtl_path.parent
    result: dict[str, dict[str, Path]] = {}
    current_mat: str | None = None

    with open(mtl_path, encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split(None, 1)
            if not parts:
                continue

            keyword = parts[0].lower()
            rest = parts[1].strip() if len(parts) > 1 else ""

            if keyword == "newmtl":
                current_mat = rest
                result.setdefault(current_mat, {})
                continue

            if current_mat is None or keyword not in _MTL_KEYWORD_TO_CHANNEL:
                continue

            channel = _MTL_KEYWORD_TO_CHANNEL[keyword]
            if channel is None:
                continue

            # Path is the last token (MTL statements can have option flags before it)
            tex_str = rest.split()[-1] if rest else ""
            if not tex_str:
                continue

            tex_path = Path(tex_str)
            if not tex_path.is_absolute():
                tex_path = (base_dir / tex_path).resolve()

            if tex_path.exists():
                result[current_mat][channel] = tex_path

    return result


def find_mtl_for_obj(obj_path: Path) -> Path | None:
    """Locate the MTL file referenced by an OBJ file.

    Searches for ``mtllib`` declaration in the OBJ, then falls back to
    same stem with ``.mtl`` extension.

    Args:
        obj_path: Path to the ``.obj`` file.

    Returns:
        Resolved path to the ``.mtl`` file, or ``None`` if not found.
    """
    if not obj_path.exists():
        return None

    mtllib_re = re.compile(r"^mtllib\s+(.+)$", re.IGNORECASE)

    with open(obj_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = mtllib_re.match(line.strip())
            if m:
                mtl_name = m.group(1).strip()
                candidate = (obj_path.parent / mtl_name).resolve()
                if candidate.exists():
                    return candidate

    default = obj_path.with_suffix(".mtl")
    return default if default.exists() else None
