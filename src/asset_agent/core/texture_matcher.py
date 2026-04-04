"""PBR texture matching engine.

Scans a directory for image files and classifies them into PBR channels
(Albedo, Normal, Roughness, Metallic, etc.) using configurable regex patterns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from asset_agent.exceptions import MissingAlbedoError
from asset_agent.utils.file_utils import IMAGE_EXTENSIONS, collect_images
from asset_agent.utils.logging import get_logger

logger = get_logger("core.texture_matcher")

_PATTERNS_FILE = Path(__file__).resolve().parents[3] / "config" / "texture_patterns.yaml"

# Fallback format priority when no config is available.
_DEFAULT_FORMAT_PRIORITY: list[str] = [
    ".png", ".exr", ".tga", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp",
]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ChannelRule:
    """Compiled matching rule for a single PBR channel."""

    name: str
    pattern: re.Pattern[str]
    color_space: str = "Non-Color"
    required: bool = False
    glossiness_keywords: list[str] = field(default_factory=list)


@dataclass
class TextureMatch:
    """A single matched texture file with metadata."""

    path: Path
    channel: str
    color_space: str
    is_glossiness: bool = False


@dataclass
class TextureMap:
    """Complete set of PBR texture assignments for a model.

    Each field corresponds to a PBR channel and holds an optional
    ``TextureMatch``.  The ``extra`` dict captures any channel defined
    in the config but not represented by a dedicated field.
    """

    albedo: TextureMatch | None = None
    normal: TextureMatch | None = None
    roughness: TextureMatch | None = None
    metallic: TextureMatch | None = None
    ao: TextureMatch | None = None
    emissive: TextureMatch | None = None
    opacity: TextureMatch | None = None
    displacement: TextureMatch | None = None
    orm: TextureMatch | None = None
    extra: dict[str, TextureMatch] = field(default_factory=dict)

    # Convenience helpers -----------------------------------------------

    @property
    def channel_names(self) -> list[str]:
        """Return names of all non-None assigned channels."""
        names: list[str] = []
        for f in fields(self):
            if f.name == "extra":
                continue
            if getattr(self, f.name) is not None:
                names.append(f.name)
        names.extend(self.extra.keys())
        return names

    def as_dict(self) -> dict[str, TextureMatch]:
        """Return a flat mapping ``{channel: TextureMatch}`` for assigned channels."""
        result: dict[str, TextureMatch] = {}
        for f in fields(self):
            if f.name == "extra":
                continue
            val = getattr(self, f.name)
            if val is not None:
                result[f.name] = val
        result.update(self.extra)
        return result


# ---------------------------------------------------------------------------
# Pattern loading
# ---------------------------------------------------------------------------

def load_channel_rules(config_path: Path | None = None) -> list[ChannelRule]:
    """Load channel regex rules from a YAML config file.

    Args:
        config_path: Path to the ``texture_patterns.yaml`` file.
                     Defaults to ``config/texture_patterns.yaml``.

    Returns:
        Ordered list of ``ChannelRule`` objects.
    """
    path = config_path or _PATTERNS_FILE
    with open(path, encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh)

    rules: list[ChannelRule] = []
    for name, info in data.get("channels", {}).items():
        rules.append(ChannelRule(
            name=name,
            pattern=re.compile(info["pattern"], re.IGNORECASE),
            color_space=info.get("color_space", "Non-Color"),
            required=info.get("required", False),
            glossiness_keywords=[kw.lower() for kw in info.get("glossiness_keywords", [])],
        ))
    return rules


def load_format_priority(config_path: Path | None = None) -> list[str]:
    """Load the image-format preference order from config.

    Args:
        config_path: Path to the ``texture_patterns.yaml`` file.

    Returns:
        List of extensions in descending priority (e.g. ``[".png", ".exr", ...]``).
    """
    path = config_path or _PATTERNS_FILE
    with open(path, encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh)
    return data.get("format_priority", _DEFAULT_FORMAT_PRIORITY)


# ---------------------------------------------------------------------------
# Disambiguation
# ---------------------------------------------------------------------------

def _disambiguate(
    candidates: list[Path],
    model_name: str | None,
    format_priority: list[str],
) -> Path:
    """Pick the best candidate when multiple files match a channel.

    Priority order:
      1. Filename contains the model name (case-insensitive).
      2. Image format preference (``.png > .exr > .tga > ...``).

    Args:
        candidates: List of matching file paths.
        model_name: Optional model name hint for tie-breaking.
        format_priority: Ordered list of preferred extensions.

    Returns:
        The single best candidate.
    """
    if len(candidates) == 1:
        return candidates[0]

    if model_name:
        model_lower = model_name.lower()
        name_matches = [c for c in candidates if model_lower in c.stem.lower()]
        if len(name_matches) == 1:
            return name_matches[0]
        if name_matches:
            candidates = name_matches

    priority_map = {ext: idx for idx, ext in enumerate(format_priority)}
    worst = len(format_priority)

    candidates.sort(key=lambda p: priority_map.get(p.suffix.lower(), worst))
    return candidates[0]


# ---------------------------------------------------------------------------
# Core matcher
# ---------------------------------------------------------------------------

@dataclass
class TextureMatcher:
    """Scans a folder of images and classifies them into PBR channels.

    Args:
        rules: Ordered list of channel regex rules.
        format_priority: Image-format preference for disambiguation.
        model_name: Optional model name hint (improves disambiguation).
    """

    rules: list[ChannelRule]
    format_priority: list[str] = field(default_factory=lambda: list(_DEFAULT_FORMAT_PRIORITY))
    model_name: str | None = None

    # -- public API ---------------------------------------------------------

    def match(self, texture_dir: Path, *, recursive: bool = True) -> TextureMap:
        """Scan *texture_dir* and return a fully-resolved ``TextureMap``.

        Args:
            texture_dir: Directory containing PBR textures.
            recursive: Descend into subdirectories.

        Returns:
            Populated ``TextureMap``.

        Raises:
            MissingAlbedoError: If no Albedo texture is found.
        """
        images = collect_images(texture_dir, recursive=recursive)
        if not images:
            logger.warning("No image files found in '%s'", texture_dir)
            raise MissingAlbedoError(str(texture_dir))

        logger.info(
            "Found %d image(s) in '%s'",
            len(images),
            texture_dir,
        )

        candidates: dict[str, list[Path]] = {rule.name: [] for rule in self.rules}
        matched_files: set[Path] = set()
        assigned_files: set[Path] = set()

        for image in images:
            stem = image.stem.lower()
            for rule in self.rules:
                if rule.pattern.search(stem):
                    candidates[rule.name].append(image)
                    matched_files.add(image)
                    break  # first-match-wins across channels

        # Base-name inference: if no albedo candidate was found via regex,
        # look for an unmatched image whose stem is the common prefix of
        # files that *did* match other channels (e.g. "texture_pbr_20250901"
        # when "texture_pbr_20250901_normal" matched normal).
        if not candidates.get("albedo"):
            inferred = self._infer_albedo(images, matched_files)
            if inferred:
                candidates["albedo"] = [inferred]
                logger.info("  albedo (inferred from base name) -> %s", inferred.name)

        texture_map = TextureMap()

        for rule in self.rules:
            hits = candidates[rule.name]
            if not hits:
                if rule.required:
                    raise MissingAlbedoError(str(texture_dir))
                if rule.name != "displacement":
                    logger.debug("No texture found for channel '%s'", rule.name)
                continue

            chosen = _disambiguate(hits, self.model_name, self.format_priority)
            if chosen in assigned_files:
                continue
            assigned_files.add(chosen)

            is_gloss = self._is_glossiness(chosen, rule)

            tm = TextureMatch(
                path=chosen,
                channel=rule.name,
                color_space=rule.color_space,
                is_glossiness=is_gloss,
            )

            if hasattr(texture_map, rule.name) and rule.name != "extra":
                setattr(texture_map, rule.name, tm)
            else:
                texture_map.extra[rule.name] = tm

            logger.info(
                "  %-12s -> %s%s",
                rule.name,
                chosen.name,
                " (glossiness)" if is_gloss else "",
            )

        self._warn_missing(texture_map)
        return texture_map

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _is_glossiness(path: Path, rule: ChannelRule) -> bool:
        """Return ``True`` if the file name implies a glossiness map."""
        if not rule.glossiness_keywords:
            return False
        stem_lower = path.stem.lower()
        return any(kw in stem_lower for kw in rule.glossiness_keywords)

    @staticmethod
    def _infer_albedo(images: list[Path], matched: set[Path]) -> Path | None:
        """Try to identify an albedo texture by base-name prefix analysis.

        If matched textures share a common stem prefix (e.g.
        ``texture_pbr_20250901_normal``, ``texture_pbr_20250901_roughness``),
        and there is an unmatched image whose stem *equals* that prefix
        (``texture_pbr_20250901``), treat it as the albedo.
        """
        if len(matched) < 1:
            return None

        unmatched = [img for img in images if img not in matched]
        if not unmatched:
            return None

        matched_stems = [p.stem.lower() for p in matched]

        prefix = matched_stems[0]
        for stem in matched_stems[1:]:
            while prefix and not stem.startswith(prefix):
                # Strip last segment (separated by _ or -)
                for sep_idx in range(len(prefix) - 1, -1, -1):
                    if prefix[sep_idx] in ("_", "-"):
                        prefix = prefix[:sep_idx]
                        break
                else:
                    prefix = ""

        if len(prefix) < 3:
            return None

        for img in unmatched:
            if img.stem.lower() == prefix or img.stem.lower() == prefix.rstrip("_-"):
                return img

        return None

    @staticmethod
    def _warn_missing(texture_map: TextureMap) -> None:
        """Log warnings for commonly-expected channels that are absent."""
        expected_optional = ["normal", "roughness", "metallic"]
        for name in expected_optional:
            if getattr(texture_map, name) is None:
                logger.warning("Channel '%s' has no matching texture", name)


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------

def create_matcher(
    model_name: str | None = None,
    config_path: Path | None = None,
) -> TextureMatcher:
    """Build a ``TextureMatcher`` with rules loaded from the default config.

    Args:
        model_name: Optional model name for disambiguation.
        config_path: Override for the ``texture_patterns.yaml`` path.

    Returns:
        Ready-to-use ``TextureMatcher``.
    """
    rules = load_channel_rules(config_path)
    priority = load_format_priority(config_path)
    return TextureMatcher(rules=rules, format_priority=priority, model_name=model_name)
