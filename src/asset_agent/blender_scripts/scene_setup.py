"""Three-point lighting, camera auto-framing, and render settings.

Runs inside Blender's embedded Python.  Do NOT import asset_agent modules.
Only stdlib + bpy are available.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import bpy  # type: ignore[import-unresolved]
from mathutils import Vector  # type: ignore[import-unresolved]

log = logging.getLogger("blender_scripts.scene_setup")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def setup_scene(
    center: Vector,
    diagonal: float,
    *,
    engine: str = "CYCLES",
    resolution: tuple[int, int] = (1920, 1080),
    samples: int = 128,
    denoise: bool = True,
    film_transparent: bool = True,
    gpu_enabled: bool = True,
) -> None:
    """Configure lighting, camera, and render settings for preview rendering.

    Args:
        center: World-space center of the model's bounding box.
        diagonal: Bounding-box diagonal length (used to scale distances).
        engine: ``"CYCLES"`` or ``"EEVEE"``.
        resolution: ``(width, height)`` in pixels.
        samples: Render sample count.
        denoise: Enable built-in denoiser.
        film_transparent: Transparent background (alpha).
        gpu_enabled: Prefer GPU compute for Cycles.
    """
    _setup_lights(center, diagonal)
    _setup_camera(center, diagonal)
    _setup_render(
        engine=engine,
        resolution=resolution,
        samples=samples,
        denoise=denoise,
        film_transparent=film_transparent,
        gpu_enabled=gpu_enabled,
    )
    log.info("Scene setup complete.")


# ---------------------------------------------------------------------------
# Lighting – three-point rig
# ---------------------------------------------------------------------------

def _add_area_light(
    name: str,
    location: tuple[float, float, float],
    energy: float,
    size: float,
    target: Vector,
) -> bpy.types.Object:
    """Create an AREA light aimed at *target*."""
    bpy.ops.object.light_add(type="AREA", location=location)
    light_obj = bpy.context.active_object
    light_obj.name = name
    light_obj.data.name = name
    light_obj.data.energy = energy
    light_obj.data.size = size

    direction = (target - Vector(location)).normalized()
    light_obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    return light_obj


def _setup_lights(center: Vector, diagonal: float) -> None:
    """Create a Key / Fill / Rim three-point lighting rig.

    Light energy scales with ``diagonal ** 2`` to compensate for inverse-square
    distance falloff, ensuring consistent illumination regardless of model size.
    """
    dist = diagonal * 1.5
    size = diagonal * 0.6

    # Energy scales with diagonal² to compensate for inverse-square falloff.
    # Factors calibrated so diagonal=2 reproduces original linear formula.
    d2 = diagonal * diagonal

    # Key light – upper-right-front
    _add_area_light(
        "Key",
        location=(center.x + dist, center.y - dist, center.z + dist * 0.8),
        energy=d2 * 75,
        size=size,
        target=center,
    )

    # Fill light – left, lower intensity
    _add_area_light(
        "Fill",
        location=(center.x - dist * 0.8, center.y - dist * 0.6, center.z + dist * 0.3),
        energy=d2 * 25,
        size=size * 1.2,
        target=center,
    )

    # Rim / back light – behind and above
    _add_area_light(
        "Rim",
        location=(center.x, center.y + dist, center.z + dist * 0.6),
        energy=d2 * 50,
        size=size * 0.8,
        target=center,
    )

    log.info("Three-point lighting created (Key/Fill/Rim).")


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------

def _setup_camera(center: Vector, diagonal: float) -> None:
    """Place a camera looking at *center* from a 3/4 perspective."""
    dist = diagonal * 1.5

    cam_location = (
        center.x + dist * 0.7,
        center.y - dist,
        center.z + dist * 0.5,
    )

    bpy.ops.object.camera_add(location=cam_location)
    cam = bpy.context.active_object
    cam.name = "AutoCamera"

    direction = (center - Vector(cam_location)).normalized()
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    cam.data.lens = 50
    cam.data.clip_start = diagonal * 0.01
    cam.data.clip_end = diagonal * 20

    bpy.context.scene.camera = cam
    log.info("Camera placed at distance %.2f from center.", dist)


# ---------------------------------------------------------------------------
# Render settings
# ---------------------------------------------------------------------------

def _setup_render(
    *,
    engine: str,
    resolution: tuple[int, int],
    samples: int,
    denoise: bool,
    film_transparent: bool,
    gpu_enabled: bool,
) -> None:
    scene = bpy.context.scene
    render = scene.render

    render.engine = "BLENDER_EEVEE_NEXT" if engine.upper() == "EEVEE" else "CYCLES"

    render.resolution_x, render.resolution_y = resolution
    render.resolution_percentage = 100
    render.image_settings.file_format = "PNG"
    render.image_settings.color_mode = "RGBA"
    render.film_transparent = film_transparent

    # When film is NOT transparent (e.g. opacity models), use a neutral
    # light-grey world background so the model is visible.
    if not film_transparent:
        world = bpy.data.worlds.get("World")
        if world is None:
            world = bpy.data.worlds.new("World")
        scene.world = world
        world.use_nodes = True
        bg_node = world.node_tree.nodes.get("Background")
        if bg_node:
            bg_node.inputs["Color"].default_value = (0.85, 0.85, 0.85, 1.0)
            bg_node.inputs["Strength"].default_value = 1.0

    if render.engine == "CYCLES":
        cycles = scene.cycles
        cycles.samples = samples
        cycles.use_denoising = denoise
        cycles.use_adaptive_sampling = True

        if gpu_enabled:
            _try_enable_gpu()

    log.info(
        "Render: %s %dx%d, %d samples, denoise=%s, transparent=%s.",
        render.engine, resolution[0], resolution[1],
        samples, denoise, film_transparent,
    )


def _try_enable_gpu() -> None:
    """Best-effort GPU activation for Cycles (CUDA > OPTIX > HIP > METAL)."""
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences

        for compute_type in ("OPTIX", "CUDA", "HIP", "METAL"):
            try:
                prefs.compute_device_type = compute_type
                prefs.get_devices()
                gpu_devices = [d for d in prefs.devices if d.type != "CPU"]
                if gpu_devices:
                    for d in prefs.devices:
                        d.use = d.type != "CPU"
                    bpy.context.scene.cycles.device = "GPU"
                    log.info("GPU compute enabled (%s).", compute_type)
                    return
            except Exception:
                continue

        log.warning("No GPU device found; falling back to CPU.")
    except Exception as exc:
        log.warning("Could not configure GPU: %s", exc)


# ---------------------------------------------------------------------------
# Render execution
# ---------------------------------------------------------------------------

def render_preview(output_path: str) -> None:
    """Render the active scene and write the result to *output_path*.

    Args:
        output_path: Absolute path for the output PNG.
    """
    from pathlib import Path
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    bpy.context.scene.render.filepath = output_path
    bpy.ops.render.render(write_still=True)
    log.info("Preview rendered -> '%s'.", output_path)
