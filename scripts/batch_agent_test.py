"""Batch-process the three test assets in AgentTest folder."""

from pathlib import Path
from asset_agent.agent import AssetAgent

INPUT = Path(r"C:\Users\Pomie\Downloads\AgentTest")
OUTPUT = INPUT / "output"

ASSETS = [
    {
        "name": "CultistMonk",
        "obj": INPUT / "uploads_files_6738595_DARK+FACELESS+CULTIST+MONK" / "8e19d0f983b433f374d4c94f13abc4d3.obj",
        "textures": INPUT / "uploads_files_6738595_DARK+FACELESS+CULTIST+MONK",
    },
    {
        "name": "LongHouse",
        "obj": INPUT / "uploads_files_2366434_long_house_export" / "export" / "model" / "LOD0_long_house.obj",
        "textures": INPUT / "uploads_files_2366434_long_house_export" / "export" / "textures",
    },
    {
        "name": "AudiRS6",
        "obj": INPUT / "AudiERS6Avant" / "uploads_files_2663635_Audi_RS_6_Avant.obj",
        "textures": INPUT / "AudiERS6Avant",
    },
]

def main() -> None:
    agent = AssetAgent()
    agent.config.render.resolution = [1920, 1080]
    agent.config.render.samples = 64
    agent.config.render.denoise = True
    agent.config.render.gpu_enabled = True

    for asset in ASSETS:
        name = asset["name"]
        out_dir = OUTPUT / name
        print(f"\n{'='*60}")
        print(f"  Processing: {name}")
        print(f"  OBJ:      {asset['obj']}")
        print(f"  Textures: {asset['textures']}")
        print(f"  Output:   {out_dir}")
        print(f"{'='*60}\n")

        result = agent.process(
            obj_path=asset["obj"],
            texture_dir=asset["textures"],
            output_dir=out_dir,
            model_name=name,
        )

        if result.success:
            print(f"\n  [OK] {name}")
            print(f"       GLB:     {result.glb_path}")
            print(f"       Preview: {result.preview_path}")
        else:
            print(f"\n  [FAIL] {name}: {result.errors}")

    print(f"\n{'='*60}")
    print(f"  All done. Output: {OUTPUT}")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
