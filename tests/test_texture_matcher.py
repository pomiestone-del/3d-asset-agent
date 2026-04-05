"""Tests for the PBR texture matching engine.

Covers:
  - Every channel type (albedo, normal, roughness, metallic, ao, emissive,
    opacity, displacement, orm)
  - Glossiness detection and flag
  - Conflict disambiguation (model-name hint, format priority)
  - Missing-albedo error
  - Missing optional channel warnings
  - Recursive subdirectory scanning
  - Case-insensitivity
  - Multiple naming conventions per channel
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from asset_agent.core.texture_matcher import (
    TextureMap,
    TextureMatch,
    TextureMatcher,
    create_matcher,
    load_channel_rules,
    load_format_priority,
)
from asset_agent.exceptions import MissingAlbedoError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_png(path: Path) -> None:
    """Write a minimal 1x1 RGB PNG so the file is a valid image."""
    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = 1, 1
    raw = b"\x00\x80\x80\x80"

    def chunk(ctype: bytes, data: bytes) -> bytes:
        c = ctype + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(raw)
    path.write_bytes(sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


def _make_textures(base: Path, names: list[str]) -> list[Path]:
    """Create minimal PNGs (or other formats) from a list of file names."""
    paths: list[Path] = []
    for name in names:
        p = base / name
        _make_png(p)
        paths.append(p)
    return paths


@pytest.fixture()
def matcher() -> TextureMatcher:
    """Return a matcher loaded from the project's default config."""
    return create_matcher()


@pytest.fixture()
def rules() -> list:
    return load_channel_rules()


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

class TestConfigLoading:
    def test_load_rules_returns_all_channels(self, rules: list) -> None:
        names = [r.name for r in rules]
        expected = [
            "albedo", "normal", "roughness", "metallic",
            "ao", "emissive", "opacity", "displacement", "orm",
        ]
        assert names == expected

    def test_albedo_is_required(self, rules: list) -> None:
        albedo = next(r for r in rules if r.name == "albedo")
        assert albedo.required is True

    def test_roughness_has_glossiness_keywords(self, rules: list) -> None:
        roughness = next(r for r in rules if r.name == "roughness")
        assert "gloss" in roughness.glossiness_keywords

    def test_load_format_priority(self) -> None:
        priority = load_format_priority()
        assert priority[0] == ".png"
        assert ".bmp" in priority


# ---------------------------------------------------------------------------
# Basic single-channel matching
# ---------------------------------------------------------------------------

class TestAlbedoMatching:
    @pytest.mark.parametrize("stem", [
        "Cube_BaseColor", "cube_base_color", "model_base-color",
        "Albedo", "wall_diffuse", "skin_diff", "color", "col",
    ])
    def test_albedo_variants(self, tmp_path: Path, matcher: TextureMatcher, stem: str) -> None:
        _make_textures(tmp_path, [f"{stem}.png"])
        result = matcher.match(tmp_path)
        assert result.albedo is not None
        assert result.albedo.channel == "albedo"
        assert result.albedo.color_space == "sRGB"

    def test_albedo_not_matched_for_ao_filename(self, tmp_path: Path, matcher: TextureMatcher) -> None:
        """File with 'color' in name but also 'ao' should NOT match albedo."""
        _make_textures(tmp_path, ["wall_color_ao.png", "wall_basecolor.png"])
        result = matcher.match(tmp_path)
        assert result.albedo is not None
        assert "ao" not in result.albedo.path.stem.lower() or "basecolor" in result.albedo.path.stem.lower()


class TestNormalMatching:
    @pytest.mark.parametrize("stem", [
        "Cube_Normal", "wall_nrm", "brick_norm", "tile_nor", "skin_nml",
    ])
    def test_normal_variants(self, tmp_path: Path, matcher: TextureMatcher, stem: str) -> None:
        _make_textures(tmp_path, ["dummy_basecolor.png", f"{stem}.png"])
        result = matcher.match(tmp_path)
        assert result.normal is not None
        assert result.normal.color_space == "Non-Color"


