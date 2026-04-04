"""GLB post-export validation (host-side wrapper).

The actual material-integrity checks run inside Blender (see
``blender_scripts/utils.py:validate_glb``).  This module provides a
clean Python API that launches Blender in ``--validate-only`` mode and
interprets the result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from asset_agent.core.blender_runner import run_validate_glb
from asset_agent.exceptions import ValidationError
from asset_agent.utils.logging import get_logger

logger = get_logger("core.validator")


@dataclass
class ValidationResult:
    """Outcome of a GLB validation run."""

    passed: bool
    errors: list[str] = field(default_factory=list)


def validate_glb(
    glb_path: Path,
    *,
    blender_path: str = "blender",
    raise_on_fail: bool = False,
) -> ValidationResult:
    """Validate a GLB file by re-importing it in a clean Blender scene.

    Args:
        glb_path: Path to the ``.glb`` file.
        blender_path: Blender executable (name or path).
        raise_on_fail: If ``True``, raise ``ValidationError`` on failure
                       instead of returning a result object.

    Returns:
        ``ValidationResult`` with pass/fail status and error details.

    Raises:
        ValidationError: If *raise_on_fail* is set and validation fails.
    """
    if not glb_path.exists():
        errors = [f"GLB file does not exist: {glb_path}"]
        if raise_on_fail:
            raise ValidationError(errors)
        return ValidationResult(passed=False, errors=errors)

    logger.info("Validating GLB: '%s'", glb_path)

    result_dict = run_validate_glb(glb_path, blender_path=blender_path)

    passed = result_dict.get("status") == "pass"
    errors = result_dict.get("errors", [])

    if passed:
        logger.info("Validation passed.")
    else:
        logger.warning("Validation failed with %d error(s).", len(errors))
        for err in errors:
            logger.warning("  - %s", err)

    if not passed and raise_on_fail:
        raise ValidationError(errors)

    return ValidationResult(passed=passed, errors=errors)
