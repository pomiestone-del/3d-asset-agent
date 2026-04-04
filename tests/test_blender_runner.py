"""Unit tests for blender_runner subprocess management."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from asset_agent.core.blender_runner import run_blender_script


def test_subprocess_uses_utf8_encoding(tmp_path):
    """subprocess.run must specify encoding=utf-8 to avoid GBK decode errors on Windows."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = '{"status": "pass"}'
    mock_result.stderr = ""

    with patch("asset_agent.core.blender_runner.subprocess.run", return_value=mock_result) as mock_run:
        with patch("asset_agent.core.blender_runner.find_blender", return_value="blender"):
            run_blender_script(tmp_path / "dummy.py", [], blender_path="blender")

        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs.get("encoding") == "utf-8", "encoding must be utf-8"
        assert call_kwargs.get("errors") == "replace", "errors must be replace"


def test_subprocess_uses_errors_replace(tmp_path):
    """errors='replace' must be set so non-UTF-8 bytes in Blender stderr don't crash."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    mock_result.stderr = ""

    with patch("asset_agent.core.blender_runner.subprocess.run", return_value=mock_result) as mock_run:
        with patch("asset_agent.core.blender_runner.find_blender", return_value="blender"):
            run_blender_script(tmp_path / "dummy.py", [], blender_path="blender")

        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs.get("errors") == "replace"


def test_skip_validation_when_no_textures(tmp_path):
    """When no albedo is found (empty textures_payload), validation must be skipped."""
    from asset_agent.agent import AssetAgent
    from asset_agent.exceptions import MissingAlbedoError

    agent = AssetAgent()
    agent.config.render.resolution = [160, 120]
    agent.config.render.samples = 4
    agent.config.render.gpu_enabled = False

    obj_path = tmp_path / "model.obj"
    obj_path.write_text("# empty\nv 0 0 0\n")

    captured: dict = {}

    def fake_run_process(**kwargs):
        captured.update(kwargs)
        glb = tmp_path / "model.glb"
        glb.write_bytes(b"dummy")
        preview = tmp_path / "model_preview.png"
        preview.write_bytes(b"dummy")
        return {"status": "pass", "glb": str(glb), "preview": str(preview)}

    with patch("asset_agent.agent.run_process_asset", side_effect=fake_run_process):
        with patch.object(agent._obj_importer, "validate_file"):
            with patch.object(agent, "match_textures", side_effect=MissingAlbedoError(".")):
                agent.process(obj_path, tmp_path, tmp_path)

    assert captured.get("skip_validation") is True, (
        "skip_validation must be True when no textures are provided"
    )
