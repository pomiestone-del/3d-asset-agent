"""Blender subprocess launcher.

Locates the Blender executable, builds command lines, and runs Blender in
headless (``--background``) mode with the project's processing scripts.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from asset_agent.exceptions import (
    BlenderExecutionError,
    BlenderNotFoundError,
    BlenderTimeoutError,
)
from asset_agent.utils.logging import get_logger

logger = get_logger("core.blender_runner")

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "blender_scripts"
_PROCESS_SCRIPT = _SCRIPTS_DIR / "process_asset.py"

DEFAULT_TIMEOUT = 300  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_blender(hint: str = "blender") -> str:
    """Resolve the Blender executable path.

    Args:
        hint: Explicit path or bare command name to search on ``PATH``.

    Returns:
        Absolute path string to the Blender binary.

    Raises:
        BlenderNotFoundError: If Blender cannot be located.
    """
    path = Path(hint)
    if path.is_file():
        return str(path.resolve())

    resolved = shutil.which(hint)
    if resolved:
        return resolved

    raise BlenderNotFoundError(hint)


def run_blender_script(
    script_path: str | Path,
    args: list[str],
    *,
    blender_path: str = "blender",
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Launch Blender in background mode and execute a Python script.

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

    def _read_stream(stream, buf: list[str], *, is_stderr: bool = False) -> None:
        for line in stream:
            buf.append(line)
            stripped = line.rstrip("\n\r")
            if is_stderr:
                logger.debug("blender stderr: %s", stripped)
            else:
                logger.info("blender> %s", stripped)

    t_out = threading.Thread(
        target=_read_stream, args=(proc.stdout, stdout_lines), daemon=True,
    )
    t_err = threading.Thread(
        target=_read_stream, args=(proc.stderr, stderr_lines),
        kwargs={"is_stderr": True}, daemon=True,
    )
    t_out.start()
    t_err.start()

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        t_out.join(timeout=2)
        t_err.join(timeout=2)
        raise BlenderTimeoutError(timeout) from exc

    t_out.join(timeout=5)
    t_err.join(timeout=5)

    stdout_text = "".join(stdout_lines)
    stderr_text = "".join(stderr_lines)

    if proc.returncode not in (0, 2):
        raise BlenderExecutionError(stderr_text, proc.returncode)

    return stdout_text


# ---------------------------------------------------------------------------
# High-level wrappers
# ---------------------------------------------------------------------------

def run_process_asset(
    obj_path: Path,
    textures_json: list[dict[str, Any]],
    output_dir: Path,
    *,
    model_name: str = "asset",
    blender_path: str = "blender",
    render_engine: str = "CYCLES",
    render_width: int = 1920,
    render_height: int = 1080,
    render_samples: int = 128,
    denoise: bool = True,
    film_transparent: bool = True,
    gpu_enabled: bool = True,
    skip_validation: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Run the full asset-processing pipeline inside Blender.

    Args:
        obj_path: Path to the ``.obj`` model.
        textures_json: Texture descriptors (list of dicts).
        output_dir: Directory for GLB and preview outputs.
        model_name: Base name for output files.
        blender_path: Blender executable.
        render_engine: ``"CYCLES"`` or ``"EEVEE"``.
        render_width: Output image width.
        render_height: Output image height.
        render_samples: Render sample count.
        denoise: Use Cycles denoiser.
        film_transparent: Transparent background.
        gpu_enabled: Attempt GPU rendering.
        skip_validation: Skip post-export GLB validation.
        timeout: Max seconds for the Blender subprocess.

    Returns:
        Parsed JSON result dict with keys ``status``, ``errors``,
        ``glb``, ``preview``.

    Raises:
        BlenderExecutionError: Pipeline failed with a fatal error.
    """
    json_str = json.dumps(textures_json, ensure_ascii=False)

    args: list[str] = [
        "--model", str(obj_path.resolve()),
        "--textures-json", json_str,
        "--output-dir", str(output_dir.resolve()),
        "--model-name", model_name,
        "--render-engine", render_engine,
        "--render-width", str(render_width),
        "--render-height", str(render_height),
        "--render-samples", str(render_samples),
    ]
    if denoise:
        args.append("--denoise")
    else:
        args.append("--no-denoise")
    if film_transparent:
        args.append("--film-transparent")
    if gpu_enabled:
        args.append("--gpu")
    else:
        args.append("--no-gpu")
    if skip_validation:
        args.append("--skip-validation")

    stdout = run_blender_script(
        _PROCESS_SCRIPT,
        args,
        blender_path=blender_path,
        timeout=timeout,
    )

    return _extract_json_result(stdout)


def run_validate_glb(
    glb_path: Path,
    *,
    blender_path: str = "blender",
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Validate an existing GLB file by re-importing it in a clean Blender scene.

    Args:
        glb_path: Path to the ``.glb`` file.
        blender_path: Blender executable.
        timeout: Max seconds.

    Returns:
        Dict with ``status`` (``"pass"``/``"fail"``) and ``errors`` list.
    """
    args: list[str] = [
        "--obj", "dummy",
        "--textures-json", "[]",
        "--output-dir", str(glb_path.parent),
        "--validate-only", str(glb_path.resolve()),
    ]

    stdout = run_blender_script(
        _PROCESS_SCRIPT,
        args,
        blender_path=blender_path,
        timeout=timeout,
    )

    return _extract_json_result(stdout)


def _extract_json_result(stdout: str) -> dict[str, Any]:
    """Find and parse the JSON result line from Blender's stdout."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue

    logger.warning("No JSON result found in Blender output; returning empty dict.")
    return {"status": "unknown", "errors": ["No JSON result in Blender output."]}
