"""Principled BSDF node-tree builder for PBR materials.

Runs inside Blender's embedded Python.  Do NOT import asset_agent modules.
Only stdlib + bpy are available.

Expects texture data as a list of dicts (deserialized from JSON), each with::

    {
        "channel":       "albedo",
        "path":          "/abs/path/to/texture.png",
        "color_space":   "sRGB" | "Non-Color",
        "is_glossiness": false
    }
"""

from __future__ import annotations

import logging
from typing import Any

import bpy  # type: ignore[import-unresolved]

log = logging.getLogger("blender_scripts.material_builder")

# Node-tree layout constants (left-to-right, top-to-bottom).
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
) -> bpy.types.Material:
    """Create a Principled BSDF material, wire up PBR textures, and assign it.

    Args:
        objects: Mesh objects to receive the material.
        textures: List of texture descriptors (see module docstring).
        material_name: Name for the new material.

    Returns:
        The newly created ``bpy.types.Material``.
    """
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

    tex_by_channel: dict[str, dict[str, Any]] = {}
    for t in textures:
        tex_by_channel[t["channel"]] = t

    y_cursor = _Y_START

    # --- ORM unpacking (must happen before individual roughness/metallic) ---
    orm_separate_node = None
    if "orm" in tex_by_channel:
        orm_info = tex_by_channel["orm"]
        orm_tex, y_cursor = _add_tex_image(nodes, orm_info, y_cursor)
        orm_separate_node = nodes.new("ShaderNodeSeparateColor")
        orm_separate_node.location = (_X_MID, y_cursor + _Y_STEP)
        y_cursor += _Y_STEP
        links.new(orm_tex.outputs["Color"], orm_separate_node.inputs["Color"])
        # R = AO  (not connected to BSDF by default; AO typically goes to a mix)
        # G = Roughness
        links.new(orm_separate_node.outputs["Green"], bsdf.inputs["Roughness"])
        # B = Metallic
        links.new(orm_separate_node.outputs["Blue"], bsdf.inputs["Metallic"])
        log.info("  ORM packed texture unpacked (R=AO, G=Rough, B=Metal).")

    # --- Per-channel wiring ---

    for channel, connect_fn in _CHANNEL_WIRING.items():
        if channel == "orm":
            continue  # already handled above
        info = tex_by_channel.get(channel)
        if info is None:
            continue
        y_cursor = connect_fn(nodes, links, bsdf, info, y_cursor, orm_separate_node)

    # --- Opacity blend mode ---
    if "opacity" in tex_by_channel:
        mat.blend_method = "HASHED"
        mat.shadow_method = "HASHED"
        log.info("  Blend method set to HASHED for opacity.")

    # --- Assign to objects ---
    for obj in objects:
        obj.data.materials.clear()
        obj.data.materials.append(mat)

    log.info("Material '%s' assigned to %d object(s).", material_name, len(objects))
    return mat


# ---------------------------------------------------------------------------
# Internal helpers – one per channel
# ---------------------------------------------------------------------------

def _add_tex_image(
    nodes: bpy.types.NodeTree,
    info: dict[str, Any],
    y_cursor: float,
) -> tuple[bpy.types.ShaderNode, float]:
    """Create a TEX_IMAGE node, load the image, set color-space, position it."""
    tex = nodes.new("ShaderNodeTexImage")
    tex.location = (_X_TEX, y_cursor)

    img = bpy.data.images.load(info["path"], check_existing=True)
    tex.image = img
    img.colorspace_settings.name = info.get("color_space", "Non-Color")

    tex.label = info["channel"]
    return tex, y_cursor


def _connect_albedo(
    nodes, links, bsdf, info, y_cursor, _orm_node,
) -> float:
    tex, y_cursor = _add_tex_image(nodes, info, y_cursor)
    links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    log.info("  albedo -> Base Color (%s)", info["color_space"])
    return y_cursor + _Y_STEP


def _connect_normal(
    nodes, links, bsdf, info, y_cursor, _orm_node,
) -> float:
    tex, y_cursor = _add_tex_image(nodes, info, y_cursor)
    normal_map = nodes.new("ShaderNodeNormalMap")
    normal_map.location = (_X_MID, y_cursor)
    links.new(tex.outputs["Color"], normal_map.inputs["Color"])
    links.new(normal_map.outputs["Normal"], bsdf.inputs["Normal"])
    log.info("  normal -> NormalMap -> Normal")
    return y_cursor + _Y_STEP


def _connect_roughness(
    nodes, links, bsdf, info, y_cursor, orm_node,
) -> float:
    if orm_node is not None:
        log.info("  roughness: skipped (ORM already connected).")
        return y_cursor

    tex, y_cursor = _add_tex_image(nodes, info, y_cursor)

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


def _connect_metallic(
    nodes, links, bsdf, info, y_cursor, orm_node,
) -> float:
    if orm_node is not None:
        log.info("  metallic: skipped (ORM already connected).")
        return y_cursor

    tex, y_cursor = _add_tex_image(nodes, info, y_cursor)
    links.new(tex.outputs["Color"], bsdf.inputs["Metallic"])
    log.info("  metallic -> Metallic")
    return y_cursor + _Y_STEP


def _connect_ao(
    nodes, links, bsdf, info, y_cursor, _orm_node,
) -> float:
    """AO is mixed into Base Color via a MixRGB (Multiply) node."""
    tex, y_cursor = _add_tex_image(nodes, info, y_cursor)

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
        links.new(albedo_output, mix.inputs[6])       # A (RGBA)
        links.new(tex.outputs["Color"], mix.inputs[7]) # B (RGBA)
        links.new(mix.outputs[2], base_color_input)    # Result (RGBA)
        log.info("  ao -> Multiply with Base Color")
    else:
        log.info("  ao: no Base Color connected; skipping mix.")

    return y_cursor + _Y_STEP


def _connect_emissive(
    nodes, links, bsdf, info, y_cursor, _orm_node,
) -> float:
    tex, y_cursor = _add_tex_image(nodes, info, y_cursor)

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


def _connect_opacity(
    nodes, links, bsdf, info, y_cursor, _orm_node,
) -> float:
    tex, y_cursor = _add_tex_image(nodes, info, y_cursor)
    links.new(tex.outputs["Color"], bsdf.inputs["Alpha"])
    log.info("  opacity -> Alpha")
    return y_cursor + _Y_STEP


def _connect_displacement(
    nodes, links, bsdf, info, y_cursor, _orm_node,
) -> float:
    """Displacement is render-only (not exported in GLB)."""
    tex, y_cursor = _add_tex_image(nodes, info, y_cursor)

    disp_node = nodes.new("ShaderNodeDisplacement")
    disp_node.location = (_X_MID, y_cursor)
    disp_node.inputs["Scale"].default_value = 0.05
    links.new(tex.outputs["Color"], disp_node.inputs["Height"])

    output_nodes = [n for n in nodes if n.type == "OUTPUT_MATERIAL"]
    if output_nodes:
        links.new(disp_node.outputs["Displacement"], output_nodes[0].inputs["Displacement"])

    log.info("  displacement -> Displacement (render-only)")
    return y_cursor + _Y_STEP


# Channel → wiring function lookup (ordered to match typical BSDF slot order).
_CHANNEL_WIRING: dict[str, Any] = {
    "albedo":       _connect_albedo,
    "normal":       _connect_normal,
    "roughness":    _connect_roughness,
    "metallic":     _connect_metallic,
    "ao":           _connect_ao,
    "emissive":     _connect_emissive,
    "opacity":      _connect_opacity,
    "displacement": _connect_displacement,
    "orm":          None,  # handled separately
}
