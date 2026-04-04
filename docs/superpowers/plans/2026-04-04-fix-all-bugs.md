# Fix All Bugs & Feature Gaps — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all P0/P1/P2 issues identified in HANDOFF.md: encoding crash, validation false-fail, MTL-based texture matching, multi-material Blender pipeline, FBX importer, and P2 UX improvements.

**Architecture:** Five independent groups that can be executed in any order. P0 fixes are trivial one-liners. Multi-material and MTL parsing share data structures (do together). FBX importer and P2 UX are independent. Backward compatibility preserved throughout — all new features are additive.

**Tech Stack:** Python 3.10, typer, pyyaml, pydantic, bpy (Blender 4.0 embedded Python), subprocess, rich

---

## GROUP A — P0 Bug Fixes

### Task 1: Fix GBK encoding crash in blender_runner.py

**Files:**
- Modify: `src/asset_agent/core/blender_runner.py:94-100`
- Create: `tests/test_blender_runner.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_blender_runner.py`:

```python
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
```

- [ ] **Step 2: Run to confirm it fails**

```bash
cd D:\CodingProjects\BlenderTexAgent\3d-asset-agent
python -m pytest tests/test_blender_runner.py -v
```

Expected: `FAILED` — `AssertionError: encoding must be utf-8`

- [ ] **Step 3: Apply the fix**

In `src/asset_agent/core/blender_runner.py`, replace lines 94-100:

```python
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_blender_runner.py -v
```

Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add src/asset_agent/core/blender_runner.py tests/test_blender_runner.py
git commit -m "fix: use UTF-8 encoding in Blender subprocess to prevent GBK crash on Windows"
```

---

### Task 2: Fix no-texture model validation false FAIL

**Files:**
- Modify: `src/asset_agent/agent.py:110-125`
- Modify: `tests/test_blender_runner.py` (add test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_blender_runner.py`:

```python
def test_skip_validation_when_no_textures(tmp_path):
    """When no albedo is found (empty textures_payload), validation must be skipped.

    Audi RS6 has only MTL color definitions — no image textures. The GLB is valid
    but validate_glb() incorrectly fails because it requires Base Color to have a
    link to an image node, which is not the case for pure-color materials.
    """
    from asset_agent.agent import AssetAgent
    from asset_agent.exceptions import MissingAlbedoError

    agent = AssetAgent()
    agent.config.render.resolution = [160, 120]
    agent.config.render.samples = 4
    agent.config.render.gpu_enabled = False

    # Create a dummy OBJ so ObjImporter.validate_file() passes
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
```

Add the missing import at the top of the test file (if not already there):

```python
from unittest.mock import MagicMock, patch
```

- [ ] **Step 2: Run to confirm it fails**

```bash
python -m pytest tests/test_blender_runner.py::test_skip_validation_when_no_textures -v
```

Expected: `FAILED` — `AssertionError: skip_validation must be True`

- [ ] **Step 3: Apply the fix**

In `src/asset_agent/agent.py`, change line ~124:

```python
        # Before (original):
        skip_validation=not cfg.validation.enabled,

        # After:
        skip_validation=(not cfg.validation.enabled) or (not textures_payload),
```

Full context (lines 110-126 of agent.py):

```python
        blender_result = run_process_asset(
            obj_path=obj_path,
            textures_json=textures_payload,
            output_dir=output_dir,
            model_name=model_name,
            blender_path=cfg.blender.executable,
            render_engine=cfg.render.engine,
            render_width=cfg.render.resolution[0],
            render_height=cfg.render.resolution[1],
            render_samples=cfg.render.samples,
            denoise=cfg.render.denoise,
            film_transparent=cfg.render.film_transparent,
            gpu_enabled=cfg.render.gpu_enabled,
            skip_validation=(not cfg.validation.enabled) or (not textures_payload),
        )
```

- [ ] **Step 4: Run all tests**

```bash
python -m pytest tests/test_blender_runner.py tests/test_texture_matcher.py -v
```

Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/asset_agent/agent.py tests/test_blender_runner.py
git commit -m "fix: skip GLB validation when no textures provided (pure-color MTL models)"
```

---

## GROUP B — MTL Parsing & Multi-Material Support

### Task 3: Implement MTL file parser

**Files:**
- Create: `src/asset_agent/core/mtl_parser.py`
- Create: `tests/test_mtl_parser.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mtl_parser.py`:

```python
"""Tests for MTL file parser."""
from __future__ import annotations

from pathlib import Path
import pytest
from asset_agent.core.mtl_parser import find_mtl_for_obj, parse_mtl


@pytest.fixture
def simple_mtl(tmp_path) -> Path:
    mtl = tmp_path / "Sword.mtl"
    mtl.write_text(
        "newmtl SwordBlade\n"
        f"map_Kd {tmp_path / 'blade_diffuse.png'}\n"
        f"map_Bump {tmp_path / 'blade_normal.png'}\n"
        f"map_Ns {tmp_path / 'blade_roughness.png'}\n"
        "\n"
        "newmtl SwordHandle\n"
        f"map_Kd {tmp_path / 'handle_diffuse.png'}\n",
        encoding="utf-8",
    )
    # Create texture stubs
    for name in ["blade_diffuse.png", "blade_normal.png", "blade_roughness.png", "handle_diffuse.png"]:
        (tmp_path / name).write_bytes(b"stub")
    return mtl


def test_parse_mtl_returns_material_names(simple_mtl):
    result = parse_mtl(simple_mtl)
    assert "SwordBlade" in result
    assert "SwordHandle" in result


def test_parse_mtl_maps_map_kd_to_albedo(simple_mtl, tmp_path):
    result = parse_mtl(simple_mtl)
    assert "albedo" in result["SwordBlade"]
    assert result["SwordBlade"]["albedo"].name == "blade_diffuse.png"


def test_parse_mtl_maps_map_bump_to_normal(simple_mtl, tmp_path):
    result = parse_mtl(simple_mtl)
    assert "normal" in result["SwordBlade"]
    assert result["SwordBlade"]["normal"].name == "blade_normal.png"


def test_parse_mtl_maps_map_ns_to_roughness(simple_mtl):
    result = parse_mtl(simple_mtl)
    assert "roughness" in result["SwordBlade"]


def test_parse_mtl_ignores_nonexistent_texture(tmp_path):
    mtl = tmp_path / "test.mtl"
    mtl.write_text("newmtl Mat\nmap_Kd nonexistent.png\n", encoding="utf-8")
    result = parse_mtl(mtl)
    # Nonexistent file should not appear in result
    assert result.get("Mat", {}).get("albedo") is None


def test_parse_mtl_nonexistent_file():
    result = parse_mtl(Path("does_not_exist.mtl"))
    assert result == {}


def test_find_mtl_for_obj_reads_mtllib_declaration(tmp_path):
    mtl = tmp_path / "Sword.mtl"
    mtl.write_text("# mtl\n", encoding="utf-8")
    obj = tmp_path / "Sword.obj"
    obj.write_text("mtllib Sword.mtl\nv 0 0 0\n", encoding="utf-8")
    assert find_mtl_for_obj(obj) == mtl


