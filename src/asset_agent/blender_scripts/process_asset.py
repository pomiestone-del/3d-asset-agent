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
    if not args.validate_only and not args.model:
        parser.error("--model (or --obj) is required")
    return args


def _load_textures_json(raw: str) -> list[dict]:
    """Parse the textures JSON (inline string or @filepath reference)."""
    if raw.startswith("@"):
        path = Path(raw[1:])
        raw = path.read_text(encoding="utf-8")
    return json.loads(raw)


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

        # 3. Build material (or keep imported MTL materials if no textures)
        if textures:
            log.info("=== Step 3/6: Build PBR material (%d textures) ===", len(textures))
            build_material(mesh_objects, textures, material_name=args.model_name)
        else:
            log.info("=== Step 3/6: No textures provided; keeping imported materials ===")

        # 4. Setup scene (lights, camera, render settings)
        log.info("=== Step 4/6: Setup scene ===")
        center, _, _, diagonal = get_scene_bbox(mesh_objects)
        setup_scene(
            center,
            diagonal,
            engine=args.render_engine,
            resolution=(args.render_width, args.render_height),
            samples=args.render_samples,
            denoise=args.denoise,
            film_transparent=args.film_transparent,
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
    # Optional validation
    # ------------------------------------------------------------------
    if not args.skip_validation:
        log.info("=== Post-export validation ===")
        errors = validate_glb(glb_path)
        if errors:
            result = {"status": "fail", "errors": errors, "glb": glb_path, "preview": render_path}
            print(json.dumps(result))
            return 2
    else:
        errors = []

    result = {"status": "pass", "errors": [], "glb": glb_path, "preview": render_path}
    print(json.dumps(result))
    log.info("=== Done ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
