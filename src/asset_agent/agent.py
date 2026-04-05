"""Main orchestration 閳ワ拷 wires together texture matching, Blender processing,
and GLB validation into a single high-level API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from asset_agent.core.blender_runner import run_process_asset
from asset_agent.core.texture_matcher import TextureMap, create_matcher
from asset_agent.core.validator import ValidationResult
from asset_agent.core.validator import validate_glb as _validate_glb
from asset_agent.exceptions import MissingAlbedoError
from asset_agent.exporters.glb_exporter import build_textures_payload
from asset_agent.utils.config import AppConfig, load_config
from asset_agent.utils.file_utils import ensure_directory
from asset_agent.utils.logging import get_logger, setup_logging

logger = get_logger("agent")


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class ProcessingResult:
    """Outcome of a full asset-processing run."""

    success: bool
    glb_path: Path | None = None
    preview_path: Path | None = None
    texture_map: TextureMap | None = None
    validation: ValidationResult | None = None
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class AssetAgent:
    """High-level orchestrator for the 3D asset processing pipeline.

    Usage::

        agent = AssetAgent()                     # or AssetAgent(config_path=...)
        result = agent.process(obj, textures, output)
        print(result.glb_path, result.preview_path)

    Args:
        config_path: Optional override YAML merged on top of ``config/default.yaml``.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        self.config: AppConfig = load_config(config_path)
        setup_logging(self.config.logging.level)

    @staticmethod
    def _get_importer(model_path: Path):
        """Return the appropriate importer based on file extension."""
        from asset_agent.importers.fbx_importer import FbxImporter
        from asset_agent.importers.obj_importer import ObjImporter
        ext = model_path.suffix.lower()
        if ext == ".fbx":
            return FbxImporter()
        return ObjImporter()

    # -- Full pipeline ------------------------------------------------------

    def process(
        self,
        obj_path: Path,
        texture_dir: Path,
        output_dir: Path,
        *,
        model_name: str | None = None,
    ) -> ProcessingResult:
        """Run the complete processing pipeline.

        Args:
            obj_path: Path to the ``.obj`` white-model file.
            texture_dir: Directory containing PBR texture images.
            output_dir: Where to write GLB and preview PNG.
            model_name: Basename for outputs. Defaults to the OBJ stem.

        Returns:
            ``ProcessingResult`` summarizing success, output paths, and errors.
        """
        if model_name is None:
            model_name = obj_path.stem

        logger.info("=== Asset Agent: processing '%s' ===", model_name)

        # 1. Validate input file
        logger.info("[1/4] Validating input file...")
        importer = self._get_importer(obj_path)
        importer.validate_file(obj_path)

        # 2. Match textures
        logger.info("[2/4] Matching textures in '%s'...", texture_dir)
        from asset_agent.core.mtl_parser import find_mtl_for_obj, parse_mtl
        from asset_agent.exporters.glb_exporter import build_multi_textures_payload

        mtl_path = find_mtl_for_obj(obj_path)
        mtl_data = parse_mtl(mtl_path) if mtl_path else {}
        # Only count materials that have actual texture declarations;
        # color-only materials (Kd/Ks/Ns only) should not trigger multi-material mode.
        materials_with_textures = {k: v for k, v in mtl_data.items() if v}
        is_multi = len(materials_with_textures) > 1

        texture_map = None  # may remain None for multi-material models

        if is_multi:
            material_names = list(mtl_data.keys())
            logger.info(
                "  Multi-material OBJ detected: %d materials (%s)",
                len(material_names),
                ", ".join(material_names),
            )
            matcher = create_matcher(model_name=model_name)
            material_maps = matcher.match_multi(
                texture_dir,
                material_names=material_names,
                obj_path=obj_path,
            )
            matched_count = sum(1 for tm in material_maps.values() if tm.albedo is not None)
            logger.info("  Matched %d/%d materials with albedo.", matched_count, len(material_names))
            textures_payload = build_multi_textures_payload(material_maps)
        else:
            try:
                texture_map = self.match_textures(texture_dir, model_name=model_name, obj_path=obj_path)
                logger.info(
                    "  Matched channels: %s",
                    ", ".join(texture_map.channel_names) or "(none)",
                )
            except MissingAlbedoError:
                logger.warning("  No albedo texture found — will keep imported MTL materials.")
                texture_map = TextureMap()
            textures_payload = build_textures_payload(texture_map.as_dict())

        # 3. Run Blender pipeline
        logger.info("[3/4] Running Blender pipeline...")
        ensure_directory(output_dir)

        cfg = self.config
        blender_result = run_process_asset(
            obj_path=obj_path,
            textures_json=textures_payload,
            output_dir=output_dir,
            model_name=model_name,
            blender_path=cfg.blender.executable,
            render_engine=cfg.render.engine,
            render_width=cfg.render.resolution[0],
            render_height=cfg.render.resolution[1],
            render_samples=cfg.render.samples,
            denoise=cfg.render.denoise,
            film_transparent=cfg.render.film_transparent,
            gpu_enabled=cfg.render.gpu_enabled,
            skip_validation=(not cfg.validation.enabled) or (not textures_payload),
        )

        status = blender_result.get("status", "unknown")
        errors = blender_result.get("errors", [])
        glb_path = Path(blender_result["glb"]) if "glb" in blender_result else None
        preview_path = Path(blender_result["preview"]) if "preview" in blender_result else None

        # 4. Summarize
        validation_result = ValidationResult(
            passed=(status == "pass"),
            errors=errors,
        )

        success = status == "pass"
        if success:
            logger.info("[4/4] Pipeline completed successfully.")
        else:
            logger.warning("[4/4] Pipeline finished with issues: %s", errors)

        return ProcessingResult(
            success=success,
            glb_path=glb_path,
            preview_path=preview_path,
            texture_map=texture_map,
            validation=validation_result,
            errors=errors,
        )

    # -- Texture matching (standalone) --------------------------------------

    def match_textures(
        self,
        texture_dir: Path,
        *,
        model_name: str | None = None,
        obj_path: Path | None = None,
    ) -> TextureMap:
        """Scan a directory and classify textures into PBR channels.

        Args:
            texture_dir: Folder containing texture images.
            model_name: Optional model-name hint for disambiguation.
            obj_path: Optional path to the ``.obj`` file used to locate
                      the MTL for explicit texture declarations.

        Returns:
            ``TextureMap`` with matched channels.
        """
        matcher = create_matcher(model_name=model_name)
        return matcher.match(texture_dir, obj_path=obj_path)

    # -- Batch processing ---------------------------------------------------

    @staticmethod
    def _discover_texture_dir(model_path: Path) -> Path:
        """Heuristic to find the texture directory for a model file.

        Checks (in order):
        1. A sibling ``textures/`` subdirectory
        2. The model file's parent directory itself
        """
        textures_subdir = model_path.parent / "textures"
        if textures_subdir.is_dir():
            return textures_subdir
        return model_path.parent

    def batch_process(
        self,
        input_dir: Path,
        output_dir: Path,
        *,
        extensions: tuple[str, ...] = (".obj", ".fbx"),
    ) -> list[ProcessingResult]:
        """Discover and process all model files under *input_dir*.

        For each model found, textures are resolved via
        ``_discover_texture_dir`` and outputs go to a per-model subfolder
        under *output_dir*.

        Args:
            input_dir: Root directory to scan (recursive).
            output_dir: Base output directory.
            extensions: File extensions to consider as models.

        Returns:
            List of ``ProcessingResult`` — one per model file found.
        """
        model_files = sorted(
            p for p in input_dir.rglob("*")
            if p.suffix.lower() in extensions and p.is_file()
        )

        if not model_files:
            logger.warning("No model files found in '%s'", input_dir)
            return []

        logger.info("Batch: found %d model(s) in '%s'", len(model_files), input_dir)
        results: list[ProcessingResult] = []

        for model_path in model_files:
            name = model_path.stem
            texture_dir = self._discover_texture_dir(model_path)
            model_output = output_dir / name
            logger.info("--- Batch item: %s ---", name)

            try:
                result = self.process(
                    obj_path=model_path,
                    texture_dir=texture_dir,
                    output_dir=model_output,
                    model_name=name,
                )
            except Exception as exc:
                logger.error("Batch item '%s' failed: %s", name, exc)
                result = ProcessingResult(success=False, errors=[str(exc)])

            results.append(result)

        passed = sum(1 for r in results if r.success)
        logger.info("Batch complete: %d/%d succeeded", passed, len(results))
        return results

    # -- GLB validation (standalone) ----------------------------------------

    def validate(self, glb_path: Path) -> ValidationResult:
        """Validate a GLB file by re-importing in a clean Blender scene.

        Args:
            glb_path: Path to the ``.glb`` file.

        Returns:
            ``ValidationResult``.
        """
        return _validate_glb(
            glb_path,
            blender_path=self.config.blender.executable,
        )
