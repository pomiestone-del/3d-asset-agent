"""Unit tests for blender_runner subprocess management."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from asset_agent.core.blender_runner import run_blender_script


def _make_mock_popen(stdout_lines=None, stderr_lines=None, returncode=0):
    """Create a mock Popen that simulates line-by-line output."""
    mock_proc = MagicMock()
    mock_proc.returncode = returncode
    mock_proc.stdout = iter(stdout_lines or [])
    mock_proc.stderr = iter(stderr_lines or [])
    mock_proc.wait.return_value = returncode
    mock_proc.kill = MagicMock()
    return mock_proc


def test_popen_uses_utf8_encoding(tmp_path):
    """Popen must specify encoding=utf-8 to avoid GBK decode errors on Windows."""
    mock_proc = _make_mock_popen(stdout_lines=['{"status": "pass"}\n'])

    with patch("asset_agent.core.blender_runner.subprocess.Popen", return_value=mock_proc) as mock_popen:
        with patch("asset_agent.core.blender_runner.find_blender", return_value="blender"):
            run_blender_script(tmp_path / "dummy.py", [], blender_path="blender")

        call_kwargs = mock_popen.call_args.kwargs
        assert call_kwargs.get("encoding") == "utf-8", "encoding must be utf-8"
        assert call_kwargs.get("errors") == "replace", "errors must be replace"


def test_popen_uses_errors_replace(tmp_path):
    """errors='replace' must be set so non-UTF-8 bytes in Blender stderr don't crash."""
    mock_proc = _make_mock_popen()

    with patch("asset_agent.core.blender_runner.subprocess.Popen", return_value=mock_proc) as mock_popen:
        with patch("asset_agent.core.blender_runner.find_blender", return_value="blender"):
            run_blender_script(tmp_path / "dummy.py", [], blender_path="blender")

        call_kwargs = mock_popen.call_args.kwargs
        assert call_kwargs.get("errors") == "replace"


def test_stdout_captured_from_popen(tmp_path):
    """Stdout lines from Popen should be joined and returned."""
    lines = ["line1\n", "line2\n", '{"status": "pass"}\n']
    mock_proc = _make_mock_popen(stdout_lines=lines)

    with patch("asset_agent.core.blender_runner.subprocess.Popen", return_value=mock_proc):
        with patch("asset_agent.core.blender_runner.find_blender", return_value="blender"):
            result = run_blender_script(tmp_path / "dummy.py", [], blender_path="blender")

    assert "line1" in result
    assert '{"status": "pass"}' in result


def test_timeout_kills_process(tmp_path):
    """When timeout expires, process must be killed."""
    import subprocess as sp

    mock_proc = _make_mock_popen()
    mock_proc.wait.side_effect = sp.TimeoutExpired(cmd="blender", timeout=1)

    from asset_agent.exceptions import BlenderTimeoutError

    with patch("asset_agent.core.blender_runner.subprocess.Popen", return_value=mock_proc):
        with patch("asset_agent.core.blender_runner.find_blender", return_value="blender"):
            with pytest.raises(BlenderTimeoutError):
                run_blender_script(tmp_path / "dummy.py", [], blender_path="blender", timeout=1)

    mock_proc.kill.assert_called_once()


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

    mock_importer = MagicMock()
    with patch("asset_agent.agent.run_process_asset", side_effect=fake_run_process):
        with patch.object(AssetAgent, "_get_importer", return_value=mock_importer):
            with patch.object(agent, "match_textures", side_effect=MissingAlbedoError(".")):
                agent.process(obj_path, tmp_path, tmp_path)

    assert captured.get("skip_validation") is True, (
        "skip_validation must be True when no textures are provided"
    )
