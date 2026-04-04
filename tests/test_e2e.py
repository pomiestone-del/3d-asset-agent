"""End-to-end tests that exercise the full pipeline through Blender.

These tests require a working Blender installation and are automatically
skipped if Blender is not available.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from conftest import BLENDER_EXE, FIXTURES_DIR, TEXTURES_DIR, requires_blender

from asset_agent.agent import AssetAgent, ProcessingResult
from asset_agent.core.blender_runner import run_blender_script, run_process_asset
from asset_agent.core.texture_matcher import create_matcher
from asset_agent.exporters.glb_exporter import build_textures_payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROCESS_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "asset_agent"
    / "blender_scripts"
    / "process_asset.py"
)


def _build_textures_payload() -> list[dict[str, Any]]:
    """Match the fixture textures and convert to JSON payload."""
    matcher = create_matcher(model_name="Cube")
    texture_map = matcher.match(TEXTURES_DIR)
    return build_textures_payload(texture_map.as_dict())


# ---------------------------------------------------------------------------
# Low-level: Blender script execution
# ---------------------------------------------------------------------------

@requires_blender
class TestBlenderScriptExecution:
    """Verify that Blender can run our scripts at all."""

    def test_blender_runs_process_script_help(self, blender_exe: str) -> None:
        """The process_asset.py script should at least parse --help without crashing."""
        stdout = run_blender_script(
            _PROCESS_SCRIPT,
            ["--help"],
            blender_path=blender_exe,
            timeout=60,
        )
        assert "process" in stdout.lower() or "usage" in stdout.lower() or "optional" in stdout.lower()


# ---------------------------------------------------------------------------
# Full pipeline via run_process_asset
# ---------------------------------------------------------------------------

@requires_blender
class TestFullPipeline:
    """Run the complete pipeline: import OBJ, build material, render, export GLB."""

    def test_process_cube_produces_glb_and_preview(
        self, tmp_path: Path, blender_exe: str
    ) -> None:
        textures_payload = _build_textures_payload()

        result = run_process_asset(
            obj_path=FIXTURES_DIR / "cube.obj",
            textures_json=textures_payload,
            output_dir=tmp_path,
            model_name="Cube",
            blender_path=blender_exe,
            render_engine="CYCLES",
            render_width=320,
            render_height=240,
            render_samples=16,
            denoise=False,
            film_transparent=True,
            gpu_enabled=False,
            skip_validation=False,
            timeout=120,
        )

        assert result["status"] == "pass", f"Pipeline failed: {result.get('errors')}"

        glb = Path(result["glb"])
        preview = Path(result["preview"])

        assert glb.exists(), f"GLB not found at {glb}"
        assert glb.stat().st_size > 100, "GLB file is suspiciously small"

        assert preview.exists(), f"Preview not found at {preview}"
        assert preview.stat().st_size > 100, "Preview PNG is suspiciously small"

    def test_process_cube_skip_validation(
        self, tmp_path: Path, blender_exe: str
    ) -> None:
        textures_payload = _build_textures_payload()

        result = run_process_asset(
            obj_path=FIXTURES_DIR / "cube.obj",
            textures_json=textures_payload,
            output_dir=tmp_path,
            model_name="CubeNoVal",
            blender_path=blender_exe,
            render_engine="CYCLES",
            render_width=160,
            render_height=120,
            render_samples=4,
            denoise=False,
            gpu_enabled=False,
            skip_validation=True,
            timeout=120,
        )

        assert result["status"] == "pass"
        assert Path(result["glb"]).exists()


# ---------------------------------------------------------------------------
# High-level: AssetAgent.process()
# ---------------------------------------------------------------------------

@requires_blender
class TestAssetAgentProcess:
    """Test the top-level AssetAgent orchestrator."""

    def test_agent_process_returns_success(self, tmp_path: Path) -> None:
        agent = AssetAgent()

        # Override to low-quality for speed
        agent.config.render.resolution = [160, 120]
        agent.config.render.samples = 4
        agent.config.render.denoise = False
        agent.config.render.gpu_enabled = False

        result = agent.process(
            obj_path=FIXTURES_DIR / "cube.obj",
            texture_dir=TEXTURES_DIR,
            output_dir=tmp_path,
            model_name="CubeAgent",
        )

        assert isinstance(result, ProcessingResult)
        assert result.success, f"Agent failed: {result.errors}"
        assert result.glb_path is not None
        assert result.glb_path.exists()
        assert result.preview_path is not None
        assert result.preview_path.exists()
        assert result.texture_map is not None
        assert result.texture_map.albedo is not None

    def test_agent_process_missing_obj_raises(self, tmp_path: Path) -> None:
        agent = AssetAgent()
        with pytest.raises(Exception):
            agent.process(
                obj_path=Path("nonexistent.obj"),
                texture_dir=TEXTURES_DIR,
                output_dir=tmp_path,
            )


# ---------------------------------------------------------------------------
# Standalone validation via AssetAgent.validate()
# ---------------------------------------------------------------------------

@requires_blender
class TestAssetAgentValidate:
    """Test GLB validation through the agent."""

    def test_validate_good_glb(self, tmp_path: Path) -> None:
        """First produce a GLB, then validate it independently."""
        agent = AssetAgent()
        agent.config.render.resolution = [160, 120]
        agent.config.render.samples = 4
        agent.config.render.denoise = False
        agent.config.render.gpu_enabled = False

        proc = agent.process(
            obj_path=FIXTURES_DIR / "cube.obj",
            texture_dir=TEXTURES_DIR,
            output_dir=tmp_path,
            model_name="ValCube",
        )
        assert proc.success

        val = agent.validate(proc.glb_path)
        assert val.passed, f"Validation failed: {val.errors}"

    def test_validate_nonexistent_glb(self) -> None:
        agent = AssetAgent()
        val = agent.validate(Path("does_not_exist.glb"))
        assert not val.passed
        assert any("does not exist" in e for e in val.errors)
