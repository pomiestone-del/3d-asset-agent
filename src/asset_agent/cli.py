"""CLI entry-point for the 3D asset processing agent.

Provides three sub-commands::

    asset-agent process  --obj <path> --textures <dir> --output <dir>
    asset-agent match    --textures <dir> [--model-name <name>]
    asset-agent validate --glb <path>
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from asset_agent.agent import AssetAgent
from asset_agent.utils.logging import setup_logging

app = typer.Typer(
    name="asset-agent",
    help="Automated 3D asset processing — PBR texture matching, Blender material setup, and GLB export.",
    add_completion=False,
    no_args_is_help=True,
)

console = Console()


# ---------------------------------------------------------------------------
# process
# ---------------------------------------------------------------------------

@app.command()
def process(
    obj: Path = typer.Option(
        ..., "--obj",
        exists=True, dir_okay=False,
        help="Path to the model file (.obj or .fbx).",
    ),
    textures: Path = typer.Option(..., "--textures", exists=True, file_okay=False, help="Directory containing PBR textures."),
    output: Path = typer.Option(..., "--output", help="Output directory for GLB and preview PNG."),
    config: Optional[Path] = typer.Option(None, "--config", exists=True, dir_okay=False, help="Override config YAML."),
    model_name: Optional[str] = typer.Option(None, "--model-name", help="Base name for output files (defaults to OBJ stem)."),
) -> None:
    """Run the full processing pipeline: match textures, build material, render, export GLB."""
    setup_logging()

    if obj.suffix.lower() not in {".obj", ".fbx"}:
        console.print(f"[red]Unsupported format: {obj.suffix}. Supported: .obj, .fbx[/red]")
        raise typer.Exit(code=1)

    agent = AssetAgent(config_path=config)
    result = agent.process(
        obj_path=obj,
        texture_dir=textures,
        output_dir=output,
        model_name=model_name,
    )

    if result.success:
        console.print("\n[bold green]Pipeline completed successfully.[/bold green]")
        if result.glb_path:
            console.print(f"  GLB:     {result.glb_path}")
        if result.preview_path:
            console.print(f"  Preview: {result.preview_path}")
    else:
        console.print("\n[bold red]Pipeline finished with errors:[/bold red]")
        for err in result.errors:
            console.print(f"  [red]- {err}[/red]")
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# match
# ---------------------------------------------------------------------------

@app.command()
def match(
    textures: Path = typer.Option(..., "--textures", exists=True, file_okay=False, help="Directory containing PBR textures."),
    model_name: Optional[str] = typer.Option(None, "--model-name", help="Model name hint for disambiguation."),
    config: Optional[Path] = typer.Option(None, "--config", exists=True, dir_okay=False, help="Override config YAML."),
) -> None:
    """Scan a texture directory and display matched PBR channels (debug helper)."""
    setup_logging()

    agent = AssetAgent(config_path=config)
    texture_map = agent.match_textures(textures, model_name=model_name)

    table = Table(title="Texture Matching Results", show_lines=True)
    table.add_column("Channel", style="cyan", min_width=14)
    table.add_column("File", style="white")
    table.add_column("Color Space", style="green")
    table.add_column("Notes", style="yellow")

    for channel, tm in texture_map.as_dict().items():
        notes = ""
        if tm.is_glossiness:
            notes = "glossiness (will be inverted)"
        table.add_row(channel, tm.path.name, tm.color_space, notes)

    console.print()
    console.print(table)

    missing = []
    for name in ["albedo", "normal", "roughness", "metallic"]:
        if getattr(texture_map, name) is None:
            missing.append(name)
    if missing:
        console.print(f"\n[yellow]Missing optional channels: {', '.join(missing)}[/yellow]")


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

@app.command()
def validate(
    glb: Path = typer.Option(..., "--glb", exists=True, dir_okay=False, help="Path to the .glb file to validate."),
    config: Optional[Path] = typer.Option(None, "--config", exists=True, dir_okay=False, help="Override config YAML."),
) -> None:
    """Validate a GLB file by re-importing in a clean Blender scene."""
    setup_logging()

    agent = AssetAgent(config_path=config)
    result = agent.validate(glb)

    if result.passed:
        console.print("\n[bold green]GLB validation passed.[/bold green]")
    else:
        console.print("\n[bold red]GLB validation failed:[/bold red]")
        for err in result.errors:
            console.print(f"  [red]- {err}[/red]")
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
