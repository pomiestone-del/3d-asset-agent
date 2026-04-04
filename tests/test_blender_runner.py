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

        call_kwargs = mock_run.call_args[1]
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

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs.get("errors") == "replace"
