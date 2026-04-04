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