def test_find_mtl_for_obj_fallback_same_stem(tmp_path):
    mtl = tmp_path / "Sword.mtl"
    mtl.write_text("# mtl\n", encoding="utf-8")
    obj = tmp_path / "Sword.obj"
    obj.write_text("# no mtllib\nv 0 0 0\n", encoding="utf-8")
    assert find_mtl_for_obj(obj) == mtl


def test_find_mtl_for_obj_returns_none_when_missing(tmp_path):
    obj = tmp_path / "NoMTL.obj"
    obj.write_text("# no mtllib\n", encoding="utf-8")
    assert find_mtl_for_obj(obj) is None
```

- [ ] **Step 2: Run to confirm failures**

```bash
python -m pytest tests/test_mtl_parser.py -v
```

Expected: `ModuleNotFoundError: No module named 'asset_agent.core.mtl_parser'`

- [ ] **Step 3: Implement `mtl_parser.py`**

Create `src/asset_agent/core/mtl_parser.py`:

```python
"""MTL (Material Template Library) file parser.

Extracts per-material texture path declarations to assist PBR texture matching.
MTL files are co-located with OBJ files and explicitly declare texture file paths
using statements like map_Kd, map_Bump, map_Ns, map_d, disp.
"""
from __future__ import annotations

import re
from pathlib import Path

# MTL statement keyword → PBR channel name (keys are lowercase)
_MTL_KEYWORD_TO_CHANNEL: dict[str, str | None] = {
    "map_kd":   "albedo",        # Diffuse color texture
    "map_ka":   "albedo",        # Ambient color (fallback albedo)
    "map_bump": "normal",        # Normal/bump map
    "bump":     "normal",        # Alternative bump syntax
    "map_ks":   None,            # Specular color — no direct BSDF mapping, skip
    "map_ns":   "roughness",     # Shininess map (inverted roughness — flagged below)
    "map_d":    "opacity",       # Dissolve/alpha texture
    "disp":     "displacement",  # Displacement map
}

# These MTL channels represent glossiness (inverted roughness) rather than roughness.
_GLOSSINESS_CHANNELS = {"map_ns"}