class TestRoughnessMatching:
    @pytest.mark.parametrize("stem", [
        "Cube_Roughness", "wall_rough", "brick_rgh",
    ])
    def test_roughness_variants(self, tmp_path: Path, matcher: TextureMatcher, stem: str) -> None:
        _make_textures(tmp_path, ["dummy_basecolor.png", f"{stem}.png"])
        result = matcher.match(tmp_path)
        assert result.roughness is not None
        assert result.roughness.is_glossiness is False

    @pytest.mark.parametrize("stem", ["Cube_Gloss", "wall_glossiness"])
    def test_glossiness_detection(self, tmp_path: Path, matcher: TextureMatcher, stem: str) -> None:
        _make_textures(tmp_path, ["dummy_basecolor.png", f"{stem}.png"])
        result = matcher.match(tmp_path)
        assert result.roughness is not None
        assert result.roughness.is_glossiness is True


class TestMetallicMatching:
    @pytest.mark.parametrize("stem", [
        "Cube_Metallic", "wall_metalness", "brick_metal", "tile_met",
    ])
    def test_metallic_variants(self, tmp_path: Path, matcher: TextureMatcher, stem: str) -> None:
        _make_textures(tmp_path, ["dummy_basecolor.png", f"{stem}.png"])
        result = matcher.match(tmp_path)
        assert result.metallic is not None
        assert result.metallic.color_space == "Non-Color"

    def test_metallic_not_matched_by_method(self, tmp_path: Path, matcher: TextureMatcher) -> None:
        """'method' contains 'met' but is followed by 'h' — should NOT match."""
        _make_textures(tmp_path, ["dummy_basecolor.png", "method_guide.png"])
        result = matcher.match(tmp_path)
        assert result.metallic is None


class TestAOMatching:
    @pytest.mark.parametrize("stem", [
        "Cube_AO", "wall_occlusion", "brick_ambientocclusion",
        "tile_ambient_occlusion", "skin_occ",
    ])
    def test_ao_variants(self, tmp_path: Path, matcher: TextureMatcher, stem: str) -> None:
        _make_textures(tmp_path, ["dummy_basecolor.png", f"{stem}.png"])
        result = matcher.match(tmp_path)
        assert result.ao is not None


class TestEmissiveMatching:
    @pytest.mark.parametrize("stem", [
        "Cube_Emissive", "wall_emission", "brick_emit",
        "light_glow", "neon_selfillum", "neon_self_illum",
    ])
    def test_emissive_variants(self, tmp_path: Path, matcher: TextureMatcher, stem: str) -> None:
        _make_textures(tmp_path, ["dummy_basecolor.png", f"{stem}.png"])
        result = matcher.match(tmp_path)
        assert result.emissive is not None
        assert result.emissive.color_space == "sRGB"


class TestOpacityMatching:
    @pytest.mark.parametrize("stem", [
        "Cube_Opacity", "wall_alpha", "glass_transparency",
        "leaf_trans", "fence_mask",
    ])
    def test_opacity_variants(self, tmp_path: Path, matcher: TextureMatcher, stem: str) -> None:
        _make_textures(tmp_path, ["dummy_basecolor.png", f"{stem}.png"])
        result = matcher.match(tmp_path)
        assert result.opacity is not None


class TestDisplacementMatching:
    @pytest.mark.parametrize("stem", [
        "Cube_Displacement", "wall_disp", "brick_height", "tile_hgt",
    ])
    def test_displacement_variants(self, tmp_path: Path, matcher: TextureMatcher, stem: str) -> None:
        _make_textures(tmp_path, ["dummy_basecolor.png", f"{stem}.png"])
        result = matcher.match(tmp_path)
        assert result.displacement is not None

    def test_height_normal_not_matched_as_displacement(
        self, tmp_path: Path, matcher: TextureMatcher
    ) -> None:
        """A file named 'height_normal' should NOT match displacement due to negative lookahead."""
        _make_textures(tmp_path, ["dummy_basecolor.png", "height_normal.png"])
        result = matcher.match(tmp_path)
        assert result.displacement is None


