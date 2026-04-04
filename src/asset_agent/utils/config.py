"""Configuration loading and validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from asset_agent.exceptions import ConfigError

_DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "config" / "default.yaml"


@dataclass
class BlenderConfig:
    executable: str = "blender"


@dataclass
class RenderConfig:
    engine: str = "CYCLES"
    resolution: list[int] = field(default_factory=lambda: [1920, 1080])
    samples: int = 128
    denoise: bool = True
    film_transparent: bool = True
    gpu_enabled: bool = True


@dataclass
class ExportConfig:
    format: str = "GLB"
    apply_modifiers: bool = True
    export_tangents: bool = True
    image_format: str = "AUTO"


@dataclass
class ValidationConfig:
    enabled: bool = True


@dataclass
class LoggingConfig:
    level: str = "INFO"


@dataclass
class AppConfig:
    """Top-level application configuration."""

    blender: BlenderConfig = field(default_factory=BlenderConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base* (non-destructive)."""
    merged = base.copy()
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def _dict_to_config(data: dict[str, Any]) -> AppConfig:
    return AppConfig(
        blender=BlenderConfig(**data.get("blender", {})),
        render=RenderConfig(**data.get("render", {})),
        export=ExportConfig(**data.get("export", {})),
        validation=ValidationConfig(**data.get("validation", {})),
        logging=LoggingConfig(**data.get("logging", {})),
    )


def load_config(override_path: Path | None = None) -> AppConfig:
    """Load configuration from the default YAML, optionally merged with an override file.

    Args:
        override_path: Optional path to a user-provided YAML that overrides defaults.

    Returns:
        Fully resolved ``AppConfig`` instance.

    Raises:
        ConfigError: If any YAML file cannot be read or parsed.
    """
    try:
        with open(_DEFAULT_CONFIG, encoding="utf-8") as fh:
            base: dict[str, Any] = yaml.safe_load(fh) or {}
    except Exception as exc:
        raise ConfigError(f"Cannot load default config: {exc}") from exc

    if override_path is not None:
        try:
            with open(override_path, encoding="utf-8") as fh:
                overrides: dict[str, Any] = yaml.safe_load(fh) or {}
        except Exception as exc:
            raise ConfigError(f"Cannot load override config '{override_path}': {exc}") from exc
        base = _merge_dicts(base, overrides)

    return _dict_to_config(base)
