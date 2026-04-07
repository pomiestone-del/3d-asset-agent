"""Tests for NormalMapConverter (ported from blender-auto-material-agent)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from asset_agent.core.normal_map_converter import (
    NormalConvertMode,
    NormalFormat,
    NormalMapConverter,
)
from asset_agent.exceptions import NormalMapConversionError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_normal_image(path: Path, g_mean_target: float = 0.8) -> Path:
    """Write a small RGBA PNG whose G-channel mean is approximately *g_mean_target*."""
    pixels = np.zeros((8, 8, 4), dtype=np.uint8)
    pixels[:, :, 0] = 128  # R
    pixels[:, :, 1] = int(g_mean_target * 255)  # G
    pixels[:, :, 2] = 255  # B
    pixels[:, :, 3] = 255  # A
    Image.fromarray(pixels, mode="RGBA").save(path)
    return path


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

def test_invert_g_channel_roundtrip():
    """Double-inversion must recover the original values."""
    converter = NormalMapConverter()
    pixels = np.random.rand(8, 8, 4).astype(np.float32)
    twice = converter.invert_g_channel(converter.invert_g_channel(pixels))
    assert np.allclose(twice, pixels)


def test_invert_g_channel_only_modifies_green():
    """Inversion must not touch R, B, or A channels."""
    converter = NormalMapConverter()
    pixels = np.ones((4, 4, 4), dtype=np.float32) * 0.5
    result = converter.invert_g_channel(pixels)
    assert np.allclose(result[:, :, 0], pixels[:, :, 0])  # R unchanged
    assert np.allclose(result[:, :, 2], pixels[:, :, 2])  # B unchanged
    assert np.allclose(result[:, :, 3], pixels[:, :, 3])  # A unchanged
    assert np.allclose(result[:, :, 1], 1.0 - pixels[:, :, 1])  # G inverted


def test_invert_g_channel_invalid_shape():
    converter = NormalMapConverter()
    with pytest.raises(NormalMapConversionError):
        converter.invert_g_channel(np.zeros((8, 8, 2), dtype=np.float32))


def test_detect_format_opengl(tmp_path):
    """High G-mean image should be identified as OpenGL."""
    src = _make_normal_image(tmp_path / "opengl_normal.png", g_mean_target=0.8)
    converter = NormalMapConverter()
    detection = converter.detect_format(str(src))
    assert detection.detected_format == NormalFormat.OPENGL
    assert detection.confidence > 0


def test_detect_format_directx(tmp_path):
    """Low G-mean image should be identified as DirectX."""
    src = _make_normal_image(tmp_path / "dx_normal.png", g_mean_target=0.2)
    converter = NormalMapConverter()
    detection = converter.detect_format(str(src))
    assert detection.detected_format == NormalFormat.DIRECTX
    assert detection.confidence > 0


def test_detect_format_missing_file():
    converter = NormalMapConverter()
    with pytest.raises(NormalMapConversionError):
        converter.detect_format("/nonexistent/normal.png")


def test_convert_same_format_no_change(tmp_path):
    """Converting from a format to itself must produce a file but set changed=False."""
    src = _make_normal_image(tmp_path / "normal.png")
    converter = NormalMapConverter()
    result = converter.convert(
        str(src), NormalFormat.OPENGL, NormalFormat.OPENGL, str(tmp_path)
    )
    assert not result.changed
    assert Path(result.output_path).exists()


def test_convert_different_formats_changes_image(tmp_path):
    """Converting between formats must modify the G-channel."""
    src = _make_normal_image(tmp_path / "normal.png", g_mean_target=0.8)
    converter = NormalMapConverter()
    result = converter.convert(
        str(src), NormalFormat.OPENGL, NormalFormat.DIRECTX, str(tmp_path)
    )
    assert result.changed
    out = Path(result.output_path)
    assert out.exists()

    original = np.asarray(Image.open(src).convert("RGBA"), dtype=np.float32) / 255.0
    converted = np.asarray(Image.open(out).convert("RGBA"), dtype=np.float32) / 255.0
    assert np.allclose(converted[:, :, 1], 1.0 - original[:, :, 1], atol=1 / 255)


def test_convert_missing_source(tmp_path):
    converter = NormalMapConverter()
    with pytest.raises(NormalMapConversionError):
        converter.convert(
            "/nonexistent/normal.png",
            NormalFormat.OPENGL,
            NormalFormat.DIRECTX,
            str(tmp_path),
        )


def test_convert_creates_output_directory(tmp_path):
    """Converter must create the output directory if it does not exist."""
    src = _make_normal_image(tmp_path / "normal.png")
    out_dir = tmp_path / "nested" / "output"
    converter = NormalMapConverter()
    result = converter.convert(
        str(src), NormalFormat.OPENGL, NormalFormat.DIRECTX, str(out_dir)
    )
    assert Path(result.output_path).exists()
