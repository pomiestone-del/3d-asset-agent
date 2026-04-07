"""Normal map format detection and conversion.

Ported from blender-auto-material-agent.  Converts between OpenGL and
DirectX normal map conventions by inverting the G-channel.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import numpy as np
from PIL import Image

from asset_agent.exceptions import NormalMapConversionError


class NormalFormat(Enum):
    DIRECTX = "directx"
    OPENGL = "opengl"


class NormalConvertMode(Enum):
    DIRECTX_TO_OPENGL = "directx-to-opengl"
    OPENGL_TO_DIRECTX = "opengl-to-directx"
    AUTO = "auto"


@dataclass
class NormalFormatDetection:
    detected_format: NormalFormat
    confidence: float
    g_channel_mean: float


@dataclass
class ConversionResult:
    output_path: str
    changed: bool


class NormalMapConverter:
    """Detects and converts between OpenGL / DirectX normal map formats.

    The only transformation is a G-channel inversion (``1.0 - G``), which
    is the sole difference between the two conventions.
    """

    def convert(
        self,
        image_path: str,
        src_format: NormalFormat,
        dst_format: NormalFormat,
        output_dir: str | None = None,
    ) -> ConversionResult:
        """Convert *image_path* from *src_format* to *dst_format*.

        Args:
            image_path: Absolute path to the source normal map.
            src_format: Format of the source image.
            dst_format: Desired output format.
            output_dir: Directory to write the result.  Defaults to the
                        source file's parent directory.

        Returns:
            ``ConversionResult`` with the output path and whether the image
            was actually modified (``changed=False`` when formats match).

        Raises:
            NormalMapConversionError: If the source file does not exist or
                                      the pixel array has an unexpected shape.
        """
        src = Path(image_path)
        if not src.exists():
            raise NormalMapConversionError(f"Normal map does not exist: {image_path}")

        out_dir = Path(output_dir) if output_dir else src.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{src.stem}_converted{src.suffix}"

        if src_format == dst_format:
            img = Image.open(src).convert("RGBA")
            img.save(out)
            return ConversionResult(output_path=str(out), changed=False)

        img = Image.open(src).convert("RGBA")
        pixels = np.asarray(img, dtype=np.float32) / 255.0
        converted = self.invert_g_channel(pixels)
        out_u8 = np.clip(converted * 255.0, 0, 255).astype(np.uint8)
        Image.fromarray(out_u8, mode="RGBA").save(out)
        return ConversionResult(output_path=str(out), changed=True)

    def invert_g_channel(self, pixels: np.ndarray) -> np.ndarray:
        """Return a copy of *pixels* with the G-channel inverted.

        Args:
            pixels: Float32 array of shape ``(H, W, C)`` with ``C >= 3``,
                    values in ``[0, 1]``.

        Raises:
            NormalMapConversionError: If the array shape is invalid.
        """
        if pixels.ndim != 3 or pixels.shape[-1] < 3:
            raise NormalMapConversionError(
                "Expected HxWxC pixel array with at least 3 channels."
            )
        result = pixels.copy()
        result[:, :, 1] = 1.0 - pixels[:, :, 1]
        return result

    def detect_format(self, image_path: str) -> NormalFormatDetection:
        """Heuristically detect whether *image_path* is an OpenGL or DirectX map.

        Strategy: compute the mean of the G-channel.

        * G-mean > 0.5  →  OpenGL  (bright greens dominate)
        * G-mean ≤ 0.5  →  DirectX (dark greens dominate)

        Confidence is the normalised distance from the 0.5 threshold, in
        the range ``[0, 1]``.

        Args:
            image_path: Absolute path to the normal map image.

        Returns:
            ``NormalFormatDetection`` with the detected format, confidence,
            and raw G-channel mean.

        Raises:
            NormalMapConversionError: If the file does not exist.
        """
        src = Path(image_path)
        if not src.exists():
            raise NormalMapConversionError(f"Normal map does not exist: {image_path}")

        img = Image.open(src).convert("RGBA")
        pixels = np.asarray(img, dtype=np.float32) / 255.0
        g_mean = float(pixels[:, :, 1].mean())

        if g_mean > 0.5:
            confidence = min((g_mean - 0.5) * 2.0, 1.0)
            return NormalFormatDetection(NormalFormat.OPENGL, confidence, g_mean)

        confidence = min((0.5 - g_mean) * 2.0, 1.0)
        return NormalFormatDetection(NormalFormat.DIRECTX, confidence, g_mean)
