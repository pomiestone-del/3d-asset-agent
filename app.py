"""Streamlit Web UI for 3D Asset Agent.

Launch with::

    streamlit run app.py
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from dotenv import load_dotenv
import streamlit as st

load_dotenv()

from asset_agent.agent import AssetAgent, ProcessingResult
from asset_agent.importers.generic_importer import _ALL_SUPPORTED
from asset_agent.utils.slack import send_slack_notification

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(page_title="3D Asset Agent", page_icon=":package:", layout="wide")
st.title(":package: 3D Asset Agent")

# ---------------------------------------------------------------------------
# Sidebar — global settings
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Settings")

    slack_webhook = st.text_input(
        "Slack Webhook URL",
        value=os.environ.get("SLACK_WEBHOOK_URL", ""),
        help="Pre-filled from SLACK_WEBHOOK_URL env var. Leave blank to disable.",
    )

    st.subheader("Render")
    samples = st.slider("Samples", min_value=4, max_value=512, value=64, step=4)
    res_w = st.number_input("Width", min_value=160, max_value=3840, value=1920, step=160)
    res_h = st.number_input("Height", min_value=120, max_value=2160, value=1080, step=120)
    use_gpu = st.checkbox("GPU", value=True)
    denoise = st.checkbox("Denoise", value=True)


def _make_agent() -> AssetAgent:
    agent = AssetAgent()
    agent.config.render.samples = samples
    agent.config.render.resolution = [int(res_w), int(res_h)]
    agent.config.render.gpu_enabled = use_gpu
    agent.config.render.denoise = denoise
    return agent


def _notify_slack(name: str, success: bool, elapsed: float, **kwargs):
    if slack_webhook:
        send_slack_notification(
            slack_webhook, model_name=name, success=success,
            elapsed_seconds=elapsed, **kwargs,
        )


def _open_folder(folder: Path):
    """Open a folder in the system file explorer (Windows)."""
    try:
        os.startfile(str(folder))
    except Exception:
        subprocess.Popen(["explorer", str(folder)])


# ---------------------------------------------------------------------------
# Helpers — scan for models (deduplicated by folder)
# ---------------------------------------------------------------------------

_IMG_EXTS = {".png", ".jpg", ".jpeg", ".tga", ".tiff", ".tif", ".exr", ".bmp"}

# Prefer formats in this order when multiple exist in the same folder
_FORMAT_PRIORITY = [
    ".fbx", ".blend", ".gltf", ".glb", ".obj",
    ".stl", ".3ds", ".dxf", ".x3d", ".x3dv",
]


def _scan_models(root: Path) -> list[dict]:
    """Scan root directory for model files, one per folder.

    When a folder contains multiple model formats (e.g. both .fbx and .obj),
    only the highest-priority format is kept.
    """
    # Group all model files by their parent folder
    by_folder: dict[Path, list[Path]] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in _ALL_SUPPORTED:
            by_folder.setdefault(p.parent, []).append(p)

    found = []
    for folder, files in sorted(by_folder.items()):
        # Pick the best format per folder
        best = min(files, key=lambda f: (
            _FORMAT_PRIORITY.index(f.suffix.lower())
            if f.suffix.lower() in _FORMAT_PRIORITY else 99
        ))

        texture_dir = AssetAgent._discover_texture_dir(best)
        tex_count = sum(
            1 for f in texture_dir.iterdir()
            if f.is_file() and f.suffix.lower() in _IMG_EXTS
        )

        # Use folder name as display name (more intuitive than file stem)
        display_name = folder.name if folder != root else best.stem

        found.append({
            "name": display_name,
            "model": best,
            "texture_dir": texture_dir,
            "tex_count": tex_count,
            "format": best.suffix.lower(),
            "all_formats": sorted(set(f.suffix.lower() for f in files)),
            "folder": folder,
        })
    return found


def _run_batch(models: list[dict], output_base: Path):
    """Process a list of scanned models and display results."""
    agent = _make_agent()
    total = len(models)
    bar = st.progress(0, text=f"0/{total}")
    t0 = time.time()

    batch_results: list[tuple[dict, ProcessingResult, float]] = []

    for i, m in enumerate(models):
        bar.progress(i / total, text=f"{i}/{total} — {m['name']}...")
        model_out = output_base / m["name"]
        item_t0 = time.time()
        try:
            result = agent.process(
                model_path=m["model"],
                texture_dir=m["texture_dir"],
                output_dir=model_out,
                model_name=m["name"],
            )
        except Exception as exc:
            result = ProcessingResult(success=False, errors=[str(exc)])

        item_elapsed = time.time() - item_t0
        batch_results.append((m, result, item_elapsed))

        # Per-model Slack notification
        _notify_slack(
            m["name"], result.success, item_elapsed,
            glb_path=str(result.glb_path) if result.glb_path else None,
            errors=result.errors,
        )

    elapsed = time.time() - t0
    bar.progress(1.0, text="Done!")

    passed = sum(1 for _, r, _ in batch_results if r.success)
    st.info(f"**{passed}/{total}** succeeded in {elapsed:.1f}s")

    # --- Result cards with preview + open-folder button ---
    st.subheader("Results")

    for m, result, item_elapsed in batch_results:
        output_folder = output_base / m["name"]

        with st.container():
            cols = st.columns([1, 3, 1])

            # Preview image
            with cols[0]:
                if result.success and result.preview_path and result.preview_path.exists():
                    st.image(str(result.preview_path), use_container_width=True)
                elif result.success:
                    st.markdown(":white_check_mark:")
                else:
                    st.markdown(":x:")

            # Info
            with cols[1]:
                if result.success:
                    st.markdown(
                        f"**{m['name']}** &nbsp; `{m['format']}` "
                        f"&nbsp; {item_elapsed:.1f}s"
                    )
                    if result.glb_path:
                        st.caption(f"GLB: `{result.glb_path}`")
                else:
                    st.markdown(f"**{m['name']}** &nbsp; :red[FAILED]")
                    st.caption("; ".join(result.errors[:2]))

            # Open folder button
            with cols[2]:
                if output_folder.exists():
                    st.button(
                        ":open_file_folder: Open",
                        key=f"open_{m['name']}",
                        on_click=_open_folder,
                        args=(output_folder,),
                    )

            st.divider()

    # Batch summary notification
    _notify_slack(
        f"Batch done: {passed}/{total}", passed == total, elapsed,
        errors=[f"{total - passed} failed"] if passed < total else None,
    )


# ---------------------------------------------------------------------------
# Main UI — single input path (file or folder)
# ---------------------------------------------------------------------------

st.markdown(
    "Input a **folder** (auto-scans for models + textures) "
    "or a **model file** path. "
    f"Supported formats: `{', '.join(sorted(_ALL_SUPPORTED))}`"
)

input_path_str = st.text_input(
    "Model file or folder",
    placeholder=r"C:\Users\Pomie\Downloads\AgentTest",
)

output_dir_str = st.text_input(
    "Output folder",
    placeholder=r"C:\Users\Pomie\Downloads\AgentTest\output",
)

# ---------------------------------------------------------------------------
# Detect input type and show preview
# ---------------------------------------------------------------------------

input_path = Path(input_path_str) if input_path_str else None
is_dir = input_path is not None and input_path.is_dir()
is_file = input_path is not None and input_path.is_file()

if is_dir:
    models = _scan_models(input_path)
    if models:
        st.markdown(f"Found **{len(models)}** model(s) (deduplicated by folder):")
        import pandas as pd
        df = pd.DataFrame([
            {
                "Name": m["name"],
                "Selected": m["format"],
                "All Formats": ", ".join(m["all_formats"]),
                "Textures": m["tex_count"],
                "Folder": str(m["folder"]),
            }
            for m in models
        ])
        st.dataframe(df, use_container_width=True, hide_index=True)
    elif input_path_str:
        st.warning("No model files found in this folder.")

elif is_file:
    if input_path.suffix.lower() in _ALL_SUPPORTED:
        tex_dir = AssetAgent._discover_texture_dir(input_path)
        tex_count = sum(
            1 for f in tex_dir.iterdir()
            if f.is_file() and f.suffix.lower() in _IMG_EXTS
        )
        st.caption(
            f"Model: `{input_path.name}` ({input_path.suffix}) | "
            f"Textures: {tex_count} files in `{tex_dir}`"
        )
    else:
        st.error(f"Unsupported format: {input_path.suffix}")

elif input_path_str:
    st.error(f"Path not found: {input_path_str}")

# ---------------------------------------------------------------------------
# Process button
# ---------------------------------------------------------------------------

if st.button("Start Processing", type="primary", use_container_width=True):
    if not input_path_str:
        st.error("Please enter a model file or folder path.")
    elif not output_dir_str:
        st.error("Please enter an output folder.")
    elif is_dir:
        models = _scan_models(input_path)
        if not models:
            st.error("No model files found.")
        else:
            _run_batch(models, Path(output_dir_str))
    elif is_file:
        if input_path.suffix.lower() not in _ALL_SUPPORTED:
            st.error(f"Unsupported format: {input_path.suffix}")
        else:
            tex_dir = AssetAgent._discover_texture_dir(input_path)
            output_dir = Path(output_dir_str)
            name = input_path.stem

            progress = st.progress(0, text="Initializing...")
            t0 = time.time()

            try:
                progress.progress(10, text="Loading config...")
                agent = _make_agent()

                progress.progress(20, text="Running Blender pipeline...")
                result = agent.process(
                    model_path=input_path,
                    texture_dir=tex_dir,
                    output_dir=output_dir,
                    model_name=name,
                )
                elapsed = time.time() - t0
                progress.progress(100, text="Done!")

                if result.success:
                    st.success(f"Completed in {elapsed:.1f}s")
                    c1, c2 = st.columns([1, 2])
                    with c1:
                        if result.preview_path and result.preview_path.exists():
                            st.image(str(result.preview_path), caption="Render Preview")
                    with c2:
                        st.metric("GLB", result.glb_path.name if result.glb_path else "-")
                        st.metric("Time", f"{elapsed:.1f}s")
                        if result.glb_path:
                            st.code(str(result.glb_path), language=None)
                        if output_dir.exists():
                            st.button(
                                ":open_file_folder: Open Output Folder",
                                on_click=_open_folder,
                                args=(output_dir,),
                            )
                else:
                    st.error(f"Failed after {elapsed:.1f}s")
                    for err in result.errors:
                        st.warning(err)

                _notify_slack(
                    name, result.success, elapsed,
                    glb_path=str(result.glb_path) if result.glb_path else None,
                    errors=result.errors,
                )

            except Exception as exc:
                elapsed = time.time() - t0
                progress.progress(100, text="Error!")
                st.error(f"Fatal error: {exc}")
                _notify_slack(name, False, elapsed, errors=[str(exc)])
    else:
        st.error(f"Path not found: {input_path_str}")
