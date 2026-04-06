"""CLI entry-point for the 3D asset processing agent.

Provides three sub-commands::

    asset-agent process  --model <path> --textures <dir> --output <dir>
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
    model: Path = typer.Option(
        ..., "--model",
        exists=True, dir_okay=False,
        help="Path to the 3D model file (.obj, .fbx, .blend, .gltf, .glb, .stl, etc.).",
    ),
    textures: Path = typer.Option(..., "--textures", exists=True, file_okay=False, help="Directory containing PBR textures."),
    output: Path = typer.Option(..., "--output", help="Output directory for GLB and preview PNG."),
    config: Optional[Path] = typer.Option(None, "--config", exists=True, dir_okay=False, help="Override config YAML."),
    model_name: Optional[str] = typer.Option(None, "--model-name", help="Base name for output files (defaults to model stem)."),
    samples: Optional[int] = typer.Option(None, "--samples", min=1, help="Render sample count (overrides config)."),
    resolution: Optional[str] = typer.Option(None, "--resolution", help="Render resolution as WxH, e.g. 1920x1080 (overrides config)."),
) -> None:
    """Run the full processing pipeline: match textures, build material, render, export GLB."""
    setup_logging()

    from asset_agent.importers.generic_importer import _ALL_SUPPORTED
    if model.suffix.lower() not in _ALL_SUPPORTED:
        console.print(f"[red]Unsupported format: {model.suffix}. Supported: {', '.join(sorted(_ALL_SUPPORTED))}[/red]")
        raise typer.Exit(code=1)

    agent = AssetAgent(config_path=config)

    if samples is not None:
        agent.config.render.samples = samples
    if resolution is not None:
        try:
            w, h = resolution.lower().split("x")
            agent.config.render.resolution = [int(w), int(h)]
        except (ValueError, TypeError):
            console.print(f"[red]Invalid resolution format: '{resolution}'. Use WxH, e.g. 1920x1080[/red]")
            raise typer.Exit(code=1)

    result = agent.process(
        model_path=model,
        texture_dir=textures,
        output_dir=output,
        model_name=model_name,
    )

    if result.success:
        console.print("\n[bold green]Pipeline completed successfully.[/bold green]")
        if result.glb_path:
            console.print(f"  GLB:          {result.glb_path}")
        if result.preview_before:
            console.print(f"  Before:       {result.preview_before}")
        if result.preview_after:
            console.print(f"  After:        {result.preview_after}")
        if result.preview_glb:
            console.print(f"  GLB preview:  {result.preview_glb}")
    else:
        console.print("\n[bold red]Pipeline finished with errors:[/bold red]")
        for err in result.errors:
            console.print(f"  [red]- {err}[/red]")
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# batch
# ---------------------------------------------------------------------------

@app.command()
def batch(
    input_dir: Path = typer.Option(..., "--input-dir", exists=True, file_okay=False, help="Root directory to scan for model files."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Base output directory (per-model subfolders created)."),
    config: Optional[Path] = typer.Option(None, "--config", exists=True, dir_okay=False, help="Override config YAML."),
    samples: Optional[int] = typer.Option(None, "--samples", min=1, help="Render sample count (overrides config)."),
    resolution: Optional[str] = typer.Option(None, "--resolution", help="Render resolution as WxH, e.g. 1920x1080 (overrides config)."),
) -> None:
    """Batch-process all supported 3D model files found under input-dir."""
    setup_logging()

    agent = AssetAgent(config_path=config)

    if samples is not None:
        agent.config.render.samples = samples
    if resolution is not None:
        try:
            w, h = resolution.lower().split("x")
            agent.config.render.resolution = [int(w), int(h)]
        except (ValueError, TypeError):
            console.print(f"[red]Invalid resolution format: '{resolution}'. Use WxH, e.g. 1920x1080[/red]")
            raise typer.Exit(code=1)

    results = agent.batch_process(input_dir, output_dir)

    if not results:
        console.print("[yellow]No model files found.[/yellow]")
        raise typer.Exit(code=0)

    # Summary table
    table = Table(title="Batch Results", show_lines=True)
    table.add_column("Model", style="cyan")
    table.add_column("Status", style="white")
    table.add_column("GLB", style="green")
    table.add_column("Errors", style="red")

    failures = 0
    for r in results:
        status = "[green]OK[/green]" if r.success else "[red]FAIL[/red]"
        glb = str(r.glb_path.name) if r.glb_path else "-"
        errs = "; ".join(r.errors[:2]) if r.errors else "-"
        name = r.glb_path.stem if r.glb_path else "(unknown)"
        table.add_row(name, status, glb, errs)
        if not r.success:
            failures += 1

    console.print()
    console.print(table)
    console.print(f"\n[bold]{len(results)} processed, {failures} failed.[/bold]")

    if failures:
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
