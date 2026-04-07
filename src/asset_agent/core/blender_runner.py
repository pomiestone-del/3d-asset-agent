"""Blender subprocess launcher.

Locates the Blender executable, builds command lines, and runs Blender in
headless (``--background``) mode with the project's processing scripts.
"""

from __future__ import annotations

import functools
import json
import os
import re
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

# ---------------------------------------------------------------------------
# Blend-file version detection
# ---------------------------------------------------------------------------

_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
_GZIP_MAGIC = b"\x1f\x8b"


def _read_blend_version(blend_path: Path) -> tuple[int, int] | None:
    """Return ``(major, minor)`` Blender version from a ``.blend`` file header.

    Handles plain, gzip-compressed, and zstd-compressed blend files.
    Returns ``None`` if the version cannot be determined.
    """
    try:
        with open(blend_path, "rb") as fh:
            header = fh.read(256)
    except OSError:
        return None

    # Decompress enough to read the BLENDER magic header
    if header[:4] == _ZSTD_MAGIC:
        try:
            import zstandard  # type: ignore[import-unresolved]
            dctx = zstandard.ZstdDecompressor()
            with open(blend_path, "rb") as fh:
                header = dctx.stream_reader(fh).read(256)
        except Exception:
            # zstandard not available or decompression failed — treat as 5.0+
            logger.debug("zstd blend file detected but zstandard not available; "
                         "assuming Blender 5.0+")
            return (5, 0)
    elif header[:2] == _GZIP_MAGIC:
        import gzip
        try:
            with gzip.open(blend_path, "rb") as fh:
                header = fh.read(256)
        except Exception:
            return None

    if not header.startswith(b"BLENDER"):
        return None

    # Bytes 9–11 are the 3-digit version string, e.g. b"420" → (4, 2)
    try:
        ver_int = int(header[9:12])
        return (ver_int // 100, (ver_int % 100) // 10)
    except (ValueError, IndexError):
        return None


@functools.lru_cache(maxsize=1)
def _discover_blender_executables() -> list[tuple[tuple[int, int], str]]:
    """Scan standard Blender installation directories and return candidates.

    Returns list of ``((major, minor), exe_path)`` sorted newest-first.
    """
    candidates: list[tuple[tuple[int, int], str]] = []

    search_roots: list[Path] = []
    # Windows: Program Files\Blender Foundation
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        pf = os.environ.get(env)
        if pf:
            search_roots.append(Path(pf) / "Blender Foundation")
    # macOS
    search_roots.append(Path("/Applications"))
    # Linux
    for p in ("/usr/bin", "/usr/local/bin", "/opt"):
        search_roots.append(Path(p))

    for root in search_roots:
        if not root.exists():
            continue
        try:
            entries = list(root.iterdir())
        except PermissionError:
            continue
        for d in entries:
            # Windows: "Blender 4.2" or "Blender 5.1"
            # Linux/macOS: directory or symlink named "blender" or "blender-4.2"
            exe = d / "blender.exe" if (d / "blender.exe").exists() else d / "blender"
            if not exe.exists():
                continue
            m = re.search(r"(\d+)[.\-_](\d+)", d.name)
            if m:
                ver = (int(m.group(1)), int(m.group(2)))
                candidates.append((ver, str(exe)))

    # Also check PATH — detect version so version requirements can be satisfied
    path_blender = shutil.which("blender")
    if path_blender and not any(exe == path_blender for _, exe in candidates):
        ver = _get_blender_version(path_blender) or (0, 0)
        candidates.append((ver, path_blender))

    return sorted(candidates, key=lambda x: x[0], reverse=True)


def find_blender_for_blend_file(
    blend_path: Path,
    preferred: str = "blender",
) -> str:
    """Return the best available Blender executable to open *blend_path*.

    If the preferred Blender is already new enough, it is returned as-is.
    Otherwise, scans installed Blender versions and returns the one that
    satisfies the blend file's minimum version requirement.

    Args:
        blend_path: Path to the ``.blend`` file.
        preferred: The configured Blender executable.

    Returns:
        Absolute path to the Blender binary.

    Raises:
        BlenderNotFoundError: No suitable Blender found.
    """
    required = _read_blend_version(blend_path)
    if required is None:
        logger.debug("Could not read blend version; using preferred Blender.")
        return find_blender(preferred)

    logger.debug("Blend file '%s' requires Blender >= %d.%d",
                 blend_path.name, required[0], required[1])

    # Check if the preferred Blender already satisfies the requirement
    try:
        pref_exe = find_blender(preferred)
        pref_ver = _get_blender_version(pref_exe)
        if pref_ver is not None and pref_ver >= required:
            logger.debug("Preferred Blender %d.%d satisfies requirement %d.%d.",
                         pref_ver[0], pref_ver[1], required[0], required[1])
            return pref_exe
        logger.info(
            "Preferred Blender %s (v%s) cannot open blend file requiring v%d.%d; "
            "scanning for compatible installation.",
            pref_exe,
            f"{pref_ver[0]}.{pref_ver[1]}" if pref_ver else "?",
            required[0], required[1],
        )
    except BlenderNotFoundError:
        pass

    # Scan installed Blenders, newest first
    for ver, exe in _discover_blender_executables():
        if ver >= required:
            logger.info("Using Blender %d.%d (%s) for blend file '%s'.",
                        ver[0], ver[1], exe, blend_path.name)
            return exe

    raise BlenderNotFoundError(
        f"No Blender >= {required[0]}.{required[1]} found to open '{blend_path.name}'"
    )


def _get_blender_version(exe: str) -> tuple[int, int] | None:
    """Ask a Blender executable for its version number."""
    # Extract from the directory name first (fast, no subprocess)
    m = re.search(r"(\d+)[.\-_](\d+)", Path(exe).parent.name)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    # Fall back to running Blender --version
    try:
        out = subprocess.check_output(
            [exe, "--version"], timeout=10, text=True, stderr=subprocess.DEVNULL
        )
        m2 = re.search(r"Blender\s+(\d+)\.(\d+)", out)
        if m2:
            return (int(m2.group(1)), int(m2.group(2)))
    except Exception:
        pass
    return None


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

def _build_render_args(
    render_engine: str,
    render_width: int,
    render_height: int,
    render_samples: int,
    denoise: bool,
    film_transparent: bool,
    gpu_enabled: bool,
    skip_validation: bool,
) -> list[str]:
    """Build the render-related CLI args shared by asset and group pipelines."""
    args: list[str] = [
        "--render-engine", render_engine,
        "--render-width", str(render_width),
        "--render-height", str(render_height),
        "--render-samples", str(render_samples),
    ]
    args.append("--denoise" if denoise else "--no-denoise")
    if film_transparent:
        args.append("--film-transparent")
    args.append("--gpu" if gpu_enabled else "--no-gpu")
    if skip_validation:
        args.append("--skip-validation")
    return args


def run_process_asset(
    model_path: Path,
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
        model_path: Path to the 3D model file (.obj, .fbx, .blend, .gltf, etc.).
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

    # For .blend files, automatically select a Blender version that can open them.
    if model_path.suffix.lower() == ".blend":
        blender_path = find_blender_for_blend_file(model_path, blender_path)

    args: list[str] = [
        "--model", str(model_path.resolve()),
        "--textures-json", json_str,
        "--output-dir", str(output_dir.resolve()),
        "--model-name", model_name,
        *_build_render_args(render_engine, render_width, render_height,
                            render_samples, denoise, film_transparent,
                            gpu_enabled, skip_validation),
    ]

    stdout = run_blender_script(
        _PROCESS_SCRIPT,
        args,
        blender_path=blender_path,
        timeout=timeout,
    )

    return _extract_json_result(stdout)


def run_process_group(
    model_entries: list[dict[str, Any]],
    output_dir: Path,
    *,
    group_name: str = "group",
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
    """Import multiple models into a single Blender scene and export one GLB.

    Args:
        model_entries: List of ``{"path", "material_name", "textures": [...]}`` dicts.
        output_dir: Directory for GLB and preview outputs.
        group_name: Base name for output files.

    Returns:
        Parsed JSON result dict with keys ``status``, ``errors``, ``glb``,
        ``preview``, ``glb_preview``.
    """
    models_json_str = json.dumps(model_entries, ensure_ascii=False)

    # For groups that contain .blend files, pick the highest required Blender.
    blend_models = [
        Path(e["path"]) for e in model_entries
        if Path(e["path"]).suffix.lower() == ".blend"
    ]
    for blend_path in blend_models:
        blender_path = find_blender_for_blend_file(blend_path, blender_path)

    args: list[str] = [
        "--models-json", models_json_str,
        "--output-dir", str(output_dir.resolve()),
        "--model-name", group_name,
        *_build_render_args(render_engine, render_width, render_height,
                            render_samples, denoise, film_transparent,
                            gpu_enabled, skip_validation),
    ]

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
