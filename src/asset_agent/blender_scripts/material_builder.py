"""Principled BSDF node-tree builder for PBR materials.

Runs inside Blender's embedded Python.  Do NOT import asset_agent modules.
Only stdlib + bpy are available.

Single-material mode: textures list has no "material" field — one material for all objects.
Multi-material mode: textures entries have a "material" field — per-slot node trees.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import bpy  # type: ignore[import-unresolved]

log = logging.getLogger("blender_scripts.material_builder")

_X_TEX = -900
_X_MID = -500
_X_BSDF = 0
_Y_START = 400
_Y_STEP = -300


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_material(
    objects: list[bpy.types.Object],
    textures: list[dict[str, Any]],
    material_name: str = "PBR_Material",
) -> bpy.types.Material | None:
    """Create Principled BSDF material(s) and assign to objects.

    Single-material mode (no ``"material"`` field in any texture entry):
        Creates one material named *material_name* and assigns it to all objects.

    Multi-material mode (at least one entry has a ``"material"`` field):
        For each material name in the payload, finds the existing Blender material
        with that name (created during OBJ import) and rebuilds its node tree.
        Existing slot assignments on objects are preserved.

    Args:
        objects: Mesh objects to receive material(s).
        textures: List of texture descriptors.
        material_name: Used as the material name in single-material mode.

    Returns:
        The ``bpy.types.Material`` in single-material mode, ``None`` in multi-material mode.
    """
    if any("material" in t for t in textures):
        if any(t.get("assign_by_name") for t in textures):
            _assign_materials_by_mesh_name(objects, textures)
        else:
            _build_multi_materials(objects, textures)
        return None

    mat = _build_single_material(material_name, textures)
    for obj in objects:
        obj.data.materials.clear()
        obj.data.materials.append(mat)
    log.info("Material '%s' assigned to %d object(s).", material_name, len(objects))
    return mat


# ---------------------------------------------------------------------------
# Internal builders
# ---------------------------------------------------------------------------

def _build_single_material(
    material_name: str,
    textures: list[dict[str, Any]],
) -> bpy.types.Material:
    """Create and populate a new Principled BSDF material."""
    mat = bpy.data.materials.new(name=material_name)
    mat.use_nodes = True
    tree = mat.node_tree
    nodes = tree.nodes
    links = tree.links
    nodes.clear()

    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (_X_BSDF, 0)

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (300, 0)
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    tex_by_channel: dict[str, dict[str, Any]] = {t["channel"]: t for t in textures}
    y_cursor = _Y_START

    # ORM unpacking
    orm_separate_node = None
    if "orm" in tex_by_channel:
        orm_info = tex_by_channel["orm"]
        orm_tex, y_cursor = _add_tex_image(nodes, orm_info, y_cursor)
        if orm_tex is not None:
            orm_separate_node = nodes.new("ShaderNodeSeparateColor")
            orm_separate_node.location = (_X_MID, y_cursor + _Y_STEP)
            y_cursor += _Y_STEP
            links.new(orm_tex.outputs["Color"], orm_separate_node.inputs["Color"])
            links.new(orm_separate_node.outputs["Green"], bsdf.inputs["Roughness"])
            links.new(orm_separate_node.outputs["Blue"], bsdf.inputs["Metallic"])
            log.info("  ORM packed texture unpacked (R=AO, G=Rough, B=Metal).")

    for channel, connect_fn in _CHANNEL_WIRING.items():
        if channel == "orm":
            continue
        info = tex_by_channel.get(channel)
        if info is None:
            continue
        y_cursor = connect_fn(nodes, links, bsdf, info, y_cursor, orm_separate_node)

    if "opacity" in tex_by_channel:
        mat.blend_method = "HASHED"
        mat.shadow_method = "HASHED"
        log.info("  Blend method set to HASHED for opacity.")

    return mat


def _build_multi_materials(
    objects: list[bpy.types.Object],
    textures: list[dict[str, Any]],
) -> None:
    """Rebuild node trees for each named Blender material slot.

    Groups textures by their ``"material"`` field. For each group, finds the
    existing bpy.data.material with that name (created during OBJ import) and
    replaces its node tree with a Principled BSDF setup.
    """
    # Group textures by material name
    by_mat: dict[str, list[dict[str, Any]]] = {}
    for t in textures:
        key = t.get("material", "")
        if key:
            by_mat.setdefault(key, []).append(t)

    for mat_name, mat_textures in by_mat.items():
        if not mat_textures:
            log.info("Material '%s' has no textures; preserving imported material.", mat_name)
            continue

        existing = bpy.data.materials.get(mat_name)
        if existing is None:
            log.warning("Material '%s' not found in scene; creating new.", mat_name)
            existing = bpy.data.materials.new(name=mat_name)

        # Clear and rebuild node tree
        existing.use_nodes = True
        existing.node_tree.nodes.clear()
        existing.node_tree.links.clear()

        # Build in a temp material, then copy nodes/links across
        tmp = _build_single_material("__tmp__" + mat_name, mat_textures)
        _copy_node_tree(tmp.node_tree, existing.node_tree)

        if "opacity" in {t["channel"] for t in mat_textures}:
            existing.blend_method = "HASHED"
            existing.shadow_method = "HASHED"

        bpy.data.materials.remove(tmp)
        log.info("Updated material '%s' with %d texture(s).", mat_name, len(mat_textures))


def _copy_node_tree(
    src: bpy.types.NodeTree,
    dst: bpy.types.NodeTree,
) -> None:
    """Copy all nodes and links from *src* into *dst* (dst should be empty)."""
    node_map: dict[str, bpy.types.ShaderNode] = {}

    for src_node in src.nodes:
        dst_node = dst.nodes.new(src_node.bl_idname)
        dst_node.location = src_node.location
        dst_node.label = src_node.label
        node_map[src_node.name] = dst_node

        # Copy input default values
        for i, inp in enumerate(src_node.inputs):
            if i < len(dst_node.inputs):
                try:
                    dst_node.inputs[i].default_value = inp.default_value
                except Exception:
                    pass

        # Copy image reference for texture nodes
        if src_node.type == "TEX_IMAGE" and src_node.image:
            dst_node.image = src_node.image

        # Copy specific node properties
        if src_node.type == "MIX" and hasattr(src_node, "blend_type"):
            dst_node.blend_type = src_node.blend_type
            if hasattr(src_node, "data_type"):
                dst_node.data_type = src_node.data_type

    for lnk in src.links:
        src_out = node_map.get(lnk.from_node.name)
        dst_in = node_map.get(lnk.to_node.name)
        if src_out and dst_in:
            out_sock = src_out.outputs.get(lnk.from_socket.name)
            in_sock = dst_in.inputs.get(lnk.to_socket.name)
            if out_sock and in_sock:
                dst.links.new(out_sock, in_sock)


def _assign_materials_by_mesh_name(
    objects: list[bpy.types.Object],
    textures: list[dict[str, Any]],
) -> None:
    """Create per-set materials and assign to mesh objects by name overlap.

    Used when texture sets are auto-detected from the directory structure
    (``assign_by_name`` flag) and material slots don't yet exist.
    Each mesh object gets the material whose set name best matches its name.
    """
    # Group textures by set name (the "material" field)
    by_set: dict[str, list[dict[str, Any]]] = {}
    for t in textures:
        key = t.get("material", "")
        if key:
            # Strip assign_by_name flag before building material
            clean = {k: v for k, v in t.items() if k != "assign_by_name"}
            by_set.setdefault(key, []).append(clean)

    if not by_set:
        return

    # Build a PBR material for each texture set
    set_materials: dict[str, bpy.types.Material] = {}
    for set_name, set_textures in by_set.items():
        if not set_textures:
            continue
        mat = _build_single_material(set_name, set_textures)
        set_materials[set_name] = mat
        log.info("  Built material '%s' (%d textures).", set_name, len(set_textures))

    # Normalize name: strip trailing digits/separators for fuzzy comparison
    def _norm(name: str) -> str:
        return re.sub(r'[_\-\s]?\d+$', '', name.lower()).rstrip('_- ')

    norm_sets = {_norm(k): k for k in set_materials}

    # Assign materials to objects by longest common prefix match
    assigned = 0
    for obj in objects:
        obj_norm = _norm(obj.name)
        best_key = None
        best_len = 0
        for norm_set, orig_set in norm_sets.items():
            cpl = _common_prefix_len(obj_norm, norm_set)
            if cpl > best_len and cpl >= 4:
                best_len = cpl
                best_key = orig_set

        if best_key is not None:
            obj.data.materials.clear()
            obj.data.materials.append(set_materials[best_key])
            assigned += 1
        # else: keep whatever material was imported

    log.info(
        "Name-based assignment: %d/%d objects matched to %d material set(s).",
        assigned, len(objects), len(set_materials),
    )


def _common_prefix_len(a: str, b: str) -> int:
    """Return the length of the longest common prefix of *a* and *b*."""
    for i, (ca, cb) in enumerate(zip(a, b)):
        if ca != cb:
            return i
    return min(len(a), len(b))


# ---------------------------------------------------------------------------
# Internal helpers — one per channel
# ---------------------------------------------------------------------------

def _add_tex_image(
    nodes: bpy.types.NodeTree,
    info: dict[str, Any],
    y_cursor: float,
) -> tuple[bpy.types.ShaderNode | None, float]:
    tex = nodes.new("ShaderNodeTexImage")
    tex.location = (_X_TEX, y_cursor)
    try:
        img = bpy.data.images.load(info["path"], check_existing=True)
    except RuntimeError:
        log.warning("Cannot load texture '%s'; skipping.", info["path"])
        nodes.remove(tex)
        return None, y_cursor
    tex.image = img
    img.colorspace_settings.name = info.get("color_space", "Non-Color")
    tex.label = info["channel"]
    return tex, y_cursor


def _connect_albedo(nodes, links, bsdf, info, y_cursor, _orm_node) -> float:
    tex, y_cursor = _add_tex_image(nodes, info, y_cursor)
    if tex is None:
        return y_cursor
    links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    log.info("  albedo -> Base Color (%s)", info["color_space"])
    return y_cursor + _Y_STEP


def _connect_normal(nodes, links, bsdf, info, y_cursor, _orm_node) -> float:
    tex, y_cursor = _add_tex_image(nodes, info, y_cursor)
    if tex is None:
        return y_cursor
    normal_map = nodes.new("ShaderNodeNormalMap")
    normal_map.location = (_X_MID, y_cursor)
    links.new(tex.outputs["Color"], normal_map.inputs["Color"])
    links.new(normal_map.outputs["Normal"], bsdf.inputs["Normal"])
    log.info("  normal -> NormalMap -> Normal")
    return y_cursor + _Y_STEP


def _connect_roughness(nodes, links, bsdf, info, y_cursor, orm_node) -> float:
    if orm_node is not None:
        log.info("  roughness: skipped (ORM already connected).")
        return y_cursor
    tex, y_cursor = _add_tex_image(nodes, info, y_cursor)
    if tex is None:
        return y_cursor
    if info.get("is_glossiness"):
        invert = nodes.new("ShaderNodeInvert")
        invert.location = (_X_MID, y_cursor)
        links.new(tex.outputs["Color"], invert.inputs["Color"])
        links.new(invert.outputs["Color"], bsdf.inputs["Roughness"])
        log.info("  glossiness -> Invert -> Roughness")
    else:
        links.new(tex.outputs["Color"], bsdf.inputs["Roughness"])
        log.info("  roughness -> Roughness")
    return y_cursor + _Y_STEP


def _connect_metallic(nodes, links, bsdf, info, y_cursor, orm_node) -> float:
    if orm_node is not None:
        log.info("  metallic: skipped (ORM already connected).")
        return y_cursor
    tex, y_cursor = _add_tex_image(nodes, info, y_cursor)
    if tex is None:
        return y_cursor
    links.new(tex.outputs["Color"], bsdf.inputs["Metallic"])
    log.info("  metallic -> Metallic")
    return y_cursor + _Y_STEP


def _connect_ao(nodes, links, bsdf, info, y_cursor, _orm_node) -> float:
    tex, y_cursor = _add_tex_image(nodes, info, y_cursor)
    if tex is None:
        return y_cursor
    base_color_input = bsdf.inputs["Base Color"]
    if base_color_input.links:
        albedo_link = base_color_input.links[0]
        albedo_output = albedo_link.from_socket
        mix = nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.blend_type = "MULTIPLY"
        mix.location = (_X_MID, y_cursor)
        mix.inputs["Factor"].default_value = 1.0
        links.remove(albedo_link)
        links.new(albedo_output, mix.inputs[6])
        links.new(tex.outputs["Color"], mix.inputs[7])
        links.new(mix.outputs[2], base_color_input)
        log.info("  ao -> Multiply with Base Color")
    else:
        log.info("  ao: no Base Color connected; skipping mix.")
    return y_cursor + _Y_STEP


def _connect_emissive(nodes, links, bsdf, info, y_cursor, _orm_node) -> float:
    tex, y_cursor = _add_tex_image(nodes, info, y_cursor)
    if tex is None:
        return y_cursor
    emission_color_input = bsdf.inputs.get("Emission Color")
    if emission_color_input is None:
        emission_color_input = bsdf.inputs.get("Emission")
    if emission_color_input is not None:
        links.new(tex.outputs["Color"], emission_color_input)
    emission_strength = bsdf.inputs.get("Emission Strength")
    if emission_strength is not None:
        emission_strength.default_value = 1.0
    log.info("  emissive -> Emission Color")
    return y_cursor + _Y_STEP


def _connect_opacity(nodes, links, bsdf, info, y_cursor, _orm_node) -> float:
    tex, y_cursor = _add_tex_image(nodes, info, y_cursor)
    if tex is None:
        return y_cursor
    links.new(tex.outputs["Color"], bsdf.inputs["Alpha"])
    log.info("  opacity -> Alpha")
    return y_cursor + _Y_STEP


def _connect_displacement(nodes, links, bsdf, info, y_cursor, _orm_node) -> float:
    tex, y_cursor = _add_tex_image(nodes, info, y_cursor)
    if tex is None:
        return y_cursor
    disp_node = nodes.new("ShaderNodeDisplacement")
    disp_node.location = (_X_MID, y_cursor)
    disp_node.inputs["Scale"].default_value = 0.05
    links.new(tex.outputs["Color"], disp_node.inputs["Height"])
    output_nodes = [n for n in nodes if n.type == "OUTPUT_MATERIAL"]
    if output_nodes:
        links.new(disp_node.outputs["Displacement"], output_nodes[0].inputs["Displacement"])
    log.info("  displacement -> Displacement (render-only)")
    return y_cursor + _Y_STEP


_CHANNEL_WIRING: dict[str, Any] = {
    "albedo":       _connect_albedo,
    "normal":       _connect_normal,
    "roughness":    _connect_roughness,
    "metallic":     _connect_metallic,
    "ao":           _connect_ao,
    "emissive":     _connect_emissive,
    "opacity":      _connect_opacity,
    "displacement": _connect_displacement,
    "orm":          None,
}
