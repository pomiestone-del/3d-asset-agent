"""Blender-side utility helpers.

Runs inside Blender's embedded Python.  Do NOT import asset_agent modules.
Only stdlib + bpy are available.
"""

from __future__ import annotations

import logging
import math
import sys
from pathlib import Path

import bpy  # type: ignore[import-unresolved]
from mathutils import Vector  # type: ignore[import-unresolved]

log = logging.getLogger("blender_scripts")


# ---------------------------------------------------------------------------
# Bootstrapping
# ---------------------------------------------------------------------------

def bootstrap_script_dir() -> None:
    """Add the directory containing these scripts to *sys.path* so they can
    import each other when launched via ``blender --python``."""
    script_dir = str(Path(__file__).resolve().parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)


def setup_blender_logging(level: str = "INFO") -> None:
    """Configure a basic console logger for Blender scripts."""
    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="[%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )


# ---------------------------------------------------------------------------
# Scene helpers
# ---------------------------------------------------------------------------

def clean_scene() -> None:
    """Remove every object, material, image, and orphan data-block."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    log.info("Scene cleaned (factory-empty).")


def _append_from_blend(filepath: str) -> None:
    """Append all mesh objects (with materials and images) from a .blend file."""
    with bpy.data.libraries.load(filepath, link=False) as (data_from, data_to):
        data_to.objects = data_from.objects
        # Also pull in materials and images so textures are available
        data_to.materials = data_from.materials
        data_to.images = data_from.images

    collection = bpy.context.collection
    for obj in data_to.objects:
        if obj is not None:
            collection.objects.link(obj)
            # Ensure the object's mesh data-block is local
            if obj.data and obj.data.library:
                obj.data = obj.data.copy()
    log.info("Appended objects from '%s'.", filepath)


_SUPPORTED_EXTENSIONS = {
    ".obj", ".fbx", ".blend", ".gltf", ".glb",
    ".stl", ".3ds", ".dxf", ".x3d", ".x3dv",
}


def import_model(filepath: str) -> list[bpy.types.Object]:
    """Import a 3D model file and return new mesh objects.

    Supported formats: OBJ, FBX, BLEND, glTF/GLB, STL, 3DS, DXF, X3D.
    """
    ext = Path(filepath).suffix.lower()
    before = set(bpy.data.objects)

    if ext == ".obj":
        if bpy.app.version >= (3, 4, 0):
            bpy.ops.wm.obj_import(filepath=filepath)
        else:
            bpy.ops.import_scene.obj(filepath=filepath)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=filepath)
    elif ext == ".blend":
        _append_from_blend(filepath)
    elif ext in (".gltf", ".glb"):
        bpy.ops.import_scene.gltf(filepath=filepath)
    elif ext == ".stl":
        if bpy.app.version >= (3, 6, 0):
            bpy.ops.wm.stl_import(filepath=filepath)
        else:
            bpy.ops.import_mesh.stl(filepath=filepath)
    elif ext == ".3ds":
        # Blender 4.0 uses the new importer
        try:
            bpy.ops.wm.autodesk_3ds_import(filepath=filepath)
        except AttributeError:
            bpy.ops.import_scene.autodesk_3ds(filepath=filepath)
    elif ext == ".dxf":
        try:
            bpy.ops.import_scene.dxf(filepath=filepath)
        except Exception as exc:
            raise RuntimeError(
                f"DXF import failed (addon may not be enabled): {exc}"
            ) from exc
    elif ext in (".x3d", ".x3dv"):
        try:
            bpy.ops.import_scene.x3d(filepath=filepath)
        except Exception as exc:
            raise RuntimeError(
                f"X3D import failed (addon may not be enabled): {exc}"
            ) from exc
    else:
        raise RuntimeError(
            f"Unsupported model format: '{ext}'. "
            f"Supported: {', '.join(sorted(_SUPPORTED_EXTENSIONS))}"
        )

    after = set(bpy.data.objects)
    new_objs = [o for o in (after - before) if o.type == "MESH"]
    if not new_objs:
        raise RuntimeError(f"No mesh objects imported from '{filepath}'.")

    # Fix normals: clear custom split normals from OBJ import (vn lines may
    # be incorrect/inverted) and recalculate to ensure consistent outward
    # orientation.  This fixes black renders and see-through artifacts.
    for obj in new_objs:
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)

        # Clear custom split normals imported from OBJ vn data
        if obj.data.has_custom_normals:
            bpy.ops.mesh.customdata_custom_splitnormals_clear()
            log.info("  Cleared custom split normals on '%s'.", obj.name)

        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.normals_make_consistent(inside=False)
        bpy.ops.object.mode_set(mode="OBJECT")
        obj.select_set(False)

    log.info("Imported %d mesh(es) from '%s'.", len(new_objs), filepath)
    return new_objs


def import_obj(filepath: str) -> list[bpy.types.Object]:
    """Backward-compatible alias for import_model()."""
    return import_model(filepath)


def export_glb(filepath: str) -> None:
    """Export the current scene as a GLB file.

    Args:
        filepath: Destination ``.glb`` path.
    """
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=filepath,
        export_format="GLB",
        export_image_format="AUTO",
        export_materials="EXPORT",
        export_colors=True,
        export_apply=True,
        export_texcoords=True,
        export_normals=True,
        export_tangents=True,
        export_yup=True,
    )
    log.info("Exported GLB -> '%s'.", filepath)


# ---------------------------------------------------------------------------
# Bounding-box utilities
# ---------------------------------------------------------------------------

def get_scene_bbox(objects: list[bpy.types.Object]) -> tuple[Vector, Vector, Vector, float]:
    """Compute the axis-aligned bounding box for a list of mesh objects.

    Args:
        objects: Mesh objects to measure.

    Returns:
        Tuple of ``(center, bbox_min, bbox_max, diagonal_length)``.
    """
    all_coords: list[Vector] = []
    for obj in objects:
        matrix = obj.matrix_world
        for corner in obj.bound_box:
            all_coords.append(matrix @ Vector(corner))

    if not all_coords:
        return Vector((0, 0, 0)), Vector((0, 0, 0)), Vector((0, 0, 0)), 1.0

    xs = [v.x for v in all_coords]
    ys = [v.y for v in all_coords]
    zs = [v.z for v in all_coords]

    bbox_min = Vector((min(xs), min(ys), min(zs)))
    bbox_max = Vector((max(xs), max(ys), max(zs)))
    center = (bbox_min + bbox_max) / 2
    diagonal = (bbox_max - bbox_min).length

    return center, bbox_min, bbox_max, diagonal


# ---------------------------------------------------------------------------
# GLB validation (runs in a separate clean scene)
# ---------------------------------------------------------------------------

def validate_glb(glb_path: str) -> list[str]:
    """Re-import a GLB in a fresh scene and verify material integrity.

    Args:
        glb_path: Path to the ``.glb`` file.

    Returns:
        List of error strings (empty = pass).
    """
    errors: list[str] = []

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=glb_path)

    imported_meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    if not imported_meshes:
        errors.append("No mesh objects found after re-importing GLB.")
        return errors

    for mat in bpy.data.materials:
        if not mat.use_nodes:
            errors.append(f"Material '{mat.name}': use_nodes is False.")
            continue

        nodes = mat.node_tree.nodes
        bsdf_nodes = [n for n in nodes if n.type == "BSDF_PRINCIPLED"]
        if not bsdf_nodes:
            errors.append(f"Material '{mat.name}': no Principled BSDF node found.")
            continue

        bsdf = bsdf_nodes[0]
        tex_nodes = [n for n in nodes if n.type == "TEX_IMAGE"]

        # Only require Base Color link if the material has texture nodes;
        # color-only materials (from MTL Kd/Ks) use default_value instead.
        if tex_nodes:
            base_color_input = bsdf.inputs.get("Base Color")
            if base_color_input is None or not base_color_input.links:
                errors.append(f"Material '{mat.name}': Base Color input is not connected.")

        for tex in tex_nodes:
            if tex.image is None:
                errors.append(f"Material '{mat.name}': image node '{tex.name}' has no image.")
            elif tex.image.packed_file is None:
                errors.append(
                    f"Material '{mat.name}': image '{tex.image.name}' is not packed into the GLB."
                )

    if errors:
        log.warning("GLB validation found %d issue(s).", len(errors))
    else:
        log.info("GLB validation passed.")

    return errors