def parse_mtl(mtl_path: Path) -> dict[str, dict[str, Path]]:
    """Parse an MTL file and return per-material texture path declarations.

    Args:
        mtl_path: Path to the ``.mtl`` file.

    Returns:
        ``{material_name: {channel_name: absolute_path}}``.
        Only textures whose files exist on disk are included.
        Relative paths are resolved relative to the MTL file's directory.
    """
    if not mtl_path.exists():
        return {}

    base_dir = mtl_path.parent
    result: dict[str, dict[str, Path]] = {}
    current_mat: str | None = None

    with open(mtl_path, encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split(None, 1)
            if not parts:
                continue

            keyword = parts[0].lower()
            rest = parts[1].strip() if len(parts) > 1 else ""

            if keyword == "newmtl":
                current_mat = rest
                result.setdefault(current_mat, {})
                continue

            if current_mat is None or keyword not in _MTL_KEYWORD_TO_CHANNEL:
                continue

            channel = _MTL_KEYWORD_TO_CHANNEL[keyword]
            if channel is None:
                continue

            # MTL texture statements can have options (e.g., -bm 1.0) before the path.
            # The path is always the last token.
            tex_str = rest.split()[-1] if rest else ""
            if not tex_str:
                continue

            tex_path = Path(tex_str)
            if not tex_path.is_absolute():
                tex_path = (base_dir / tex_path).resolve()

            if tex_path.exists():
                result[current_mat][channel] = tex_path

    return result


def find_mtl_for_obj(obj_path: Path) -> Path | None:
    """Locate the MTL file referenced by an OBJ file.

    First searches the OBJ file for a ``mtllib`` declaration; if not found,
    falls back to a file with the same stem and ``.mtl`` extension.

    Args:
        obj_path: Path to the ``.obj`` file.

    Returns:
        Resolved path to the ``.mtl`` file, or ``None`` if not found.
    """
    if not obj_path.exists():
        return None

    mtllib_re = re.compile(r"^mtllib\s+(.+)$", re.IGNORECASE)

    with open(obj_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = mtllib_re.match(line.strip())
            if m:
                mtl_name = m.group(1).strip()
                candidate = (obj_path.parent / mtl_name).resolve()
                if candidate.exists():
                    return candidate

    # Fallback: same directory, same stem
    default = obj_path.with_suffix(".mtl")
    return default if default.exists() else None
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_mtl_parser.py -v
```

Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
git add src/asset_agent/core/mtl_parser.py tests/test_mtl_parser.py
git commit -m "feat: add MTL file parser for explicit texture path extraction"
```

---

### Task 4: Integrate MTL parsing as first-pass in TextureMatcher

**Files:**
- Modify: `src/asset_agent/core/texture_matcher.py`
- Modify: `tests/test_texture_matcher.py` (add tests at end)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_texture_matcher.py`:

```python
# ── MTL integration ──────────────────────────────────────────────────────────

class TestMtlIntegration:
    """TextureMatcher should use MTL-declared paths before falling back to regex."""

    def test_mtl_declared_albedo_used_when_regex_fails(self, tmp_path):
        """If regex can't find albedo but MTL has map_Kd, use the MTL path."""
        # File named in a way regex won't match as albedo
        tex = tmp_path / "weird_name_xyz.png"
        tex.write_bytes(b"stub")

        mtl = tmp_path / "model.mtl"
        mtl.write_text(f"newmtl Mat\nmap_Kd {tex}\n", encoding="utf-8")

        obj = tmp_path / "model.obj"
        obj.write_text("mtllib model.mtl\nv 0 0 0\n", encoding="utf-8")

        matcher = create_matcher(model_name="model")
        # Pass obj_path so matcher can find the MTL
        result = matcher.match(tmp_path, obj_path=obj)
        assert result.albedo is not None
        assert result.albedo.path == tex

    def test_regex_channels_fill_gaps_not_in_mtl(self, tmp_path):
        """Channels absent from MTL should still be matched by regex."""
        albedo = tmp_path / "model_diffuse.png"
        normal = tmp_path / "model_normal.png"
        albedo.write_bytes(b"stub")
        normal.write_bytes(b"stub")

        # MTL only declares albedo
        mtl = tmp_path / "model.mtl"
        mtl.write_text(f"newmtl Mat\nmap_Kd {albedo}\n", encoding="utf-8")

        obj = tmp_path / "model.obj"
        obj.write_text("mtllib model.mtl\nv 0 0 0\n", encoding="utf-8")

        matcher = create_matcher(model_name="model")
        result = matcher.match(tmp_path, obj_path=obj)

        assert result.albedo is not None
        assert result.normal is not None  # found via regex
```

- [ ] **Step 2: Run to confirm failures**

```bash
python -m pytest tests/test_texture_matcher.py::TestMtlIntegration -v
```

Expected: `FAILED` — `TypeError: match() got an unexpected keyword argument 'obj_path'`

- [ ] **Step 3: Update `TextureMatcher.match()` to accept `obj_path`**

In `src/asset_agent/core/texture_matcher.py`, update the `match()` signature and body:

```python
    def match(
        self,
        texture_dir: Path,
        *,
        recursive: bool = True,
        obj_path: Path | None = None,
    ) -> TextureMap:
        """Scan *texture_dir* and return a fully-resolved ``TextureMap``.

        MTL-declared textures take priority over regex matches.  Regex fills
        any channels the MTL did not declare.

        Args:
            texture_dir: Directory containing PBR textures.
            recursive: Descend into subdirectories.
            obj_path: Optional path to the ``.obj`` file.  Used to locate the
                      MTL file for explicit texture declarations.

        Returns:
            Populated ``TextureMap``.

        Raises:
            MissingAlbedoError: If no Albedo texture is found by any method.
        """
        # --- MTL first-pass ---
        mtl_assignments: dict[str, Path] = {}
        if obj_path is not None:
            from asset_agent.core.mtl_parser import find_mtl_for_obj, parse_mtl
            mtl_file = find_mtl_for_obj(obj_path)
            if mtl_file:
                mtl_data = parse_mtl(mtl_file)
                if len(mtl_data) == 1:
                    mtl_assignments = next(iter(mtl_data.values()))
                elif len(mtl_data) > 1:
                    # Multi-material: merge all declared paths as hints
                    # (full multi-material handled separately via match_multi)
                    for mat_channels in mtl_data.values():
                        for ch, p in mat_channels.items():
                            mtl_assignments.setdefault(ch, p)

        # --- Regex scan ---
        images = collect_images(texture_dir, recursive=recursive)
        if not images and not mtl_assignments:
            logger.warning("No image files found in '%s'", texture_dir)
            raise MissingAlbedoError(str(texture_dir))

        candidates: dict[str, list[Path]] = {rule.name: [] for rule in self.rules}
        matched_files: set[Path] = set()

        for image in images:
            stem = image.stem.lower()
            for rule in self.rules:
                if rule.pattern.search(stem):
                    candidates[rule.name].append(image)
                    matched_files.add(image)
                    break

        # Base-name inference (existing logic, unchanged)
        if not candidates.get("albedo"):
            inferred = self._infer_albedo(images, matched_files)
            if inferred:
                candidates["albedo"] = [inferred]
                logger.info("  albedo (inferred from base name) -> %s", inferred.name)

        texture_map = TextureMap()
        assigned_files: set[Path] = set()

        # --- Apply MTL assignments first ---
        for channel, path in mtl_assignments.items():
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

        # --- Regex fills remaining channels ---
        for rule in self.rules:
            if hasattr(texture_map, rule.name) and getattr(texture_map, rule.name) is not None:
                continue  # already set by MTL
            hits = candidates[rule.name]
            if not hits:
                if rule.required and texture_map.albedo is None:
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

            logger.info("  %-12s -> %s%s", rule.name, chosen.name, " (glossiness)" if is_gloss else "")

        if texture_map.albedo is None:
            raise MissingAlbedoError(str(texture_dir))

        self._warn_missing(texture_map)
        return texture_map
```

Note: The original logic (candidates dict, matched_files, _infer_albedo, _disambiguate, _is_glossiness, _warn_missing) is preserved — only the outer structure changes to accommodate the MTL pre-pass and to check albedo at the end rather than relying on `required=true` in the rule.

- [ ] **Step 4: Update `agent.py` to pass `obj_path` to `match_textures()`**

In `src/asset_agent/agent.py`, update the `match_textures` call inside `process()`:

```python
        try:
            texture_map = self.match_textures(
                texture_dir,
                model_name=model_name,
                obj_path=obj_path,          # <-- new
            )
```

And update `match_textures()` signature:

```python
    def match_textures(
        self,
        texture_dir: Path,
        *,
        model_name: str | None = None,
        obj_path: Path | None = None,       # <-- new
    ) -> TextureMap:
        matcher = create_matcher(model_name=model_name)
        return matcher.match(texture_dir, obj_path=obj_path)  # <-- pass through
```

- [ ] **Step 5: Run all tests**

```bash
python -m pytest tests/test_texture_matcher.py tests/test_mtl_parser.py -v
```

Expected: all pass (74 original + 9 MTL + 2 integration = 85 tests)

- [ ] **Step 6: Commit**

```bash
git add src/asset_agent/core/texture_matcher.py src/asset_agent/agent.py tests/test_texture_matcher.py
git commit -m "feat: use MTL-declared texture paths as first-pass before regex matching"
```

---

### Task 5: Multi-material texture matching

**Files:**
- Modify: `src/asset_agent/core/texture_matcher.py` (add `match_multi()`)
- Modify: `src/asset_agent/exporters/glb_exporter.py` (add `build_multi_textures_payload()`)
- Modify: `tests/test_texture_matcher.py` (add tests)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_texture_matcher.py`:

```python
class TestMatchMulti:
    """TextureMatcher.match_multi() returns a TextureMap per material."""

    def test_match_multi_returns_dict_keyed_by_material(self, tmp_path):
        for name in ["boards_diffuse.png", "planks_diffuse.png"]:
            (tmp_path / name).write_bytes(b"stub")

        matcher = create_matcher()
        result = matcher.match_multi(tmp_path, material_names=["boards", "planks"])

        assert isinstance(result, dict)
        assert "boards" in result
        assert "planks" in result

    def test_match_multi_each_value_is_texture_map(self, tmp_path):
        from asset_agent.core.texture_matcher import TextureMap
        (tmp_path / "mat_albedo.png").write_bytes(b"stub")

        matcher = create_matcher()
        result = matcher.match_multi(tmp_path, material_names=["mat"])
        assert isinstance(result["mat"], TextureMap)

    def test_match_multi_missing_albedo_yields_empty_map(self, tmp_path):
        # "unknown" won't match any texture file
        matcher = create_matcher()
        result = matcher.match_multi(tmp_path, material_names=["unknown"])
        assert result["unknown"].albedo is None


class TestBuildMultiTexturesPayload:
    """build_multi_textures_payload() encodes a material field in each entry."""

    def test_payload_includes_material_field(self, tmp_path):
        from asset_agent.core.texture_matcher import TextureMatch, TextureMap
        from asset_agent.exporters.glb_exporter import build_multi_textures_payload

        tex = tmp_path / "blade_albedo.png"
        tex.write_bytes(b"stub")

        maps = {
            "blade": TextureMap(
                albedo=TextureMatch(path=tex, channel="albedo", color_space="sRGB"),
            )
        }
        payload = build_multi_textures_payload(maps)
        assert len(payload) == 1
        assert payload[0]["material"] == "blade"
        assert payload[0]["channel"] == "albedo"

    def test_single_material_payload_has_no_material_field(self, tmp_path):
        from asset_agent.core.texture_matcher import TextureMatch, TextureMap
        from asset_agent.exporters.glb_exporter import build_textures_payload

        tex = tmp_path / "blade_albedo.png"
        tex.write_bytes(b"stub")
        tm = TextureMap(albedo=TextureMatch(path=tex, channel="albedo", color_space="sRGB"))
        payload = build_textures_payload(tm.as_dict())
        assert all("material" not in entry for entry in payload)
```

- [ ] **Step 2: Run to confirm failures**

```bash
python -m pytest tests/test_texture_matcher.py::TestMatchMulti tests/test_texture_matcher.py::TestBuildMultiTexturesPayload -v
```

Expected: `FAILED` — `AttributeError: 'TextureMatcher' object has no attribute 'match_multi'`

- [ ] **Step 3: Add `match_multi()` to TextureMatcher**

Append to `TextureMatcher` class in `src/asset_agent/core/texture_matcher.py`:

```python
    def match_multi(
        self,
        texture_dir: Path,
        material_names: list[str],
        *,
        recursive: bool = True,
        obj_path: Path | None = None,
    ) -> dict[str, TextureMap]:
        """Match textures independently for each material name.

        For each material, runs a fresh regex match using the material name
        as the ``model_name`` disambiguation hint.  Materials that yield no
        albedo get an empty ``TextureMap`` instead of raising.

        Args:
            texture_dir: Folder containing PBR textures.
            material_names: List of material names (from MTL ``newmtl``).
            recursive: Descend into sub-directories.
            obj_path: Optional OBJ path for MTL first-pass per material.

        Returns:
            ``{material_name: TextureMap}`` for every name in *material_names*.
        """
        # Pre-parse MTL once
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
            # Inject MTL assignments for this specific material
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
```

Also add the helper `_match_with_mtl` private method to `TextureMatcher` (refactors the MTL pre-pass logic out of `match()` so both methods share it):

```python
    def _match_with_mtl(
        self,
        texture_dir: Path,
        *,
        recursive: bool = True,
        mtl_assignments: dict[str, Path] | None = None,
    ) -> TextureMap:
        """Internal: run matching with pre-parsed MTL assignments injected."""
        images = collect_images(texture_dir, recursive=recursive)
        if not images and not mtl_assignments:
            raise MissingAlbedoError(str(texture_dir))

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

        texture_map = TextureMap()
        assigned_files: set[Path] = set()

        # MTL assignments have priority
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

        # Regex fills remaining channels
        for rule in self.rules:
            if hasattr(texture_map, rule.name) and getattr(texture_map, rule.name) is not None:
                continue
            hits = candidates[rule.name]
            if not hits:
                if rule.required and texture_map.albedo is None:
                    raise MissingAlbedoError(str(texture_dir))
                continue
            chosen = _disambiguate(hits, self.model_name, self.format_priority)
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

        if texture_map.albedo is None:
            raise MissingAlbedoError(str(texture_dir))

        self._warn_missing(texture_map)
        return texture_map
```

Then update the `match()` method to call `_match_with_mtl()` internally (removing duplication):

```python
    def match(self, texture_dir: Path, *, recursive: bool = True, obj_path: Path | None = None) -> TextureMap:
        """...(existing docstring)..."""
        mtl_assignments: dict[str, Path] = {}
        if obj_path is not None:
            from asset_agent.core.mtl_parser import find_mtl_for_obj, parse_mtl
            mtl_file = find_mtl_for_obj(obj_path)
            if mtl_file:
                mtl_data = parse_mtl(mtl_file)
                if len(mtl_data) == 1:
                    mtl_assignments = next(iter(mtl_data.values()))
                elif len(mtl_data) > 1:
                    for mat_channels in mtl_data.values():
                        for ch, p in mat_channels.items():
                            mtl_assignments.setdefault(ch, p)
        return self._match_with_mtl(texture_dir, recursive=recursive, mtl_assignments=mtl_assignments)
```

- [ ] **Step 4: Add `build_multi_textures_payload()` to `glb_exporter.py`**

Append to `src/asset_agent/exporters/glb_exporter.py`:

```python
def build_multi_textures_payload(
    material_maps: dict[str, "TextureMap"],
) -> list[dict[str, Any]]:
    """Convert a per-material ``{name: TextureMap}`` into the JSON payload
    expected by the Blender processing script for multi-material scenes.

    Each entry includes a ``"material"`` field identifying which Blender
    material slot it belongs to.

    Args:
        material_maps: Mapping of ``{material_name: TextureMap}``.

    Returns:
        List of dicts, each with ``channel``, ``path``, ``color_space``,
        ``is_glossiness``, and ``material`` fields.
    """
    payload: list[dict[str, Any]] = []
    for material_name, texture_map in material_maps.items():
        for channel, match in texture_map.as_dict().items():
            payload.append({
                "material": material_name,
                "channel": channel,
                "path": str(match.path.resolve()),
                "color_space": match.color_space,
                "is_glossiness": match.is_glossiness,
            })
    return payload
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_texture_matcher.py -v
```

Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/asset_agent/core/texture_matcher.py src/asset_agent/exporters/glb_exporter.py tests/test_texture_matcher.py
git commit -m "feat: add multi-material texture matching and payload builder"
```

---

### Task 6: Multi-material Blender material builder

**Files:**
- Modify: `src/asset_agent/blender_scripts/material_builder.py`

- [ ] **Step 1: Update `build_material()` to handle per-material texture groups**

The JSON payload now optionally includes a `"material"` key. When present, textures are grouped by material name and each Blender material slot is updated independently. When absent, single-material behavior is preserved.

Replace `build_material()` in `src/asset_agent/blender_scripts/material_builder.py`:

```python
def build_material(
    objects: list[bpy.types.Object],
    textures: list[dict[str, Any]],
    material_name: str = "PBR_Material",
) -> bpy.types.Material | None:
    """Create Principled BSDF material(s) and assign to objects.

    Single-material mode (no ``"material"`` field in textures):
        Creates one material named *material_name* and assigns it to all objects.

    Multi-material mode (some entries have a ``"material"`` field):
        For each material name in the payload, finds or creates a Blender material
        with that name and fills its node tree.  Existing material slot assignments
        on the imported objects are preserved.

    Args:
        objects: Mesh objects to receive material(s).
        textures: List of texture descriptors (see module docstring).
        material_name: Used as the material name in single-material mode.

    Returns:
        The created ``bpy.types.Material`` in single-material mode,
        or ``None`` in multi-material mode.
    """
    # Detect mode: if any entry has a "material" key, use multi-material path
    if any("material" in t for t in textures):
        _build_multi_materials(objects, textures)
        return None

    # Single-material path (original behaviour)
    mat = _build_single_material(material_name, textures)
    for obj in objects:
        obj.data.materials.clear()
        obj.data.materials.append(mat)
    log.info("Material '%s' assigned to %d object(s).", material_name, len(objects))
    return mat
```

Add `_build_single_material()` (extracts the existing node-building logic):

```python
def _build_single_material(
    material_name: str,
    textures: list[dict[str, Any]],
) -> bpy.types.Material:
    """Create and populate a new Principled BSDF material."""
    mat = bpy.data.materials.new(name=material_name)
    mat.use_nodes = True
    tree = mat.node_tree
    nodes = tree.nodes
    links = tree.links
    nodes.clear()

    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (_X_BSDF, 0)
    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (300, 0)
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    tex_by_channel: dict[str, dict[str, Any]] = {t["channel"]: t for t in textures}
    y_cursor = _Y_START

    # ORM unpacking
    orm_separate_node = None
    if "orm" in tex_by_channel:
        orm_info = tex_by_channel["orm"]
        orm_tex, y_cursor = _add_tex_image(nodes, orm_info, y_cursor)
        orm_separate_node = nodes.new("ShaderNodeSeparateColor")
        orm_separate_node.location = (_X_MID, y_cursor + _Y_STEP)
        y_cursor += _Y_STEP
        links.new(orm_tex.outputs["Color"], orm_separate_node.inputs["Color"])
        links.new(orm_separate_node.outputs["Green"], bsdf.inputs["Roughness"])
        links.new(orm_separate_node.outputs["Blue"], bsdf.inputs["Metallic"])

    for channel, connect_fn in _CHANNEL_WIRING.items():
        if channel == "orm":
            continue
        info = tex_by_channel.get(channel)
        if info is None:
            continue
        y_cursor = connect_fn(nodes, links, bsdf, info, y_cursor, orm_separate_node)

    if "opacity" in tex_by_channel:
        mat.blend_method = "HASHED"
        mat.shadow_method = "HASHED"

    return mat
```

Add `_build_multi_materials()`:

```python
def _build_multi_materials(
    objects: list[bpy.types.Object],
    textures: list[dict[str, Any]],
) -> None:
    """Update each Blender material slot that has a matching texture set.

    Groups textures by their ``"material"`` field, then for each group finds
    the existing Blender material with that name (created by OBJ import) and
    replaces its node tree with a full Principled BSDF setup.
    """
    # Group textures by material name
    by_mat: dict[str, list[dict[str, Any]]] = {}
    for t in textures:
        key = t.get("material", "")
        if key:
            by_mat.setdefault(key, []).append(t)

    for mat_name, mat_textures in by_mat.items():
        existing = bpy.data.materials.get(mat_name)
        if existing is not None:
            # Clear existing nodes and rebuild
            existing.use_nodes = True
            existing.node_tree.nodes.clear()
            # Re-build using _build_single_material logic inline:
            new_mat = _build_single_material(mat_name + "_pbr", mat_textures)
            # Copy node tree from new_mat into existing
            existing.node_tree.nodes.clear()
            existing.node_tree.links.clear()
            # Copy nodes
            node_map: dict[str, bpy.types.ShaderNode] = {}
            for src_node in new_mat.node_tree.nodes:
                dst_node = existing.node_tree.nodes.new(src_node.bl_idname)
                dst_node.location = src_node.location
                dst_node.label = src_node.label
                node_map[src_node.name] = dst_node
                # Copy inputs with default values
                for i, inp in enumerate(src_node.inputs):
                    if i < len(dst_node.inputs):
                        try:
                            dst_node.inputs[i].default_value = inp.default_value
                        except Exception:
                            pass
                # Copy image reference
                if src_node.type == "TEX_IMAGE" and src_node.image:
                    dst_node.image = src_node.image
            # Copy links
            for lnk in new_mat.node_tree.links:
                src_out_node = node_map.get(lnk.from_node.name)
                dst_in_node = node_map.get(lnk.to_node.name)
                if src_out_node and dst_in_node:
                    src_socket = src_out_node.outputs.get(lnk.from_socket.name)
                    dst_socket = dst_in_node.inputs.get(lnk.to_socket.name)
                    if src_socket and dst_socket:
                        existing.node_tree.links.new(src_socket, dst_socket)
            if "opacity" in {t["channel"] for t in mat_textures}:
                existing.blend_method = "HASHED"
                existing.shadow_method = "HASHED"
            # Remove the temporary material
            bpy.data.materials.remove(new_mat)
            log.info("Updated material '%s' with %d textures.", mat_name, len(mat_textures))
        else:
            log.warning("Material '%s' not found in scene; creating new.", mat_name)
            new_mat = _build_single_material(mat_name, mat_textures)
            for obj in objects:
                for slot in obj.material_slots:
                    if slot.material is None:
                        slot.material = new_mat
```

Remove the old inline `build_material()` body (the one that directly created the material and called `_CHANNEL_WIRING`); all that logic now lives in `_build_single_material()`.

- [ ] **Step 2: Run unit tests (blender_scripts are tested via e2e only)**

```bash
python -m pytest tests/test_texture_matcher.py tests/test_mtl_parser.py tests/test_blender_runner.py -v
```

Expected: all pass (no blender-side unit test needed — covered by e2e)

- [ ] **Step 3: Commit**

```bash
git add src/asset_agent/blender_scripts/material_builder.py
git commit -m "feat: multi-material support in Blender material builder"
```

---

### Task 7: Multi-material agent orchestration

**Files:**
- Modify: `src/asset_agent/agent.py`

- [ ] **Step 1: Update `process()` to detect and route multi-material OBJ**

In `src/asset_agent/agent.py`, update the texture-matching block (steps 2 and 3):

```python
        # 2. Match textures
        logger.info("[2/4] Matching textures in '%s'...", texture_dir)
        from asset_agent.core.mtl_parser import find_mtl_for_obj, parse_mtl
        from asset_agent.exporters.glb_exporter import build_multi_textures_payload

        mtl_path = find_mtl_for_obj(obj_path)
        mtl_data = parse_mtl(mtl_path) if mtl_path else {}
        is_multi = len(mtl_data) > 1

        if is_multi:
            material_names = list(mtl_data.keys())
            logger.info("  Multi-material OBJ detected: %d materials (%s)", len(material_names), ", ".join(material_names))
            matcher = create_matcher(model_name=model_name)
            material_maps = matcher.match_multi(
                texture_dir,
                material_names=material_names,
                obj_path=obj_path,
            )
            matched_count = sum(1 for tm in material_maps.values() if tm.albedo is not None)
            logger.info("  Matched %d/%d materials with albedo.", matched_count, len(material_names))
            textures_payload = build_multi_textures_payload(material_maps)
        else:
            try:
                texture_map = self.match_textures(texture_dir, model_name=model_name, obj_path=obj_path)
                logger.info("  Matched channels: %s", ", ".join(texture_map.channel_names) or "(none)")
            except MissingAlbedoError:
                logger.warning("  No albedo texture found — will keep imported MTL materials.")
                texture_map = TextureMap()
            textures_payload = build_textures_payload(texture_map.as_dict())
```

Add `create_matcher` to the imports at the top of `agent.py`:

```python
from asset_agent.core.texture_matcher import TextureMap, create_matcher
```

- [ ] **Step 2: Run all non-e2e tests**

```bash
python -m pytest tests/test_texture_matcher.py tests/test_mtl_parser.py tests/test_blender_runner.py -v
```

Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add src/asset_agent/agent.py
git commit -m "feat: route multi-material OBJ through match_multi and build_multi_textures_payload"
```

---

## GROUP C — FBX Importer

### Task 8: FBX import in blender_scripts

**Files:**
- Modify: `src/asset_agent/blender_scripts/utils.py`
- Modify: `src/asset_agent/blender_scripts/process_asset.py`

- [ ] **Step 1: Add `import_model()` to utils.py**

In `src/asset_agent/blender_scripts/utils.py`, replace `import_obj()` with `import_model()` (keep `import_obj` as an alias):

```python
def import_model(filepath: str) -> list[bpy.types.Object]:
    """Import a 3D model file and return newly created mesh objects.

    Supports ``.obj`` and ``.fbx`` formats.  The correct Blender import
    operator is selected based on the file extension.

    Args:
        filepath: Absolute path to the model file.

    Returns:
        List of imported mesh objects.

    Raises:
        RuntimeError: If no mesh objects are created, or format is unsupported.
    """
    ext = Path(filepath).suffix.lower()
    before = set(bpy.data.objects)

    if ext == ".obj":
        if bpy.app.version >= (3, 4, 0):
            bpy.ops.wm.obj_import(filepath=filepath)
        else:
            bpy.ops.import_scene.obj(filepath=filepath)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=filepath)
    else:
        raise RuntimeError(f"Unsupported model format: '{ext}'. Supported: .obj, .fbx")

    after = set(bpy.data.objects)
    new_objs = [o for o in (after - before) if o.type == "MESH"]

    if not new_objs:
        raise RuntimeError(f"No mesh objects imported from '{filepath}'.")

    log.info("Imported %d mesh(es) from '%s'.", len(new_objs), filepath)
    return new_objs


# Backward compatibility alias
def import_obj(filepath: str) -> list[bpy.types.Object]:
    return import_model(filepath)
```

- [ ] **Step 2: Update process_asset.py to use import_model**

In `src/asset_agent/blender_scripts/process_asset.py`:

Change the import line:
```python
    from utils import (  # type: ignore[import-not-found]
        clean_scene,
        export_glb,
        get_scene_bbox,
        import_model,          # <-- was import_obj
        setup_blender_logging,
        validate_glb,
    )
```

Change the usage line (Step 2/6):
```python
        mesh_objects = import_model(args.obj)    # <-- was import_obj
```

Also rename the CLI argument from `--obj` to `--model` with backward-compat alias:

```python
    parser.add_argument("--model", required=False, help="Path to the model file (.obj or .fbx).")
    parser.add_argument("--obj", dest="model", help=argparse.SUPPRESS)  # backward compat
```

Update `required=True` logic: add a post-parse check:
```python
    args = parser.parse_args(custom_args)
    # validate_only mode uses --obj dummy, so model may be "dummy"
    if not args.validate_only and not args.model:
        parser.error("--model (or --obj) is required")
    # Normalize attribute name
    if not hasattr(args, 'model') or args.model is None:
        args.model = getattr(args, 'obj', None)
```

Update all references to `args.obj` → `args.model` in `process_asset.py`.

- [ ] **Step 3: Commit**

```bash
git add src/asset_agent/blender_scripts/utils.py src/asset_agent/blender_scripts/process_asset.py
git commit -m "feat: add FBX import support in blender_scripts (import_model replaces import_obj)"
```

---

### Task 9: FBX importer host-side and CLI

**Files:**
- Modify: `src/asset_agent/importers/fbx_importer.py`
- Modify: `src/asset_agent/importers/base.py` (check for extension validation)
- Modify: `src/asset_agent/agent.py`
- Modify: `src/asset_agent/core/blender_runner.py`
- Modify: `src/asset_agent/cli.py`

- [ ] **Step 1: Implement FbxImporter**

Replace `src/asset_agent/importers/fbx_importer.py`:

```python
"""FBX file importer."""
from __future__ import annotations

from pathlib import Path

from asset_agent.exceptions import ImportError_
from asset_agent.importers.base import BaseImporter


class FbxImporter(BaseImporter):
    """Validate and prepare FBX files for import through Blender."""

    @property
    def supported_extensions(self) -> frozenset[str]:
        return frozenset({".fbx"})

    def validate_file(self, path: Path) -> None:
        """Check that *path* is an existing .fbx file.

        Raises:
            ImportError_: If the file doesn't exist or has wrong extension.
        """
        if not path.exists():
            raise ImportError_(str(path), "file does not exist")
        if path.suffix.lower() != ".fbx":
            raise ImportError_(str(path), f"expected .fbx, got '{path.suffix}'")

    def build_import_args(self, path: Path) -> list[str]:
        """Return CLI args for the Blender process script."""
        return ["--model", str(path.resolve())]
```

- [ ] **Step 2: Update agent.py to route by file extension**

In `src/asset_agent/agent.py`, update `__init__` and `process()`:

```python
    def __init__(self, config_path: Path | None = None) -> None:
        self.config: AppConfig = load_config(config_path)
        setup_logging(self.config.logging.level)
        # importers are chosen per-call based on file extension
```

In `process()`, replace the single `_obj_importer.validate_file()` call:

```python
        # 1. Validate input file
        logger.info("[1/4] Validating input file...")
        importer = self._get_importer(obj_path)
        importer.validate_file(obj_path)
```

Add the helper method:

```python
    @staticmethod
    def _get_importer(model_path: Path):
        """Return the appropriate importer for *model_path* based on extension."""
        from asset_agent.importers.fbx_importer import FbxImporter
        from asset_agent.importers.obj_importer import ObjImporter
        ext = model_path.suffix.lower()
        if ext == ".fbx":
            return FbxImporter()
        return ObjImporter()
```

- [ ] **Step 3: Update `run_process_asset` in blender_runner.py to accept `--model`**

In `src/asset_agent/core/blender_runner.py`, change `run_process_asset()` signature and args:

```python
def run_process_asset(
    obj_path: Path,          # accepts .obj or .fbx
    ...
) -> dict[str, Any]:
    ...
    args: list[str] = [
        "--model", str(obj_path.resolve()),    # <-- was "--obj"
        "--textures-json", json_str,
        ...
    ]
```

- [ ] **Step 4: Update CLI to accept .fbx in --obj / add --model**

In `src/asset_agent/cli.py`, update the `process` command's `--obj` parameter:

```python
@app.command()
def process(
    obj: Path = typer.Option(
        ..., "--obj",
        exists=True, dir_okay=False,
        help="Path to the model file (.obj or .fbx).",
    ),
    ...
```

The `typer.Option(exists=True)` already handles any extension — no code change needed beyond documentation. Optionally add a runtime check:

```python
    if obj.suffix.lower() not in {".obj", ".fbx"}:
        console.print(f"[red]Unsupported format: {obj.suffix}. Supported: .obj, .fbx[/red]")
        raise typer.Exit(code=1)
```

- [ ] **Step 5: Run non-e2e tests**

```bash
python -m pytest tests/ -v --ignore=tests/test_e2e.py
```

Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/asset_agent/importers/fbx_importer.py src/asset_agent/agent.py \
        src/asset_agent/core/blender_runner.py src/asset_agent/cli.py
git commit -m "feat: implement FBX importer and route by file extension in agent and CLI"
```

---

## GROUP D — P2 UX Improvements

### Task 10: Expose --samples and --resolution as CLI flags

**Files:**
- Modify: `src/asset_agent/cli.py`
- Modify: `src/asset_agent/agent.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_blender_runner.py`:

```python
def test_cli_samples_flag_overrides_config(tmp_path):
    """--samples CLI flag must override config.render.samples."""
    from typer.testing import CliRunner
    from asset_agent.cli import app

    runner = CliRunner()
    obj = tmp_path / "m.obj"
    obj.write_text("v 0 0 0\n")
    tex = tmp_path / "tex"
    tex.mkdir()
    (tex / "m_basecolor.png").write_bytes(b"stub")

    captured: dict = {}

    def fake_process(self, obj_path, texture_dir, output_dir, **kwargs):
        from asset_agent.agent import ProcessingResult
        captured["samples"] = self.config.render.samples
        return ProcessingResult(success=True)

    with patch("asset_agent.agent.AssetAgent.process", fake_process):
        result = runner.invoke(app, [
            "process",
            "--obj", str(obj),
            "--textures", str(tex),
            "--output", str(tmp_path / "out"),
            "--samples", "32",
        ])

    assert captured.get("samples") == 32, f"Expected 32, got {captured.get('samples')}"
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_blender_runner.py::test_cli_samples_flag_overrides_config -v
```

Expected: `FAILED` — `No such option: --samples`

- [ ] **Step 3: Add `--samples` and `--resolution` to CLI**

In `src/asset_agent/cli.py`, update the `process` command:

```python
@app.command()
def process(
    obj: Path = typer.Option(..., "--obj", exists=True, dir_okay=False, help="Path to the model file (.obj or .fbx)."),
    textures: Path = typer.Option(..., "--textures", exists=True, file_okay=False, help="Directory containing PBR textures."),
    output: Path = typer.Option(..., "--output", help="Output directory for GLB and preview PNG."),
    config: Optional[Path] = typer.Option(None, "--config", exists=True, dir_okay=False, help="Override config YAML."),
    model_name: Optional[str] = typer.Option(None, "--model-name", help="Base name for output files (defaults to OBJ stem)."),
    samples: Optional[int] = typer.Option(None, "--samples", help="Render sample count (overrides config)."),
    resolution: Optional[str] = typer.Option(None, "--resolution", help="Render resolution as WxH, e.g. 1920x1080 (overrides config)."),
) -> None:
    """Run the full processing pipeline: match textures, build material, render, export GLB."""
    setup_logging()

    agent = AssetAgent(config_path=config)

    if samples is not None:
        agent.config.render.samples = samples
    if resolution is not None:
        try:
            w, h = (int(x) for x in resolution.lower().split("x"))
            agent.config.render.resolution = [w, h]
        except ValueError:
            console.print(f"[red]Invalid --resolution format '{resolution}'. Expected WxH, e.g. 1920x1080[/red]")
            raise typer.Exit(code=1)

    result = agent.process(
        obj_path=obj,
        texture_dir=textures,
        output_dir=output,
        model_name=model_name,
    )
    ...
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_blender_runner.py -v
```

Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/asset_agent/cli.py
git commit -m "feat: expose --samples and --resolution CLI flags for render quality override"
```

---

### Task 11: Real-time Blender progress output

**Files:**
- Modify: `src/asset_agent/core/blender_runner.py`

Blender processing takes 20-60 seconds. Replace `subprocess.run()` with `subprocess.Popen` + line-by-line stdout forwarding so the user sees progress in real time.

- [ ] **Step 1: Replace `subprocess.run` with streaming `Popen` in `run_blender_script`**

In `src/asset_agent/core/blender_runner.py`, replace the entire `run_blender_script` function body:

```python
def run_blender_script(
    script_path: str | Path,
    args: list[str],
    *,
    blender_path: str = "blender",
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Launch Blender in background mode and execute a Python script.

    Streams stdout line-by-line so the user sees progress in real time.
    Collects the full stdout for JSON result extraction.

    Args:
        script_path: Path to the ``.py`` script Blender should run.
        args: Extra arguments forwarded after the ``--`` separator.
        blender_path: Blender executable (name or absolute path).
        timeout: Maximum wall-clock seconds before killing the process.

    Returns:
        Combined stdout from the Blender process.

    Raises:
        BlenderNotFoundError: Blender binary not found.
        BlenderExecutionError: Non-zero exit code.
        BlenderTimeoutError: Process exceeded *timeout*.
    """
    exe = find_blender(blender_path)

    cmd: list[str] = [
        exe,
        "--background",
        "--factory-startup",
        "--python", str(script_path),
        "--",
        *args,
    ]

    logger.info("Running: %s", " ".join(cmd[:6]) + " ...")
    logger.debug("Full command: %s", cmd)

    import threading
    import time

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise BlenderNotFoundError(exe) from exc

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def _read_stream(stream, lines: list[str], log_fn) -> None:
        for line in stream:
            line = line.rstrip("\n")
            lines.append(line)
            log_fn(line)

    stdout_thread = threading.Thread(
        target=_read_stream,
        args=(proc.stdout, stdout_lines, lambda l: logger.debug("[blender] %s", l)),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_read_stream,
        args=(proc.stderr, stderr_lines, lambda l: logger.debug("[blender:err] %s", l)),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise BlenderTimeoutError(timeout)

    stdout_thread.join()
    stderr_thread.join()

    returncode = proc.returncode
    stdout = "\n".join(stdout_lines)
    stderr = "\n".join(stderr_lines)

    if returncode not in (0, 2):
        raise BlenderExecutionError(stderr, returncode)

    logger.debug("Blender stdout:\n%s", stdout[-2000:])
    return stdout
```

Note: This replaces `subprocess.run` with `Popen` + threads. The `encoding` and `errors` parameters are still set correctly (fixing the GBK bug from Task 1). If Task 1 was already merged, the `encoding` setting here replaces that fix — ensure it is present.

- [ ] **Step 2: Run existing tests to ensure nothing broke**

```bash
python -m pytest tests/test_blender_runner.py tests/test_texture_matcher.py -v
```

Note: `test_subprocess_uses_utf8_encoding` will now fail because we use Popen, not `subprocess.run`. Update the test to check `Popen` instead:

In `tests/test_blender_runner.py`, update `test_subprocess_uses_utf8_encoding`:

```python
def test_subprocess_uses_utf8_encoding(tmp_path):
    """Blender subprocess must use UTF-8 encoding to avoid GBK decode errors on Windows."""
    import threading

    mock_proc = MagicMock()
    mock_proc.stdout = iter([""])
    mock_proc.stderr = iter([""])
    mock_proc.returncode = 0
    mock_proc.wait = MagicMock(return_value=0)

    with patch("asset_agent.core.blender_runner.subprocess.Popen", return_value=mock_proc) as mock_popen:
        with patch("asset_agent.core.blender_runner.find_blender", return_value="blender"):
            with patch("threading.Thread") as mock_thread:
                mock_thread.return_value.start = MagicMock()
                mock_thread.return_value.join = MagicMock()
                try:
                    run_blender_script(tmp_path / "dummy.py", [], blender_path="blender")
                except Exception:
                    pass

        call_kwargs = mock_popen.call_args[1]
        assert call_kwargs.get("encoding") == "utf-8"
        assert call_kwargs.get("errors") == "replace"
```

- [ ] **Step 3: Run tests again**

```bash
python -m pytest tests/test_blender_runner.py -v
```

Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add src/asset_agent/core/blender_runner.py tests/test_blender_runner.py
git commit -m "feat: stream Blender stdout in real time using Popen instead of subprocess.run"
```

---

### Task 12: batch subcommand

**Files:**
- Modify: `src/asset_agent/cli.py`
- Modify: `src/asset_agent/agent.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_blender_runner.py`:

```python
def test_batch_subcommand_exists():
    """The CLI must expose a 'batch' subcommand."""
    from typer.testing import CliRunner
    from asset_agent.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["batch", "--help"])
    assert result.exit_code == 0
    assert "input-dir" in result.output.lower() or "input_dir" in result.output.lower()
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_blender_runner.py::test_batch_subcommand_exists -v
```

Expected: `FAILED` — `No such command 'batch'`

- [ ] **Step 3: Add `batch_process()` to AssetAgent**

Append to `src/asset_agent/agent.py`:

```python
    def batch_process(
        self,
        input_dir: Path,
        output_dir: Path,
        *,
        texture_subdir: str = "textures",
        glob_patterns: list[str] | None = None,
    ) -> list[ProcessingResult]:
        """Discover and process all OBJ/FBX models in *input_dir*.

        Discovery rules (in order of priority):
        1. ``<model_stem>_textures/`` sibling directory
        2. ``<model_stem>/textures/`` subdirectory
        3. ``<model_stem>/`` directory (textures co-located with model)
        4. Same directory as the model file

        Args:
            input_dir: Root directory to search for model files.
            output_dir: Root output directory; each model gets a sub-folder.
            texture_subdir: Name of the texture sub-folder to look for.
            glob_patterns: Glob patterns for model files (default: ``["**/*.obj", "**/*.fbx"]``).

        Returns:
            List of ``ProcessingResult`` for each discovered model.
        """
        if glob_patterns is None:
            glob_patterns = ["**/*.obj", "**/*.fbx"]

        results: list[ProcessingResult] = []
        processed: set[Path] = set()

        for pattern in glob_patterns:
            for model_path in sorted(input_dir.glob(pattern)):
                if model_path in processed:
                    continue
                processed.add(model_path)

                texture_dir = self._discover_texture_dir(model_path, texture_subdir)
                model_name = model_path.stem
                model_output = output_dir / model_name

                logger.info("Batch: processing '%s'", model_name)
                try:
                    result = self.process(
                        obj_path=model_path,
                        texture_dir=texture_dir,
                        output_dir=model_output,
                        model_name=model_name,
                    )
                except Exception as exc:
                    logger.error("Batch: failed '%s': %s", model_name, exc)
                    result = ProcessingResult(success=False, errors=[str(exc)])
                results.append(result)

        return results

    @staticmethod
    def _discover_texture_dir(model_path: Path, texture_subdir: str) -> Path:
        """Find the best texture directory for *model_path*."""
        stem = model_path.stem
        parent = model_path.parent

        candidates = [
            parent / f"{stem}_textures",
            parent / f"{stem}" / texture_subdir,
            parent / f"{stem}",
            parent,
        ]

        for candidate in candidates:
            if candidate.is_dir():
                return candidate

        return parent
```

- [ ] **Step 4: Add `batch` CLI subcommand**

Append to `src/asset_agent/cli.py`:

```python
@app.command()
def batch(
    input_dir: Path = typer.Option(..., "--input-dir", exists=True, file_okay=False, help="Directory to scan for model files."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Root output directory (one sub-folder per model)."),
    config: Optional[Path] = typer.Option(None, "--config", exists=True, dir_okay=False, help="Override config YAML."),
    samples: Optional[int] = typer.Option(None, "--samples", help="Render sample count (overrides config)."),
    resolution: Optional[str] = typer.Option(None, "--resolution", help="Render resolution as WxH."),
) -> None:
    """Discover and batch-process all OBJ/FBX models in a directory."""
    setup_logging()

    agent = AssetAgent(config_path=config)
    if samples is not None:
        agent.config.render.samples = samples
    if resolution is not None:
        try:
            w, h = (int(x) for x in resolution.lower().split("x"))
            agent.config.render.resolution = [w, h]
        except ValueError:
            console.print(f"[red]Invalid --resolution '{resolution}'. Expected WxH.[/red]")
            raise typer.Exit(code=1)

    results = agent.batch_process(input_dir=input_dir, output_dir=output_dir)

    passed = sum(1 for r in results if r.success)
    failed = len(results) - passed

    console.print(f"\n[bold]Batch complete: {passed} passed, {failed} failed (total {len(results)})[/bold]")

    if failed:
        for i, r in enumerate(results):
            if not r.success:
                console.print(f"  [red]FAIL [{i}]: {r.errors}[/red]")
        raise typer.Exit(code=1)
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_blender_runner.py -v
```

Expected: all pass

- [ ] **Step 6: Final full test run**

```bash
python -m pytest tests/test_texture_matcher.py tests/test_mtl_parser.py tests/test_blender_runner.py -v
```

Expected: all pass

- [ ] **Step 7: Final commit**

```bash
git add src/asset_agent/agent.py src/asset_agent/cli.py
git commit -m "feat: add batch subcommand and AssetAgent.batch_process() for bulk processing"
```

---

## Post-Implementation Verification

- [ ] Run full unit test suite (no Blender needed):

```bash
cd D:\CodingProjects\BlenderTexAgent\3d-asset-agent
python -m pytest tests/test_texture_matcher.py tests/test_mtl_parser.py tests/test_blender_runner.py -v
```

- [ ] Run end-to-end tests (requires Blender):

```bash
python -m pytest tests/ -v
```

- [ ] Smoke-test with real assets:

```bash
python scripts/batch_agent_test.py
```

- [ ] Verify AudiRS6 no longer reports `success=False` (Bug 2 fixed)
- [ ] Verify no `UnicodeDecodeError` in PowerShell output (Bug 1 fixed)
- [ ] Verify Long House uses multiple materials (multi-material task)

---

## Self-Review

**Spec coverage:**
- ✅ Bug 1 (GBK encoding) — Task 1
- ✅ Bug 2 (validation false FAIL) — Task 2
- ✅ MTL parsing — Tasks 3-4
- ✅ Multi-material support — Tasks 5-7
- ✅ FBX importer — Tasks 8-9
- ✅ --samples / --resolution CLI — Task 10
- ✅ Progress feedback — Task 11
- ✅ Batch subcommand — Task 12

**Backward compatibility:** All changes are additive. Existing `--obj` CLI flag still works. Single-material mode is the default. `import_obj()` alias preserved. `TextureMap` structure unchanged.

**Note on Task 11 and Task 1 interaction:** Task 11 replaces `subprocess.run` with `Popen`. If Task 1 is implemented first and then Task 11 is implemented, the Task 1 change will be superseded — the `encoding` and `errors` parameters must also appear in the `Popen` call in Task 11. The plan includes this: the `Popen` call in Task 11 already sets `encoding="utf-8", errors="replace"`.