class TestORMMatching:
    @pytest.mark.parametrize("stem", [
        "Cube_ORM", "wall_packed", "brick_ARM", "tile_RMA",
    ])
    def test_orm_variants(self, tmp_path: Path, matcher: TextureMatcher, stem: str) -> None:
        _make_textures(tmp_path, ["dummy_basecolor.png", f"{stem}.png"])
        result = matcher.match(tmp_path)
        assert result.orm is not None

    def test_orm_not_matched_by_form(self, tmp_path: Path, matcher: TextureMatcher) -> None:
        """'form' contains 'orm' but has a preceding letter — should NOT match."""
        _make_textures(tmp_path, ["dummy_basecolor.png", "form_data.png"])
        result = matcher.match(tmp_path)
        assert result.orm is None


# ---------------------------------------------------------------------------
# Full texture set
# ---------------------------------------------------------------------------

class TestFullTextureSet:
    def test_fixture_textures(self, matcher: TextureMatcher) -> None:
        """Match the checked-in fixture textures for the Cube model."""
        tex_dir = Path(__file__).parent / "fixtures" / "textures"
        result = matcher.match(tex_dir)
        assert result.albedo is not None
        assert result.normal is not None
        assert result.roughness is not None
        assert result.metallic is not None
        assert "Cube_BaseColor" in result.albedo.path.stem

    def test_all_channels_populated(self, tmp_path: Path, matcher: TextureMatcher) -> None:
        _make_textures(tmp_path, [
            "Model_BaseColor.png",
            "Model_Normal.png",
            "Model_Roughness.png",
            "Model_Metallic.png",
            "Model_AO.png",
            "Model_Emissive.png",
            "Model_Opacity.png",
            "Model_Displacement.png",
        ])
        result = matcher.match(tmp_path)
        for ch in ["albedo", "normal", "roughness", "metallic",
                    "ao", "emissive", "opacity", "displacement"]:
            assert getattr(result, ch) is not None, f"Channel '{ch}' should be populated"

    def test_channel_names_property(self, tmp_path: Path, matcher: TextureMatcher) -> None:
        _make_textures(tmp_path, [
            "diffuse.png", "normal.png", "roughness.png",
        ])
        result = matcher.match(tmp_path)
        names = result.channel_names
        assert "albedo" in names
        assert "normal" in names
        assert "roughness" in names

    def test_as_dict(self, tmp_path: Path, matcher: TextureMatcher) -> None:
        _make_textures(tmp_path, ["albedo.png", "normal.png"])
        result = matcher.match(tmp_path)
        d = result.as_dict()
        assert "albedo" in d
        assert d["albedo"].path.name == "albedo.png"


# ---------------------------------------------------------------------------
# Disambiguation
# ---------------------------------------------------------------------------

class TestDisambiguation:
    def test_model_name_hint_wins(self, tmp_path: Path) -> None:
        """When model_name matches one candidate, it should be preferred."""
        _make_textures(tmp_path, [
            "Sword_BaseColor.png",
            "Shield_BaseColor.png",
        ])
        m = create_matcher(model_name="Sword")
        result = m.match(tmp_path)
        assert result.albedo is not None
        assert "Sword" in result.albedo.path.stem

    def test_format_priority_png_over_jpg(self, tmp_path: Path) -> None:
        _make_textures(tmp_path, [
            "wall_basecolor.jpg",
            "wall_basecolor.png",
        ])
        m = create_matcher()
        result = m.match(tmp_path)
        assert result.albedo is not None
        assert result.albedo.path.suffix == ".png"

    def test_format_priority_exr_over_tga(self, tmp_path: Path) -> None:
        _make_textures(tmp_path, [
            "wall_normal.tga",
            "wall_normal.exr",
        ])
        m = create_matcher()
        _make_textures(tmp_path, ["dummy_basecolor.png"])
        result = m.match(tmp_path)
        assert result.normal is not None
        assert result.normal.path.suffix == ".exr"

    def test_model_name_takes_precedence_over_format(self, tmp_path: Path) -> None:
        _make_textures(tmp_path, [
            "Hero_BaseColor.jpg",
            "Enemy_BaseColor.png",
        ])
        m = create_matcher(model_name="Hero")
        result = m.match(tmp_path)
        assert result.albedo is not None
        assert "Hero" in result.albedo.path.stem


# ---------------------------------------------------------------------------
# Missing textures / error handling
# ---------------------------------------------------------------------------

