"""Batch-process all new test assets in AgentTest folder."""

from pathlib import Path
from asset_agent.agent import AssetAgent

INPUT = Path(r"C:\Users\Pomie\Downloads\AgentTest")
OUTPUT = INPUT / "output"

ASSETS = [
    # --- Previously tested (kept for regression) ---
    {
        "name": "CultistMonk",
        "model": INPUT / "uploads_files_6738595_DARK+FACELESS+CULTIST+MONK" / "8e19d0f983b433f374d4c94f13abc4d3.obj",
        "textures": INPUT / "uploads_files_6738595_DARK+FACELESS+CULTIST+MONK",
    },
    {
        "name": "LongHouse",
        "model": INPUT / "uploads_files_2366434_long_house_export" / "export" / "model" / "LOD0_long_house.obj",
        "textures": INPUT / "uploads_files_2366434_long_house_export" / "export" / "textures",
    },
    {
        "name": "AudiRS6",
        "model": INPUT / "AudiERS6Avant" / "uploads_files_2663635_Audi_RS_6_Avant.obj",
        "textures": INPUT / "AudiERS6Avant",
    },

    # --- New models ---
    {
        "name": "CactiSetCactus",
        "model": INPUT / "CactiSetCactus" / "uploads_files_1969587_Cactus1.obj",
        "textures": INPUT / "CactiSetCactus" / "uploads_files_1969587_Unity+HD+Render+Pipeline" / "Unity HD Render Pipeline",
    },
    {
        "name": "DiningTable",
        "model": INPUT / "DiningTableamir" / "uploads_files_5680349_Dining-Table-amir3design-2021-obj.obj",
        "textures": INPUT / "DiningTableamir" / "uploads_files_5680349_Texture_2k(1)" / "maps",
    },
    {
        "name": "PrehistoricMammoth",
        "model": INPUT / "PrehistoricMammoth" / "uploads_files_6758912_fbx.fbx",
        "textures": INPUT / "PrehistoricMammoth" / "uploads_files_6758912_textures" / "textures",
    },
    {
        "name": "SodaBottle",
        "model": INPUT / "SodaBottle3DModel" / "uploads_files_6701536_CG.fbx",
        "textures": INPUT / "SodaBottle3DModel" / "uploads_files_6701536_textures" / "textures",
    },
    {
        "name": "Butterfly",
        "model": INPUT / "uploads_files_1948108_Animated+Butterfly+Pack+By+Travis+Davids"
                / "Animated Butterfly Pack By Travis Davids"
                / "Textures And Butterfly Body"
                / "BASIC BUTTERFLY BODY_Travis_Davids.OBJ",
        "textures": INPUT / "uploads_files_1948108_Animated+Butterfly+Pack+By+Travis+Davids"
                   / "Animated Butterfly Pack By Travis Davids"
                   / "Textures And Butterfly Body",
    },
    {
        "name": "Apples",
        "model": INPUT / "uploads_files_3685626_FS-0011-Apples" / "FS-0011 Apples" / "FS-0011 Apples.fbx",
        "textures": INPUT / "uploads_files_3685626_FS-0011-Apples" / "FS-0011 Apples" / "textures",
    },
    {
        "name": "Model6936576",
        "model": INPUT / "uploads_files_6936576_model" / "978bcbdc4563d2411066f0e5fceb18b9.obj",
        "textures": INPUT / "uploads_files_6936576_model",
    },
    {
        "name": "FileFormat",
        "model": INPUT / "uploads_files_6912835_file+format" / "file format" / "fbx.fbx",
        "textures": INPUT / "uploads_files_6912835_file+format" / "file format",
    },

    # --- .blend files (materials embedded, textures may be internal) ---
    {
        "name": "IndustrialPipeLamp",
        "model": INPUT / "industrial_pipe_lamp_4k.blend" / "industrial_pipe_lamp_4k.blend",
        "textures": INPUT / "industrial_pipe_lamp_4k.blend" / "textures",
    },
    {
        "name": "JackDanielBottle",
        "model": INPUT / "uploads_files_2732038_Jack+Daniel+Bottle" / "uploads_files_2732038_Jack+Daniel+Bottle.blend",
        "textures": INPUT / "uploads_files_2732038_Jack+Daniel+Bottle",
    },
    {
        "name": "Emily",
        "model": INPUT / "uploads_files_6890527_Emily" / "Emily" / "Emily 5.0.blend",
        "textures": INPUT / "uploads_files_6890527_Emily" / "Emily" / "textures",
    },
]


def main() -> None:
    agent = AssetAgent()
    agent.config.render.resolution = [1920, 1080]
    agent.config.render.samples = 64
    agent.config.render.denoise = True
    agent.config.render.gpu_enabled = True

    results = []
    for asset in ASSETS:
        name = asset["name"]
        out_dir = OUTPUT / name
        print(f"\n{'='*60}")
        print(f"  Processing: {name}")
        print(f"  Model:    {asset['model']}")
        print(f"  Textures: {asset['textures']}")
        print(f"  Output:   {out_dir}")
        print(f"{'='*60}\n")

        try:
            result = agent.process(
                model_path=asset["model"],
                texture_dir=asset["textures"],
                output_dir=out_dir,
                model_name=name,
            )

            if result.success:
                print(f"\n  [OK] {name}")
                print(f"       GLB:     {result.glb_path}")
                print(f"       Preview: {result.preview_path}")
                results.append((name, "OK", ""))
            else:
                print(f"\n  [FAIL] {name}: {result.errors}")
                results.append((name, "FAIL", "; ".join(result.errors[:2])))
        except Exception as exc:
            print(f"\n  [ERROR] {name}: {exc}")
            results.append((name, "ERROR", str(exc)[:80]))

    print(f"\n{'='*60}")
    print(f"  Summary:")
    for name, status, err in results:
        tag = f"[{status}]"
        msg = f"  {tag:8s} {name}"
        if err:
            msg += f"  ({err[:60]})"
        print(msg)
    ok_count = sum(1 for _, s, _ in results if s == "OK")
    print(f"\n  {ok_count}/{len(results)} succeeded.  Output: {OUTPUT}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
