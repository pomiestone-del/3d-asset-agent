"""Tests for multi-material detection and fallback logic in AssetAgent."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from asset_agent.agent import AssetAgent


class TestIsMultiDetection:
    """is_multi should only trigger when MTL has multiple materials with actual textures."""

    def _run_process_capturing_payload(self, tmp_path, mtl_content, texture_files=None):
        """Helper: write MTL + OBJ, mock Blender, capture textures_payload."""
        obj_path = tmp_path / "model.obj"
        mtl_path = tmp_path / "model.mtl"
        mtl_path.write_text(mtl_content, encoding="utf-8")
        obj_path.write_text(f"mtllib model.mtl\nv 0 0 0\n", encoding="utf-8")

        tex_dir = tmp_path / "textures"
        tex_dir.mkdir(exist_ok=True)
        for name in (texture_files or []):
            (tex_dir / name).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        captured = {}

        def fake_run(**kwargs):
            captured["textures_json"] = kwargs.get("textures_json", [])
            captured["skip_validation"] = kwargs.get("skip_validation", False)
            glb = tmp_path / "out" / "model.glb"
            glb.parent.mkdir(parents=True, exist_ok=True)
            glb.write_bytes(b"dummy")
            preview = tmp_path / "out" / "model_preview.png"
            preview.write_bytes(b"dummy")
            return {"status": "pass", "glb": str(glb), "preview": str(preview)}

        agent = AssetAgent()
        mock_importer = MagicMock()
        with patch("asset_agent.agent.run_process_asset", side_effect=fake_run):
            with patch.object(AssetAgent, "_get_importer", return_value=mock_importer):
                agent.process(obj_path, tex_dir, tmp_path / "out")

        return captured

    def test_color_only_mtl_does_not_trigger_multi(self, tmp_path):
        """MTL with 25 color-only materials (like AudiRS6) should NOT enter multi-material mode."""
        mtl = (
            "newmtl CARI_PAINT\nKd 0.8 0.13 0.11\nNs 225\n\n"
            "newmtl Glass\nKd 0.8 0.8 0.8\nNs 324\n\n"
            "newmtl Plastic\nKd 0.0 0.0 0.0\nNs 16\n"
        )
        captured = self._run_process_capturing_payload(tmp_path, mtl)
        # No textures found → empty payload, skip_validation True
        payload = captured["textures_json"]
        # Should NOT contain any "material" field entries (multi-material mode)
        assert not any("material" in entry for entry in payload), \
            "Color-only MTL should not produce multi-material payload"

    def test_mixed_mtl_with_one_textured_not_multi(self, tmp_path):
        """MTL with 2 materials but only 1 has textures → not multi-material."""
        tex_dir = tmp_path / "textures"
        tex_dir.mkdir(exist_ok=True)
        albedo = tex_dir / "thatch_bc.png"
        albedo.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        mtl = (
            "newmtl None\nKd 0.8 0.8 0.8\n\n"
            f"newmtl thatch\nmap_Kd {albedo}\n"
        )
        captured = self._run_process_capturing_payload(
            tmp_path, mtl, texture_files=[]
        )
        payload = captured["textures_json"]
        # Only 1 material with textures → single-material mode
        assert not any("material" in entry for entry in payload)

    def test_two_textured_materials_triggers_multi(self, tmp_path):
        """MTL with 2+ materials both having textures → multi-material mode."""
        tex_dir = tmp_path / "textures"
        tex_dir.mkdir(exist_ok=True)
        albedo_a = tex_dir / "matA_basecolor.png"
        albedo_b = tex_dir / "matB_basecolor.png"
        albedo_a.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        albedo_b.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        mtl = (
            f"newmtl matA\nmap_Kd {albedo_a}\n\n"
            f"newmtl matB\nmap_Kd {albedo_b}\n"
        )
        captured = self._run_process_capturing_payload(
            tmp_path, mtl, texture_files=[]
        )
        payload = captured["textures_json"]
        assert any("material" in entry for entry in payload), \
            "Two textured materials should produce multi-material payload"
