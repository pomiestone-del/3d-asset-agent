"""Blender main entry-point script 鈥� orchestrates the full processing pipeline.

Runs inside Blender's embedded Python via::

    blender --background --factory-startup --python process_asset.py -- <args>

Do NOT import asset_agent modules.  Only stdlib + bpy are available.
Sibling modules (material_builder, scene_setup, utils) are imported after
bootstrapping sys.path.

Exit codes:
    0 鈥� success
    1 鈥� processing error
    2 鈥� validation failure (GLB is written but material check failed)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    """Parse arguments that appear **after** the ``--`` separator."""
    # Blender passes everything after '--' in sys.argv; find that boundary.
    try:
        sep = sys.argv.index("--")
        custom_args = sys.argv[sep + 1 :]
    except ValueError:
        custom_args = []

    parser = argparse.ArgumentParser(
        description="Process a 3D asset: import, apply PBR textures, render, export GLB.",
    )
    parser.add_argument("--model", default=None, help="Path to the model file (.obj or .fbx).")
    parser.add_argument("--obj", default=None, dest="model", help=argparse.SUPPRESS)
    parser.add_argument(
        "--models-json",
        default=None,
        help=(
            "JSON array for multi-model group mode. "
            'Each entry: {"path", "material_name", "textures": [...]}.'
        ),
    )
    parser.add_argument(
        "--textures-json",
        required=True,
        help=(
            "JSON string or @filepath describing matched textures. "
            'Each entry: {"channel", "path", "color_space", "is_glossiness"}.'
        ),
    )
    parser.add_argument("--output-dir", required=True, help="Directory for all outputs.")
    parser.add_argument("--model-name", default="asset", help="Base name for output files.")

    # Render settings (overridable)
    parser.add_argument("--render-engine", default="CYCLES")
    parser.add_argument("--render-width", type=int, default=1920)
    parser.add_argument("--render-height", type=int, default=1080)
    parser.add_argument("--render-samples", type=int, default=128)
    parser.add_argument("--denoise", action="store_true", default=True)
    parser.add_argument("--no-denoise", action="store_false", dest="denoise")
    parser.add_argument("--film-transparent", action="store_true", default=True)
    parser.add_argument("--gpu", action="store_true", default=True)
    parser.add_argument("--no-gpu", action="store_false", dest="gpu")

    # Validation
    parser.add_argument("--skip-validation", action="store_true", default=False)

    # Validate-only mode (used by core/validator.py)
    parser.add_argument(
        "--validate-only",
        metavar="GLB_PATH",
        default=None,
        help="Skip processing; only validate the given GLB file.",
    )

    args = parser.parse_args(custom_args)
    if not args.validate_only and not args.model and not args.models_json:
        parser.error("--model (or --models-json) is required")
    return args


def _load_textures_json(raw: str) -> list[dict]:
    """Parse the textures JSON (inline string or @filepath reference)."""
    if raw.startswith("@"):
        path = Path(raw[1:])
        raw = path.read_text(encoding="utf-8")
    return json.loads(raw)


_METAL_COLOR_HINTS: dict[str, tuple[float, float, float]] = {
    "gold":   (1.0, 0.766, 0.336),
    "yellow": (1.0, 0.766, 0.336),
    "silver": (0.972, 0.960, 0.915),
    "white":  (0.972, 0.960, 0.915),
    "copper": (0.955, 0.637, 0.538),
    "bronze": (0.804, 0.584, 0.257),
    "rose":   (0.942, 0.640, 0.540),
    "iron":   (0.560, 0.570, 0.580),
    "steel":  (0.630, 0.620, 0.600),
}


def _fix_broken_image_links(log: logging.Logger) -> None:
    """Disconnect image texture nodes whose images failed to load.

    After importing a model, some materials may reference external texture
    files that don't exist on this machine.  These nodes produce black output
    which makes the model look wrong.  Disconnecting them lets the Principled
    BSDF default values (Base Color, etc.) show through instead.

    For metallic materials, a fallback Base Color is inferred from the
    material name (e.g. "Yellow Gold" → gold color) so mirrors don't
    render as solid black.
    """
    import bpy  # type: ignore[import-unresolved]

    fixed = 0
    for mat in bpy.data.materials:
        if not mat.use_nodes or mat.node_tree is None:
            continue
        for node in list(mat.node_tree.nodes):
            if node.type != "TEX_IMAGE":
                continue
            img = node.image
            if img is None:
                continue
            # Image is broken if it has zero dimensions (failed to load)
            if img.size[0] == 0 and img.size[1] == 0:
                # Track which BSDF inputs were connected
                connected_inputs: list[str] = []
                for output in node.outputs:
                    for link in list(output.links):
                        if link.to_node.type == "BSDF_PRINCIPLED":
                            connected_inputs.append(link.to_socket.name)
                        mat.node_tree.links.remove(link)

                # Set fallback Base Color for metallic materials
                if "Base Color" in connected_inputs:
                    bsdf_nodes = [
                        n for n in mat.node_tree.nodes
                        if n.type == "BSDF_PRINCIPLED"
                    ]
                    for bsdf in bsdf_nodes:
                        metallic_val = bsdf.inputs.get("Metallic")
                        is_metallic = (
                            metallic_val is not None
                            and not metallic_val.links
                            and metallic_val.default_value > 0.5
                        )
                        if is_metallic:
                            color = _infer_metal_color(mat.name)
                            bc = bsdf.inputs.get("Base Color")
                            if bc is not None:
                                bc.default_value = (*color, 1.0)
                            # Bump roughness so it's not a perfect mirror
                            rough = bsdf.inputs.get("Roughness")
                            if rough and not rough.links and rough.default_value < 0.15:
                                rough.default_value = 0.25
                            log.info(
                                "  Set fallback Base Color (%.2f,%.2f,%.2f) "
                                "for metallic material '%s'.",
                                *color, mat.name,
                            )

                log.info(
                    "  Fixed broken image '%s' in material '%s'.",
                    img.name, mat.name,
                )
                fixed += 1
    if fixed:
        log.info("  Disconnected %d broken image texture(s).", fixed)


def _infer_metal_color(material_name: str) -> tuple[float, float, float]:
    """Guess a metal Base Color from the material name.

    Matches the keyword appearing earliest in the name so that
    "White Gold" matches "white" (silver) rather than "gold".
    """
    name_lower = material_name.lower()
    best_pos = len(name_lower)
    best_color = (0.8, 0.8, 0.8)
    for keyword, color in _METAL_COLOR_HINTS.items():
        pos = name_lower.find(keyword)
        if pos != -1 and pos < best_pos:
            best_pos = pos
            best_color = color
    return best_color


def main() -> int:
    """Run the full processing pipeline and return an exit code."""
    args = _parse_args()

    # --- Bootstrap sibling imports ---
    script_dir = str(Path(__file__).resolve().parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    from utils import (  # type: ignore[import-not-found]
        clean_scene,
        export_glb,
        get_scene_bbox,
        import_model,
        setup_blender_logging,
        validate_glb,
    )
    from material_builder import build_material  # type: ignore[import-not-found]
    from scene_setup import render_preview, setup_scene  # type: ignore[import-not-found]

    setup_blender_logging()
    log = logging.getLogger("blender_scripts.process_asset")

    # ------------------------------------------------------------------
    # Validate-only mode
    # ------------------------------------------------------------------
    if args.validate_only:
        log.info("=== Validate-only mode: '%s' ===", args.validate_only)
        errors = validate_glb(args.validate_only)
        if errors:
            result = {"status": "fail", "errors": errors}
            print(json.dumps(result))
            return 2
        result = {"status": "pass", "errors": []}
        print(json.dumps(result))
        return 0

    # ------------------------------------------------------------------
    # Full processing pipeline
    # ------------------------------------------------------------------
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    render_path = str(output_dir / f"{args.model_name}_preview.png")
    glb_path = str(output_dir / f"{args.model_name}.glb")

    # ------------------------------------------------------------------
    # Multi-model group mode
    # ------------------------------------------------------------------
    if args.models_json:
        try:
            models_data = json.loads(args.models_json)
        except Exception as exc:
            log.error("Failed to parse --models-json: %s", exc)
            return 1
        return _run_group_pipeline(args, models_data,
                                   clean_scene, import_model,
                                   build_material, get_scene_bbox,
                                   setup_scene, render_preview,
                                   export_glb)

    # ------------------------------------------------------------------
    # Single-model mode
    # ------------------------------------------------------------------
    try:
        textures = _load_textures_json(args.textures_json)
    except Exception as exc:
        log.error("Failed to parse textures JSON: %s", exc)
        return 1

    try:
        # 1. Clean scene
        log.info("=== Step 1/6: Clean scene ===")
        clean_scene()

        # 2. Import model
        log.info("=== Step 2/6: Import model '%s' ===", args.model)
        mesh_objects = import_model(args.model)

        # 2b. Fix broken image texture links (missing external files)
        _fix_broken_image_links(log)

        # 3. Build material (or keep imported MTL materials if no textures)
        has_opacity = any(t.get("channel") == "opacity" for t in textures)
        if textures:
            log.info("=== Step 3/6: Build PBR material (%d textures) ===", len(textures))
            build_material(mesh_objects, textures, material_name=args.model_name)
        else:
            log.info("=== Step 3/6: No textures provided; keeping imported materials ===")

        # 4. Setup scene (lights, camera, render settings)
        log.info("=== Step 4/6: Setup scene ===")
        center, _, _, diagonal = get_scene_bbox(mesh_objects)

        # Disable transparent film when model uses opacity to avoid
        # invisible renders (transparent model on transparent background).
        film_transparent = args.film_transparent
        if has_opacity and film_transparent:
            film_transparent = False
            log.info("  Opacity texture detected; disabling transparent film.")

        setup_scene(
            center,
            diagonal,
            engine=args.render_engine,
            resolution=(args.render_width, args.render_height),
            samples=args.render_samples,
            denoise=args.denoise,
            film_transparent=film_transparent,
            gpu_enabled=args.gpu,
        )

        # 5. Render preview
        log.info("=== Step 5/6: Render preview ===")
        render_preview(render_path)

        # 6. Export GLB
        log.info("=== Step 6/6: Export GLB ===")
        export_glb(glb_path)

    except Exception as exc:
        log.error("Pipeline failed: %s", exc, exc_info=True)
        return 1

    # ------------------------------------------------------------------
    # Step 7: Re-import GLB → render validation preview + validate
    # ------------------------------------------------------------------
    glb_preview_path = str(output_dir / f"{args.model_name}_glb_preview.png")

    log.info("=== Step 7/7: GLB re-import preview + validation ===")
    glb_preview_rendered, errors = _render_and_validate_glb(
        glb_path=glb_path,
        preview_path=glb_preview_path,
        engine=args.render_engine,
        resolution=(args.render_width, args.render_height),
        samples=args.render_samples,
        denoise=args.denoise,
        film_transparent=args.film_transparent,
        gpu_enabled=args.gpu,
        skip_validation=args.skip_validation,
        render_preview_fn=render_preview,
        setup_scene_fn=setup_scene,
        get_scene_bbox_fn=get_scene_bbox,
    )

    result_base = {
        "glb": glb_path,
        "preview": render_path,
        "glb_preview": glb_preview_path if glb_preview_rendered else None,
    }

    if errors:
        result = {**result_base, "status": "fail", "errors": errors}
        print(json.dumps(result))
        return 2

    result = {**result_base, "status": "pass", "errors": []}
    print(json.dumps(result))
    log.info("=== Done ===")
    return 0


def _render_and_validate_glb(
    glb_path: str,
    preview_path: str,
    *,
    engine: str,
    resolution: tuple[int, int],
    samples: int,
    denoise: bool,
    film_transparent: bool,
    gpu_enabled: bool,
    skip_validation: bool,
    render_preview_fn,
    setup_scene_fn,
    get_scene_bbox_fn,
) -> tuple[bool, list[str]]:
    """Re-import the exported GLB, render a preview, then validate materials.

    Combines the re-import render and validation in a single scene so we
    only pay the import cost once.

    Returns:
        (rendered, errors) — rendered is True if the preview was written.
    """
    import bpy  # type: ignore[import-unresolved]

    log = logging.getLogger("blender_scripts.process_asset")
    errors: list[str] = []
    rendered = False

    bpy.ops.wm.read_factory_settings(use_empty=True)
    try:
        bpy.ops.import_scene.gltf(filepath=glb_path)
    except Exception as exc:
        errors.append(f"Failed to re-import GLB: {exc}")
        return rendered, errors

    imported_meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    if not imported_meshes:
        errors.append("No mesh objects found after re-importing GLB.")
        return rendered, errors

    # Render re-import preview
    try:
        center, _, _, diagonal = get_scene_bbox_fn(imported_meshes)
        setup_scene_fn(
            center,
            diagonal,
            engine=engine,
            resolution=resolution,
            samples=samples,
            denoise=denoise,
            film_transparent=film_transparent,
            gpu_enabled=gpu_enabled,
        )
        render_preview_fn(preview_path)
        rendered = True
        log.info("GLB re-import preview rendered -> '%s'.", preview_path)
    except Exception as exc:
        log.warning("GLB re-import render failed: %s", exc)

    # Material validation
    if not skip_validation:
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

    return rendered, errors


def _run_group_pipeline(args, models_data,
                        clean_scene, import_model,
                        build_material, get_scene_bbox,
                        setup_scene, render_preview,
                        export_glb) -> int:
    """Import multiple models into one scene and export as a single GLB."""
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    render_path = str(output_dir / f"{args.model_name}_preview.png")
    glb_path = str(output_dir / f"{args.model_name}.glb")

    log = logging.getLogger("blender_scripts.process_asset")

    try:
        log.info("=== Group Step 1: Clean scene ===")
        clean_scene()

        all_mesh_objects = []
        has_opacity = False

        for i, entry in enumerate(models_data, 1):
            model_path = entry["path"]
            material_name = entry.get("material_name", Path(model_path).stem)
            textures = entry.get("textures", [])

            log.info("=== Group Step 2.%d: Import '%s' ===", i, model_path)
            mesh_objects = import_model(model_path)
            _fix_broken_image_links(log)

            if textures:
                log.info("  Building material '%s' (%d textures).", material_name, len(textures))
                build_material(mesh_objects, textures, material_name=material_name)
                if any(t.get("channel") == "opacity" for t in textures):
                    has_opacity = True
            else:
                log.info("  No textures for '%s'; keeping imported materials.", material_name)

            all_mesh_objects.extend(mesh_objects)

        log.info("=== Group Step 3: Setup scene (%d objects) ===", len(all_mesh_objects))
        center, _, _, diagonal = get_scene_bbox(all_mesh_objects)

        film_transparent = args.film_transparent
        if has_opacity and film_transparent:
            film_transparent = False
            log.info("  Opacity detected; disabling transparent film.")

        setup_scene(
            center, diagonal,
            engine=args.render_engine,
            resolution=(args.render_width, args.render_height),
            samples=args.render_samples,
            denoise=args.denoise,
            film_transparent=film_transparent,
            gpu_enabled=args.gpu,
        )

        log.info("=== Group Step 4: Render preview ===")
        render_preview(render_path)

        log.info("=== Group Step 5: Export GLB ===")
        export_glb(glb_path)

    except Exception as exc:
        log.error("Group pipeline failed: %s", exc, exc_info=True)
        return 1

    # Re-import preview + validation
    glb_preview_path = str(output_dir / f"{args.model_name}_glb_preview.png")
    log.info("=== Group Step 6: GLB re-import preview + validation ===")

    from scene_setup import setup_scene as _ss, render_preview as _rp  # type: ignore
    from utils import get_scene_bbox as _bbox  # type: ignore

    glb_preview_rendered, errors = _render_and_validate_glb(
        glb_path=glb_path,
        preview_path=glb_preview_path,
        engine=args.render_engine,
        resolution=(args.render_width, args.render_height),
        samples=args.render_samples,
        denoise=args.denoise,
        film_transparent=args.film_transparent,
        gpu_enabled=args.gpu,
        skip_validation=args.skip_validation,
        render_preview_fn=_rp,
        setup_scene_fn=_ss,
        get_scene_bbox_fn=_bbox,
    )

    result_base = {
        "glb": glb_path,
        "preview": render_path,
        "glb_preview": glb_preview_path if glb_preview_rendered else None,
    }

    if errors:
        result = {**result_base, "status": "fail", "errors": errors}
        print(json.dumps(result))
        return 2

    result = {**result_base, "status": "pass", "errors": []}
    print(json.dumps(result))
    log.info("=== Group Done ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
