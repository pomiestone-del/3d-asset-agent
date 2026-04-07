"""Unified exception hierarchy for the 3D asset processing pipeline."""

from __future__ import annotations


class AssetAgentError(Exception):
    """Root exception for every error raised by the asset-agent pipeline."""


# ── Texture Matching ─────────────────────────────────────────────────────────


class TextureMatchError(AssetAgentError):
    """Base class for texture-matching errors."""


class MissingAlbedoError(TextureMatchError):
    """Raised when no Albedo / Base Color texture is found (it is mandatory)."""

    def __init__(self, texture_dir: str) -> None:
        self.texture_dir = texture_dir
        super().__init__(
            f"No Albedo/BaseColor texture found in '{texture_dir}'. "
            "At least one Albedo map is required."
        )


class AmbiguousTextureError(TextureMatchError):
    """Raised when multiple textures match a channel and cannot be disambiguated."""

    def __init__(self, channel: str, candidates: list[str]) -> None:
        self.channel = channel
        self.candidates = candidates
        super().__init__(
            f"Ambiguous match for channel '{channel}': {candidates}. "
            "Provide a model name hint or remove duplicates."
        )


# ── Blender Execution ────────────────────────────────────────────────────────


class BlenderError(AssetAgentError):
    """Base class for Blender-related errors."""


class BlenderNotFoundError(BlenderError):
    """Raised when the Blender executable cannot be located."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"Blender executable not found at '{path}'.")


class BlenderExecutionError(BlenderError):
    """Raised when a Blender subprocess exits with a non-zero return code."""

    def __init__(self, stderr: str, returncode: int | None = None) -> None:
        self.stderr = stderr
        self.returncode = returncode
        msg = "Blender script failed"
        if returncode is not None:
            msg += f" (exit code {returncode})"
        msg += f":\n{stderr[:2000]}"
        super().__init__(msg)


class BlenderTimeoutError(BlenderError):
    """Raised when a Blender subprocess exceeds its timeout."""

    def __init__(self, timeout: int) -> None:
        self.timeout = timeout
        super().__init__(f"Blender process timed out after {timeout}s.")


# ── Import / Export ──────────────────────────────────────────────────────────


class ImportError_(AssetAgentError):
    """Raised when a 3D model file cannot be imported.

    Named with a trailing underscore to avoid shadowing the builtin.
    """

    def __init__(self, path: str, reason: str = "") -> None:
        self.path = path
        self.reason = reason
        msg = f"Failed to import '{path}'"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


class ExportError(AssetAgentError):
    """Raised when GLB/glTF export fails."""

    def __init__(self, path: str, reason: str = "") -> None:
        self.path = path
        self.reason = reason
        msg = f"Failed to export '{path}'"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


# ── Validation ───────────────────────────────────────────────────────────────


class ValidationError(AssetAgentError):
    """Raised when GLB post-export validation detects problems."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        bullet_list = "\n  • ".join(errors)
        super().__init__(f"GLB validation failed:\n  • {bullet_list}")


# ── Configuration ────────────────────────────────────────────────────────────


class ConfigError(AssetAgentError):
    """Raised when configuration loading or parsing fails."""


# ── Normal Map Conversion ─────────────────────────────────────────────────────


class NormalMapConversionError(AssetAgentError):
    """Raised when normal map format detection or G-channel conversion fails."""