class TestMissingTextures:
    def test_missing_albedo_raises(self, tmp_path: Path, matcher: TextureMatcher) -> None:
        _make_textures(tmp_path, ["wall_normal.png", "wall_roughness.png"])
        with pytest.raises(MissingAlbedoError):
            matcher.match(tmp_path)

    def test_empty_directory_raises(self, tmp_path: Path, matcher: TextureMatcher) -> None:
        with pytest.raises(MissingAlbedoError):
            matcher.match(tmp_path)

    def test_missing_optional_channels_still_succeeds(
        self, tmp_path: Path, matcher: TextureMatcher
    ) -> None:
        _make_textures(tmp_path, ["wood_albedo.png"])
        result = matcher.match(tmp_path)
        assert result.albedo is not None
        assert result.normal is None
        assert result.roughness is None
        assert result.metallic is None


# ---------------------------------------------------------------------------
# Case insensitivity
# ---------------------------------------------------------------------------

class TestCaseInsensitivity:
    @pytest.mark.parametrize("stem", [
        "CUBE_BASECOLOR", "cube_basecolor", "Cube_Basecolor", "cUbE_bAsEcOlOr",
    ])
    def test_case_variants(self, tmp_path: Path, matcher: TextureMatcher, stem: str) -> None:
        _make_textures(tmp_path, [f"{stem}.png"])
        result = matcher.match(tmp_path)
        assert result.albedo is not None


# ---------------------------------------------------------------------------
# Subdirectory scanning
# ---------------------------------------------------------------------------

class TestRecursiveScan:
    def test_finds_textures_in_subdirectory(self, tmp_path: Path, matcher: TextureMatcher) -> None:
        sub = tmp_path / "4K"
        _make_textures(sub, ["wall_basecolor.png", "wall_normal.png"])
        result = matcher.match(tmp_path, recursive=True)
        assert result.albedo is not None
        assert result.normal is not None

    def test_non_recursive_ignores_subdirs(self, tmp_path: Path, matcher: TextureMatcher) -> None:
        _make_textures(tmp_path, ["wall_basecolor.png"])
        sub = tmp_path / "extra"
        _make_textures(sub, ["wall_normal.png"])
        result = matcher.match(tmp_path, recursive=False)
        assert result.albedo is not None
        assert result.normal is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_non_image_files_are_ignored(self, tmp_path: Path, matcher: TextureMatcher) -> None:
        _make_textures(tmp_path, ["wall_basecolor.png"])
        (tmp_path / "readme.txt").write_text("not an image")
        (tmp_path / "notes.md").write_text("not an image")
        result = matcher.match(tmp_path)
        assert result.albedo is not None

    def test_first_match_wins_across_channels(self, tmp_path: Path, matcher: TextureMatcher) -> None:
        """A file that matches albedo should not also appear in another channel."""
        _make_textures(tmp_path, [
            "wall_diffuse_color.png",
            "wall_normal.png",
        ])
        result = matcher.match(tmp_path)
        assert result.albedo is not None
        if result.normal is not None:
            assert result.normal.path != result.albedo.path

    def test_orm_does_not_consume_individual_channels(
        self, tmp_path: Path, matcher: TextureMatcher
    ) -> None:
        """ORM and separate roughness/metallic should coexist."""
        _make_textures(tmp_path, [
            "wall_basecolor.png",
            "wall_ORM.png",
            "wall_roughness.png",
            "wall_metallic.png",
        ])
        result = matcher.match(tmp_path)
        assert result.orm is not None
        assert result.roughness is not None
        assert result.metallic is not None


