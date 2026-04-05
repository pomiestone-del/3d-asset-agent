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
    texture_dir: Path | None = None,
) -> Path:
    """Pick the best candidate when multiple files match a channel.

    Priority order:
      1. Filename contains the model name (case-insensitive).
      2. Prefer files at the texture directory root over subdirectories.
      3. Image format preference (``.png > .exr > .tga > ...``).

    Args:
        candidates: List of matching file paths.
        model_name: Optional model name hint for tie-breaking.
        format_priority: Ordered list of preferred extensions.
        texture_dir: Optional root scan directory — files here are preferred.

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

    # Prefer files at the root of the texture directory over subdirectories
    if texture_dir is not None and len(candidates) > 1:
        root_candidates = [c for c in candidates if c.parent == texture_dir]
        if root_candidates:
            candidates = root_candidates

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

    def match(
        self,
        texture_dir: Path,
        *,
        recursive: bool = True,
        obj_path: Path | None = None,
    ) -> TextureMap:
        """Scan *texture_dir* and return a fully-resolved ``TextureMap``.

        MTL-declared textures take priority over regex matches.

        Args:
            texture_dir: Directory containing PBR textures.
            recursive: Descend into subdirectories.
            obj_path: Optional path to the ``.obj`` file used to locate
                      the MTL for explicit texture declarations.

        Returns:
            Populated ``TextureMap``.

        Raises:
            MissingAlbedoError: If no Albedo texture is found by any method.
        """
        mtl_assignments: dict[str, Path] = {}
        if obj_path is not None:
            from asset_agent.core.mtl_parser import find_mtl_for_obj, parse_mtl
            mtl_file = find_mtl_for_obj(obj_path)
            if mtl_file:
                mtl_data = parse_mtl(mtl_file)
                if len(mtl_data) == 1:
                    mtl_assignments = next(iter(mtl_data.values()))
                elif len(mtl_data) > 1:
                    # Multi-material: merge all paths as hints (single-material call)
                    for mat_channels in mtl_data.values():
                        for ch, p in mat_channels.items():
                            mtl_assignments.setdefault(ch, p)
        return self._match_with_mtl(texture_dir, recursive=recursive, mtl_assignments=mtl_assignments)

    def match_multi(
        self,
        texture_dir: Path,
        material_names: list[str],
        *,
        recursive: bool = True,
        obj_path: Path | None = None,
    ) -> dict[str, TextureMap]:
        """Match textures independently for each material name.

        Uses the material name as the ``model_name`` disambiguation hint.
        Materials that yield no albedo get an empty ``TextureMap`` instead of raising.

        Args:
            texture_dir: Folder containing PBR textures.
            material_names: List of material names (from MTL ``newmtl``).
            recursive: Descend into sub-directories.
            obj_path: Optional OBJ path for MTL first-pass per material.

        Returns:
            ``{material_name: TextureMap}`` for every name in *material_names*.
        """
        # Pre-parse MTL once for all materials
        mtl_per_material: dict[str, dict[str, Path]] = {}
        if obj_path is not None:
            from asset_agent.core.mtl_parser import find_mtl_for_obj, parse_mtl
            mtl_file = find_mtl_for_obj(obj_path)
            if mtl_file:
                mtl_per_material = parse_mtl(mtl_file)

        result: dict[str, TextureMap] = {}
        for mat_name in material_names:
            sub_matcher = TextureMatcher(
                rules=self.rules,
                format_priority=self.format_priority,
                model_name=mat_name,
            )
            mtl_assignments = mtl_per_material.get(mat_name, {})
            try:
                texture_map = sub_matcher._match_with_mtl(
                    texture_dir,
                    recursive=recursive,
                    mtl_assignments=mtl_assignments,
                )
            except MissingAlbedoError:
                logger.warning("No albedo found for material '%s'; using empty map.", mat_name)
                texture_map = TextureMap()
            result[mat_name] = texture_map

        return result

    def _match_with_mtl(
        self,
        texture_dir: Path,
        *,
        recursive: bool = True,
        mtl_assignments: dict[str, Path] | None = None,
    ) -> TextureMap:
        """Run matching with optional pre-parsed MTL assignments injected.

        MTL assignments take priority over regex matches.
        """
        images = collect_images(texture_dir, recursive=recursive)
        if not images and not mtl_assignments:
            logger.warning("No image files found in '%s'", texture_dir)
            raise MissingAlbedoError(str(texture_dir))

        logger.info(
            "Found %d image(s) in '%s'",
            len(images),
            texture_dir,
        )

        candidates: dict[str, list[Path]] = {rule.name: [] for rule in self.rules}
        matched_files: set[Path] = set()

        for image in images:
            stem = image.stem.lower()
            for rule in self.rules:
                if rule.pattern.search(stem):
                    candidates[rule.name].append(image)
                    matched_files.add(image)
                    break

        if not candidates.get("albedo"):
            inferred = self._infer_albedo(images, matched_files)
            if inferred:
                candidates["albedo"] = [inferred]
                logger.info("  albedo (inferred from base name) -> %s", inferred.name)

        texture_map = TextureMap()
        assigned_files: set[Path] = set()

        # MTL assignments win over regex
        for channel, path in (mtl_assignments or {}).items():
            rule = next((r for r in self.rules if r.name == channel), None)
            color_space = rule.color_space if rule else "Non-Color"
            is_gloss = channel == "roughness" and any(
                kw in path.stem.lower() for kw in ["gloss", "glossiness", "shini"]
            )
            tm = TextureMatch(path=path, channel=channel, color_space=color_space, is_glossiness=is_gloss)
            if hasattr(texture_map, channel) and channel != "extra":
                setattr(texture_map, channel, tm)
                assigned_files.add(path)
                logger.info("  %-12s -> %s (MTL)", channel, path.name)

        # Regex fills remaining channels.
        # First pass: resolve albedo so we can determine directory affinity.
        albedo_dir: Path | None = None
        if texture_map.albedo is not None:
            albedo_dir = texture_map.albedo.path.parent
        elif candidates.get("albedo"):
            albedo_pick = _disambiguate(candidates["albedo"], self.model_name, self.format_priority, texture_dir)
            albedo_dir = albedo_pick.parent

        for rule in self.rules:
            if hasattr(texture_map, rule.name) and getattr(texture_map, rule.name) is not None:
                continue
            hits = candidates[rule.name]
            if not hits:
                if rule.required and texture_map.albedo is None:
                    raise MissingAlbedoError(str(texture_dir))
                if rule.name != "displacement":
                    logger.debug("No texture found for channel '%s'", rule.name)
                continue

            # Directory affinity: when albedo lives in a proper subdirectory
            # (not the scan root), restrict other channels to that same dir.
            # This prevents cross-directory mixing (e.g. thatch opacity mask
            # applied to a planks albedo).
            if (
                albedo_dir is not None
                and rule.name != "albedo"
                and albedo_dir != texture_dir
            ):
                colocated = [h for h in hits if h.parent == albedo_dir]
                if colocated:
                    hits = colocated
                elif len(hits) > 1:
                    # Multiple candidates from other dirs — skip ambiguous match
                    continue
                else:
                    # Single candidate from a different dir — skip it; it likely
                    # belongs to a different material set.
                    logger.debug(
                        "Skipping '%s' for channel '%s' (different dir than albedo)",
                        hits[0].name, rule.name,
                    )
                    continue

            chosen = _disambiguate(hits, self.model_name, self.format_priority, texture_dir)
            if chosen in assigned_files:
                continue
            assigned_files.add(chosen)
            is_gloss = self._is_glossiness(chosen, rule)
            tm = TextureMatch(
                path=chosen, channel=rule.name,
                color_space=rule.color_space, is_glossiness=is_gloss,
            )
            if hasattr(texture_map, rule.name) and rule.name != "extra":
                setattr(texture_map, rule.name, tm)
            else:
                texture_map.extra[rule.name] = tm
            logger.info(
                "  %-12s -> %s%s", rule.name, chosen.name,
                " (glossiness)" if is_gloss else "",
            )

        if texture_map.albedo is None:
            raise MissingAlbedoError(str(texture_dir))

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

    def detect_material_sets(self, texture_dir: Path) -> list[str]:
        """Detect distinct PBR texture sets by finding multiple albedo files.

        Extracts the filename prefix before the channel keyword for each
        albedo-matching file.  Returns prefixes when 2+ distinct sets are found.

        Args:
            texture_dir: Directory containing textures.

        Returns:
            Sorted list of set prefixes (original case).
            Empty list if fewer than 2 sets found.
        """
        images = collect_images(texture_dir, recursive=True)
        albedo_rule = next((r for r in self.rules if r.name == "albedo"), None)
        if albedo_rule is None:
            return []

        prefixes: dict[str, str] = {}  # lower -> original
        for img in images:
            stem = img.stem
            m = albedo_rule.pattern.search(stem.lower())
            if m:
                raw_prefix = stem[: m.start()].rstrip("_- ")
                if raw_prefix:
                    prefixes.setdefault(raw_prefix.lower(), raw_prefix)

        if len(prefixes) < 2:
            return []

        return sorted(prefixes.values())

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
