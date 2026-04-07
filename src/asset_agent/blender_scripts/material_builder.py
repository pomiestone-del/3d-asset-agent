"""Principled BSDF node-tree builder for PBR materials.

Runs inside Blender's embedded Python.  Do NOT import asset_agent modules.
Only stdlib + bpy are available.

Single-material mode: textures list has no "material" field — one material for all objects.
Multi-material mode: textures entries have a "material" field — per-slot node trees.

Supported channels
------------------
albedo        Direct RGBA/RGB albedo → Base Color (alpha NOT auto-wired; use opacity channel).
nnn_normal    Custom-packed normal map: image stores R=Z, G=X, B=Y of the surface normal.
              Connected via Separate Color → (G→X, B→Y, R→Z) → Combine XYZ → Normal Map.
normal        Standard OpenGL normal map → Normal Map → Normal.
mro           MRO packed texture (R=Metallic, G=Roughness, B=Occlusion).
              R → Metallic, G → Roughness; B (AO) is multiplied with Albedo (MULTIPLY×0.5).
orm           ORM packed texture (R=AO, G=Roughness, B=Metallic).
roughness     Standalone roughness (or glossiness → Invert → Roughness).
metallic      Standalone metallic.
ao            Standalone AO → MULTIPLY mix with Base Color.
emissive      Emission Color.
opacity       Alpha transparency.
displacement  Height displacement (render-only).

GLB export preparation
----------------------
Call ``prepare_for_glb_export()`` before exporting to GLB.  It patches every
material in the current scene to be glTF-compatible:
  * NNN normal chains (SeparateColor + CombineXYZ) are replaced by a new
    pixel-swizzled texture (R=src.G, G=src.B, B=src.R) connected directly.
  * AO MULTIPLY mix nodes between albedo and Base Color are removed, with the
    albedo re-connected directly to Base Color (AO applied during rendering only).
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

_RAMP_RE = re.compile(r"(?<![a-z])ramp(?![a-z])", re.IGNORECASE)


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

    # ORM unpacking (R=AO, G=Roughness, B=Metallic)
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

    # MRO unpacking (R=Metallic, G=Roughness, B=AO) — project-specific packing
    mro_separate_node = None
    if "mro" in tex_by_channel and orm_separate_node is None:
        mro_info = tex_by_channel["mro"]
        mro_tex, y_cursor = _add_tex_image(nodes, mro_info, y_cursor)
        if mro_tex is not None:
            mro_separate_node = nodes.new("ShaderNodeSeparateColor")
            mro_separate_node.location = (_X_MID, y_cursor)
            y_cursor += _Y_STEP
            links.new(mro_tex.outputs["Color"], mro_separate_node.inputs["Color"])
            links.new(mro_separate_node.outputs["Red"], bsdf.inputs["Metallic"])
            links.new(mro_separate_node.outputs["Green"], bsdf.inputs["Roughness"])
            log.info("  MRO packed texture unpacked (R=Metal, G=Rough, B=AO).")

    channels_done: set[str] = {"orm", "mro"}

    # Albedo — if MRO is present, mix albedo with MRO.B (AO) using MULTIPLY
    if "albedo" in tex_by_channel:
        info = tex_by_channel["albedo"]
        if mro_separate_node is not None:
            y_cursor = _connect_albedo_mro(nodes, links, bsdf, info, y_cursor,
                                           mro_separate_node)
        else:
            y_cursor = _connect_albedo(nodes, links, bsdf, info, y_cursor, orm_separate_node)
        channels_done.add("albedo")

    for channel, connect_fn in _CHANNEL_WIRING.items():
        if channel in channels_done or connect_fn is None:
            continue
        info = tex_by_channel.get(channel)
        if info is None:
            continue
        y_cursor = connect_fn(nodes, links, bsdf, info, y_cursor,
                              orm_separate_node or mro_separate_node)

    # Enable transparency blend mode only when an explicit opacity channel is present.
    # Do NOT trigger on RGBA albedo textures — the alpha channel there encodes cutouts
    # only when the artist explicitly adds an "opacity" texture entry.  Auto-wiring
    # RGBA albedo → Alpha causes unwanted transparency in glTF viewers.
    has_alpha = "opacity" in tex_by_channel
    if has_alpha:
        mat.blend_method = "HASHED"
        mat.shadow_method = "HASHED"
        log.info("  Blend method set to HASHED for transparency.")

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


def _connect_albedo_mro(
    nodes, links, bsdf, info, y_cursor,
    mro_sep: bpy.types.ShaderNode,
) -> float:
    """Connect albedo texture mixed with MRO.B (AO) via MULTIPLY node.

    Node graph built::

        AAAX/AAAT ─────────────────────► Mix.A ──► Mix.Result ──► Base Color
        MROX SeparateColor.Blue (AO) ──► Mix.B

    The Mix node uses ``MULTIPLY`` blend mode at Factor=0.5, matching the
    artist's convention in the source blend files.
    """
    tex, y_cursor = _add_tex_image(nodes, info, y_cursor)
    if tex is None:
        return y_cursor

    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    mix.location = (_X_MID, y_cursor)
    mix.inputs["Factor"].default_value = 0.5

    links.new(tex.outputs["Color"], mix.inputs["A"])
    links.new(mro_sep.outputs["Blue"], mix.inputs["B"])
    links.new(mix.outputs["Result"], bsdf.inputs["Base Color"])

    log.info("  albedo × MRO.AO (MULTIPLY×0.5) -> Base Color")
    return y_cursor + _Y_STEP


def _connect_nnn_normal(nodes, links, bsdf, info, y_cursor, _sep_node) -> float:
    """Connect NNN-packed normal map with the project-specific channel swizzle.

    The NNN texture stores normals as: R=normalZ, G=normalX, B=normalY.
    The required graph::

        NNNX ──► SeparateColor ──► G → CombineXYZ.X
                                   B → CombineXYZ.Y
                                   R → CombineXYZ.Z
                              CombineXYZ ──► NormalMap.Color ──► BSDF.Normal
    """
    tex, y_cursor = _add_tex_image(nodes, info, y_cursor)
    if tex is None:
        return y_cursor

    sep = nodes.new("ShaderNodeSeparateColor")
    sep.location = (_X_MID - 200, y_cursor)

    combine = nodes.new("ShaderNodeCombineXYZ")
    combine.location = (_X_MID, y_cursor)

    normal_map = nodes.new("ShaderNodeNormalMap")
    normal_map.location = (_X_MID + 200, y_cursor)

    links.new(tex.outputs["Color"], sep.inputs["Color"])

    # Channel swizzle: image R→Z, G→X, B→Y
    links.new(sep.outputs["Green"], combine.inputs["X"])
    links.new(sep.outputs["Blue"], combine.inputs["Y"])
    links.new(sep.outputs["Red"], combine.inputs["Z"])

    links.new(combine.outputs["Vector"], normal_map.inputs["Color"])
    links.new(normal_map.outputs["Normal"], bsdf.inputs["Normal"])

    log.info("  nnn_normal -> SeparateColor (G→X,B→Y,R→Z) -> CombineXYZ -> NormalMap -> Normal")
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
    "nnn_normal":   _connect_nnn_normal,
    "normal":       _connect_normal,
    "roughness":    _connect_roughness,
    "metallic":     _connect_metallic,
    "ao":           _connect_ao,
    "emissive":     _connect_emissive,
    "opacity":      _connect_opacity,
    "displacement": _connect_displacement,
    "orm":          None,
    "mro":          None,   # handled before the wiring loop
    "ramp":         None,   # not connected — glTF-incompatible UV method; removed by prepare_for_glb_export
}


# ---------------------------------------------------------------------------
# GLB export preparation — rewire nodes to glTF-compatible topology
# ---------------------------------------------------------------------------

def prepare_for_glb_export() -> None:
    """Patch all scene materials so Blender's glTF exporter can read them.

    Four transformations are applied per material:

    1. **NNN normal chains** — ``SeparateColor → CombineXYZ → NormalMap``
       is replaced by a pixel-swizzled image in standard OpenGL convention
       (new.R=src.G, new.G=src.B, new.B=src.R), connected directly as
       ``TEX_IMAGE → NormalMap → BSDF.Normal``.

    2. **MRO → ORM remapping** — MROX textures store R=Metal, G=Rough, B=AO.
       glTF metallicRoughnessTexture requires R=AO, G=Rough, B=Metal.
       A new ORM image is created (R=src.B, G=src.G, B=src.R) and the
       SeparateColor connections are updated to Blue→Metallic, Green→Roughness.

    3. **Ramp texture removal** — Gradient/ramp textures use geometry-normal-based
       UV access which is incompatible with glTF.  Any ramp TEX_IMAGE node is
       removed; if it feeds a Mix node, the non-ramp input is reconnected to the
       Mix's downstream targets so the rest of the chain is preserved.

    4. **AO multiply mix** — A ``MULTIPLY`` Mix node between albedo and
       ``BSDF.Base Color`` is removed; the albedo texture is reconnected
       directly.  AO is a render-time effect; glTF stores it separately.

    5. **Backface culling** — ``use_backface_culling`` is set to ``False`` so
       the glTF exporter marks every mesh as ``doubleSided: true``.  Character
       assets (hair planes, cloth, eyelashes) must be rendered from both sides;
       the Blender default of ``True`` causes back-facing polygons to be culled
       in glTF viewers, producing black/transparent patches or inverted-normal
       artefacts.
    """
    for mat in bpy.data.materials:
        if not mat.use_nodes or mat.node_tree is None:
            continue
        _patch_nnn_normal_for_gltf(mat)
        _patch_mro_for_gltf(mat)
        _patch_ramp_for_gltf(mat)
        _patch_ao_mix_for_gltf(mat)
        mat.use_backface_culling = False


# ---------------------------------------------------------------------------
# Shared pixel-manipulation helpers (used by the GLB patch functions below)
# ---------------------------------------------------------------------------

def _find_bsdf(nodes) -> "bpy.types.ShaderNode | None":
    return next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)


def _load_image_pixels(
    img: "bpy.types.Image",
    mat_name: str,
) -> "np.ndarray | None":
    """Load pixel data from *img* into a float32 numpy array shaped (H, W, 4).

    Force-loads packed images first.  Returns ``None`` if data is unavailable.
    """
    import numpy as np  # bundled with Blender 4.x+

    w, h = img.size[0], img.size[1]
    if w == 0 or h == 0:
        log.warning("  GLB patch [%s]: image '%s' has zero size; skipping.", mat_name, img.name)
        return None

    # Force-load packed images (Blender lazily defers this).
    try:
        _ = img.pixels[0]
    except Exception as exc:
        log.warning("  GLB patch [%s]: cannot access '%s' pixels: %s; skipping.", mat_name, img.name, exc)
        return None

    buf = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(buf)

    # foreach_get may still return zeros for some packed-image states.
    if not np.any(buf):
        buf = np.array(img.pixels[:], dtype=np.float32)

    if not np.any(buf):
        log.warning("  GLB patch [%s]: image '%s' pixel data is all-zero; skipping.", mat_name, img.name)
        return None

    return buf.reshape(h, w, 4)


def _save_image_pixels(img: "bpy.types.Image", pixels: "np.ndarray") -> None:
    """Write *(H, W, 4)* float32 *pixels* back to *img* and repack if needed."""
    img.pixels.foreach_set(pixels.flatten())
    img.update()
    if img.packed_file is not None:
        img.pack()


def _patch_nnn_normal_for_gltf(mat: bpy.types.Material) -> None:
    """Replace the NNN channel-swizzle chain with a pixel-reordered normal map."""
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    bsdf = _find_bsdf(nodes)
    if bsdf is None:
        return

    normal_input = bsdf.inputs.get("Normal")
    if not normal_input or not normal_input.links:
        return

    nm_node = normal_input.links[0].from_node
    if nm_node.type != "NORMAL_MAP":
        return

    color_input = nm_node.inputs.get("Color")
    if not color_input or not color_input.links:
        return

    combine_node = color_input.links[0].from_node
    if combine_node.type != "COMBXYZ":
        return

    # Trace back to the SeparateColor node
    sep_node = None
    for inp in combine_node.inputs:
        if inp.links and inp.links[0].from_node.type == "SEPARATE_COLOR":
            sep_node = inp.links[0].from_node
            break
    if sep_node is None:
        return

    sep_color_inp = sep_node.inputs.get("Color")
    if not sep_color_inp or not sep_color_inp.links:
        return

    tex_node = sep_color_inp.links[0].from_node
    if tex_node.type != "TEX_IMAGE" or tex_node.image is None:
        return

    src_img = tex_node.image
    # Reorder channels in-place on the ORIGINAL image so tex_node keeps working.
    # src: R=normalZ, G=normalX, B=normalY  →  dst: R=normalX, G=normalY, B=normalZ (OpenGL)
    src_pixels = _load_image_pixels(src_img, mat.name)
    if src_pixels is None:
        return

    dst_pixels = src_pixels.copy()
    dst_pixels[:, :, 0] = src_pixels[:, :, 1]  # R = old G (normalX)
    dst_pixels[:, :, 1] = src_pixels[:, :, 2]  # G = old B (normalY)
    dst_pixels[:, :, 2] = src_pixels[:, :, 0]  # B = old R (normalZ)
    # Alpha channel unchanged

    _save_image_pixels(src_img, dst_pixels)

    # Remove only the swizzle chain; keep tex_node (it still points to src_img).
    nodes.remove(sep_node)
    nodes.remove(combine_node)

    # Reconnect: tex_node.Color → NormalMap.Color directly
    links.new(tex_node.outputs["Color"], nm_node.inputs["Color"])

    log.info(
        "  GLB patch [%s]: NNN swizzle applied in-place to '%s' (sep/combine nodes removed).",
        mat.name, src_img.name,
    )


def _patch_mro_for_gltf(mat: bpy.types.Material) -> None:
    """Convert MROX (R=Metal, G=Rough, B=AO) texture to glTF ORM (R=AO, G=Rough, B=Metal).

    Detects the pattern: SeparateColor.Red → BSDF.Metallic with the same
    SeparateColor.Green → BSDF.Roughness.  Creates a pixel-swapped ORM image
    (new.R=src.B, new.G=src.G, new.B=src.R) and reconnects
    SeparateColor.Blue → Metallic so the glTF exporter reads Metal from the
    correct channel.
    """
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    bsdf = _find_bsdf(nodes)
    if bsdf is None:
        return

    metallic_inp = bsdf.inputs.get("Metallic")
    if not metallic_inp or not metallic_inp.links:
        return

    sep_link = metallic_inp.links[0]
    if sep_link.from_socket.name != "Red":
        return
    sep_node = sep_link.from_node
    if sep_node.type != "SEPARATE_COLOR":
        return

    # Confirm the same SeparateColor also drives Roughness from Green
    roughness_inp = bsdf.inputs.get("Roughness")
    if not roughness_inp or not roughness_inp.links:
        return
    if roughness_inp.links[0].from_node is not sep_node:
        return
    if roughness_inp.links[0].from_socket.name != "Green":
        return

    # Get the MROX texture node
    color_inp = sep_node.inputs.get("Color")
    if not color_inp or not color_inp.links:
        return
    tex_node = color_inp.links[0].from_node
    if tex_node.type != "TEX_IMAGE" or tex_node.image is None:
        return

    src_img = tex_node.image
    # Pixel-swap: R=AO(src.B), G=Rough(src.G unchanged), B=Metal(src.R)
    src_pixels = _load_image_pixels(src_img, mat.name)
    if src_pixels is None:
        return

    h, w = src_pixels.shape[:2]
    dst_pixels = src_pixels.copy()
    dst_pixels[:, :, 0] = src_pixels[:, :, 2]  # R = old B (AO)
    dst_pixels[:, :, 2] = src_pixels[:, :, 0]  # B = old R (Metal)
    # Green (Roughness) and Alpha unchanged

    orm_name = src_img.name
    for suffix in ("_MROX", "_mrox", "_MRO", "_mro"):
        if orm_name.endswith(suffix):
            orm_name = orm_name[: -len(suffix)] + "_ORM_gltf"
            break
    else:
        orm_name = orm_name + "_orm_gltf"

    if orm_name in bpy.data.images:
        orm_img = bpy.data.images[orm_name]
    else:
        orm_img = bpy.data.images.new(orm_name, w, h, alpha=False, float_buffer=False)
        orm_img.pixels.foreach_set(dst_pixels.flatten())
        orm_img.colorspace_settings.name = "Non-Color"
        orm_img.pack()

    tex_node.image = orm_img

    # Drop the original MROX image — no longer referenced by any node
    if src_img.users == 0:
        bpy.data.images.remove(src_img)

    # Reconnect: Red → Metallic becomes Blue → Metallic
    for lnk in list(metallic_inp.links):
        links.remove(lnk)
    links.new(sep_node.outputs["Blue"], metallic_inp)

    log.info(
        "  GLB patch [%s]: MROX remapped to ORM (R=AO, G=Rough, B=Metal) → '%s'.",
        mat.name, orm_name,
    )


def _ramp_uses_standard_uv(ramp_node: bpy.types.ShaderNode) -> bool:
    """Return True if the ramp texture is accessed via a standard UV map.

    glTF supports UV0/UV1 channel access only.  A ramp node is compatible when:
      - Its Vector input is unconnected (Blender defaults to the active UV map).
      - Its Vector input is driven by a UV Map node.
      - Its Vector input is driven by a Texture Coordinate node's "UV" socket,
        optionally through a Mapping node.

    Everything else (Normal, Generated, Object, Geometry…) is procedural and
    incompatible with glTF.
    """
    vec_inp = ramp_node.inputs.get("Vector")
    if vec_inp is None or not vec_inp.links:
        # No Vector input → uses active UV map → compatible
        return True

    def _source_is_uv(node, socket_name: str) -> bool:
        """Recursively chase Mapping nodes back to the UV origin."""
        if node.type == "UVMAP":
            return True
        if node.type == "TEX_COORD":
            return socket_name == "UV"
        if node.type == "MAPPING":
            # Mapping transforms a vector — trace its own Vector input
            inp = node.inputs.get("Vector")
            if inp and inp.links:
                src = inp.links[0]
                return _source_is_uv(src.from_node, src.from_socket.name)
        return False

    src_link = vec_inp.links[0]
    return _source_is_uv(src_link.from_node, src_link.from_socket.name)


def _patch_ramp_for_gltf(mat: bpy.types.Material) -> None:
    """Remove procedural ramp textures; keep UV-mapped ramps as regular textures.

    Ramp textures accessed via geometry normals, Generated, Object, or other
    procedural coordinates cannot be represented in glTF and are removed:

    * **Via Mix node**: the non-ramp input is wired to the Mix's downstream
      targets and the Mix node is deleted.
    * **Direct connection**: the link is simply severed.

    Ramp textures that use a standard UV map (UV0/UV1) are left untouched —
    they are valid glTF textures and the exporter handles them normally.
    """
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    ramp_nodes = [
        n for n in list(nodes)
        if n.type == "TEX_IMAGE"
        and n.image is not None
        and (_RAMP_RE.search(n.image.name) or _RAMP_RE.search(n.label or ""))
    ]

    if not ramp_nodes:
        return

    for ramp_node in ramp_nodes:
        if _ramp_uses_standard_uv(ramp_node):
            log.info(
                "  GLB patch [%s]: ramp '%s' uses standard UV — keeping.",
                mat.name, ramp_node.image.name,
            )
            continue

        color_out = ramp_node.outputs.get("Color")
        if color_out is None:
            continue

        for lnk in list(color_out.links):
            to_node = lnk.to_node
            ramp_slot = lnk.to_socket.name  # "A" or "B" on a Mix node

            if to_node.type == "MIX":
                other_slot = "B" if ramp_slot == "A" else "A"
                other_inp = to_node.inputs.get(other_slot)
                other_src = (
                    other_inp.links[0].from_socket
                    if other_inp and other_inp.links
                    else None
                )

                # Rewire every output of the Mix to the non-ramp source.
                for out_sock in to_node.outputs:
                    for out_lnk in list(out_sock.links):
                        dest = out_lnk.to_socket
                        links.remove(out_lnk)
                        if other_src is not None:
                            links.new(other_src, dest)

                nodes.remove(to_node)
            else:
                links.remove(lnk)

        ramp_img = ramp_node.image
        ramp_name = ramp_img.name if ramp_img else "?"
        nodes.remove(ramp_node)
        if ramp_img and ramp_img.users == 0:
            bpy.data.images.remove(ramp_img)

        log.info(
            "  GLB patch [%s]: ramp '%s' removed (procedural UV).",
            mat.name, ramp_name,
        )


def _patch_ao_mix_for_gltf(mat: bpy.types.Material) -> None:
    """Remove AO MULTIPLY Mix node; reconnect albedo directly to Base Color."""
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return

    base_color_inp = bsdf.inputs.get("Base Color")
    if not base_color_inp or not base_color_inp.links:
        return

    mix_node = base_color_inp.links[0].from_node
    if mix_node.type != "MIX":
        return
    if getattr(mix_node, "blend_type", None) != "MULTIPLY":
        return

    # Trace upstream through chained Mix nodes to find the root albedo TEX_IMAGE
    albedo_socket = _find_albedo_socket(mix_node)
    if albedo_socket is None:
        return

    # Collect every Mix node in the chain before rewiring (removing links clears them)
    mix_chain: list[bpy.types.ShaderNode] = []
    cur = mix_node
    visited: set = set()
    while cur is not None and id(cur) not in visited and cur.type == "MIX":
        visited.add(id(cur))
        mix_chain.append(cur)
        # Walk the A input to find a chained Mix upstream
        a_inp = cur.inputs.get("A")
        cur = a_inp.links[0].from_node if (a_inp and a_inp.links) else None
        if cur and getattr(cur, "blend_type", None) != "MULTIPLY":
            cur = None

    # Rewire: reconnect albedo directly to Base Color
    for lnk in list(base_color_inp.links):
        links.remove(lnk)
    links.new(albedo_socket, base_color_inp)

    # Remove all orphaned Mix nodes in the chain
    for mx in mix_chain:
        try:
            nodes.remove(mx)
        except Exception:
            pass  # already removed or invalid

    log.info(
        "  GLB patch [%s]: AO MULTIPLY mix removed; albedo '%s' connected directly.",
        mat.name, albedo_socket.node.image.name if albedo_socket.node.type == "TEX_IMAGE" else "?",
    )


def _mix_input_sort_key(inp) -> int:
    """Sort Mix node inputs A-first so albedo on slot A is found before AO/ramp on slot B."""
    n = inp.name
    if n == "A":
        return 0
    if n == "B":
        return 2
    return 1


def _find_albedo_socket(mix_node: bpy.types.ShaderNode):
    """Walk upstream Mix chain (A input) to find the first TEX_IMAGE Color socket.

    Iterates inputs in ``A``-first order so ramp or AO textures wired to the
    ``B`` slot are never returned instead of the actual albedo on ``A``.
    """
    visited: set = set()
    current = mix_node
    while current is not None and id(current) not in visited:
        visited.add(id(current))

        linked_inputs = [i for i in current.inputs if i.links]
        linked_inputs.sort(key=_mix_input_sort_key)

        advanced = False
        for inp in linked_inputs:
            src_node = inp.links[0].from_node
            src_socket = inp.links[0].from_socket
            if src_node.type == "TEX_IMAGE":
                return src_socket
            if src_node.type == "MIX" and getattr(src_node, "blend_type", None) == "MULTIPLY":
                current = src_node
                advanced = True
                break

        if not advanced:
            break
    return None