class TestMtlIntegration:
    """TextureMatcher should use MTL-declared paths before falling back to regex."""

    def test_mtl_declared_albedo_used_when_regex_fails(self, tmp_path):
        """If regex can't find albedo but MTL has map_Kd, use the MTL path."""
        tex = tmp_path / "weird_name_xyz.png"
        tex.write_bytes(b"stub")

        mtl = tmp_path / "model.mtl"
        mtl.write_text(f"newmtl Mat\nmap_Kd {tex}\n", encoding="utf-8")

        obj = tmp_path / "model.obj"
        obj.write_text("mtllib model.mtl\nv 0 0 0\n", encoding="utf-8")

        matcher = create_matcher(model_name="model")
        result = matcher.match(tmp_path, obj_path=obj)
        assert result.albedo is not None
        assert result.albedo.path == tex

    def test_regex_channels_fill_gaps_not_in_mtl(self, tmp_path):
        """Channels absent from MTL should still be matched by regex."""
        albedo = tmp_path / "model_diffuse.png"
        normal = tmp_path / "model_normal.png"
        albedo.write_bytes(b"stub")
        normal.write_bytes(b"stub")

        mtl = tmp_path / "model.mtl"
        mtl.write_text(f"newmtl Mat\nmap_Kd {albedo}\n", encoding="utf-8")

        obj = tmp_path / "model.obj"
        obj.write_text("mtllib model.mtl\nv 0 0 0\n", encoding="utf-8")

        matcher = create_matcher(model_name="model")
        result = matcher.match(tmp_path, obj_path=obj)

        assert result.albedo is not None
        assert result.normal is not None


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
        (tmp_path / "mat_albedo.png").write_bytes(b"stub")

        matcher = create_matcher()
        result = matcher.match_multi(tmp_path, material_names=["mat"])
        assert isinstance(result["mat"], TextureMap)

    def test_match_multi_missing_albedo_yields_empty_map(self, tmp_path):
        matcher = create_matcher()
        result = matcher.match_multi(tmp_path, material_names=["unknown"])
        assert result["unknown"].albedo is None


class TestBuildMultiTexturesPayload:
    """build_multi_textures_payload() encodes a material field in each entry."""

    def test_payload_includes_material_field(self, tmp_path):
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
        from asset_agent.exporters.glb_exporter import build_textures_payload

        tex = tmp_path / "blade_albedo.png"
        tex.write_bytes(b"stub")
        tm = TextureMap(albedo=TextureMatch(path=tex, channel="albedo", color_space="sRGB"))
        payload = build_textures_payload(tm.as_dict())
        assert all("material" not in entry for entry in payload)


class TestDirectoryAffinity:
    """When textures span multiple subdirectories, prefer co-located textures."""

    def test_prefers_same_directory_as_albedo(self, tmp_path):
        """Normal/roughness from albedo's dir should win over other dirs."""
        # planks/ has albedo + roughness
        _make_textures(tmp_path, [
            "planks/boards_and_planks_bc.png",
            "planks/boards_and_planks_roughness.png",
        ])
        # boards/ has roughness only
        _make_textures(tmp_path, [
            "boards/tiled_dirty_planks_roughness.png",
        ])
        # thatch/ has an unrelated opacity mask
        _make_textures(tmp_path, [
            "thatch/thatch_opacity_mask.png",
        ])

        matcher = create_matcher()
        result = matcher.match(tmp_path)

        assert result.albedo is not None
        assert result.albedo.path.parent.name == "planks"
        # Roughness should come from same dir as albedo
        assert result.roughness is not None
        assert result.roughness.path.parent.name == "planks"

    def test_skips_cross_dir_when_albedo_in_subdir(self, tmp_path):
        """Single candidate from a different subdir should be skipped — it belongs to another material."""
        _make_textures(tmp_path, [
            "planks/boards_bc.png",
            "other/boards_normal.png",
        ])
        matcher = create_matcher()
        result = matcher.match(tmp_path)

        assert result.albedo is not None
        assert result.albedo.path.parent.name == "planks"
        # Normal is in a different subdir → skipped
        assert result.normal is None

    def test_no_affinity_when_albedo_in_root(self, tmp_path):
        """When albedo is in the root scan dir, no filtering — all candidates valid."""
        _make_textures(tmp_path, [
            "model_bc.png",
            "subdir/model_normal.png",
        ])
        matcher = create_matcher()
        result = matcher.match(tmp_path)

        assert result.albedo is not None
        assert result.normal is not None
        assert result.normal.path.parent.name == "subdir"

    def test_opacity_not_mixed_from_different_dir(self, tmp_path):
        """Opacity mask from a different directory should not be picked when co-located candidates exist."""
        _make_textures(tmp_path, [
            "planks/boards_and_planks_bc.png",
            "planks/boards_and_planks_roughness.png",
            "planks/boards_and_planks_opacity.png",
            "thatch/thatch_opacity_mask.png",
        ])
        matcher = create_matcher()
        result = matcher.match(tmp_path)

        assert result.opacity is not None
        assert result.opacity.path.parent.name == "planks"
