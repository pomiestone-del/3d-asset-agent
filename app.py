"""Streamlit Web UI for 3D Asset Agent.

Launch with::

    streamlit run app.py
"""

from __future__ import annotations

import os
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


# ---------------------------------------------------------------------------
# Helpers — scan for models
# ---------------------------------------------------------------------------

def _scan_models(root: Path) -> list[dict]:
    """Scan root directory for model files, pairing each with its texture dir.

    Expected layout (model + textures in same folder or textures/ subfolder):
        root/
        ├── Chair/
        │   ├── chair.obj
        │   ├── chair_basecolor.png
        │   └── chair_normal.png
        ├── Table/
        │   ├── table.fbx
        │   └── textures/
        │       ├── table_diffuse.png
        │       └── table_roughness.png
    """
    found = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in _ALL_SUPPORTED:
            continue
        texture_dir = AssetAgent._discover_texture_dir(p)

        # Count image files in texture dir
        img_exts = {".png", ".jpg", ".jpeg", ".tga", ".tiff", ".tif", ".exr", ".bmp"}
        tex_count = sum(
            1 for f in texture_dir.iterdir()
            if f.is_file() and f.suffix.lower() in img_exts
        )

        found.append({
            "name": p.stem,
            "model": p,
            "texture_dir": texture_dir,
            "tex_count": tex_count,
            "format": p.suffix.lower(),
        })
    return found


# ---------------------------------------------------------------------------
# Tab layout
# ---------------------------------------------------------------------------

tab_single, tab_batch = st.tabs(["Single Model", "Batch Processing"])

# ---------------------------------------------------------------------------
# Tab 1 — Single model
# ---------------------------------------------------------------------------

with tab_single:
    model_path_str = st.text_input(
        "Model file path",
        placeholder=r"D:\models\Chair\chair.obj",
        help="Supports: " + ", ".join(sorted(_ALL_SUPPORTED)),
    )

    auto_tex = st.checkbox(
        "Auto-detect textures from model folder",
        value=True,
        help="Looks for textures/ subfolder, then falls back to the model's parent directory.",
    )

    if auto_tex:
        if model_path_str and Path(model_path_str).is_file():
            detected = AssetAgent._discover_texture_dir(Path(model_path_str))
            st.caption(f"Texture dir: `{detected}`")
        texture_dir_str = ""
    else:
        texture_dir_str = st.text_input(
            "Textures directory",
            placeholder=r"D:\models\Chair\textures",
        )

    output_dir_str = st.text_input(
        "Output directory",
        placeholder=r"D:\models\output",
    )

    model_name = st.text_input(
        "Model name (optional)",
        help="Base name for output files. Defaults to model filename stem.",
    )

    if st.button("Start Processing", type="primary", use_container_width=True):
        errors = []
        if not model_path_str:
            errors.append("Model file path is required.")
        elif not Path(model_path_str).is_file():
            errors.append(f"Model file not found: {model_path_str}")
        if not auto_tex and not texture_dir_str:
            errors.append("Textures directory is required.")
        elif not auto_tex and not Path(texture_dir_str).is_dir():
            errors.append(f"Textures directory not found: {texture_dir_str}")
        if not output_dir_str:
            errors.append("Output directory is required.")

        if errors:
            for e in errors:
                st.error(e)
        else:
            model_path = Path(model_path_str)
            if auto_tex:
                texture_dir = AssetAgent._discover_texture_dir(model_path)
            else:
                texture_dir = Path(texture_dir_str)
            output_dir = Path(output_dir_str)
            name = model_name.strip() or model_path.stem

            progress = st.progress(0, text="Initializing...")
            t0 = time.time()

            try:
                progress.progress(10, text="Loading config...")
                agent = _make_agent()

                progress.progress(20, text="Running Blender pipeline...")
                result: ProcessingResult = agent.process(
                    model_path=model_path,
                    texture_dir=texture_dir,
                    output_dir=output_dir,
                    model_name=name,
                )
                elapsed = time.time() - t0
                progress.progress(100, text="Done!")

                if result.success:
                    st.success(f"Completed in {elapsed:.1f}s")
                    c1, c2 = st.columns(2)
                    with c1:
                        if result.preview_path and result.preview_path.exists():
                            st.image(str(result.preview_path), caption="Render Preview")
                    with c2:
                        st.metric("GLB", result.glb_path.name if result.glb_path else "-")
                        st.metric("Time", f"{elapsed:.1f}s")
                        if result.glb_path:
                            st.code(str(result.glb_path), language=None)
                else:
                    st.error(f"Failed after {elapsed:.1f}s")
                    for err in result.errors:
                        st.warning(err)

                _notify_slack(name, result.success, elapsed,
                              glb_path=str(result.glb_path) if result.glb_path else None,
                              errors=result.errors)

            except Exception as exc:
                elapsed = time.time() - t0
                progress.progress(100, text="Error!")
                st.error(f"Fatal error: {exc}")
                _notify_slack(name, False, elapsed, errors=[str(exc)])


# ---------------------------------------------------------------------------
# Tab 2 — Batch processing
# ---------------------------------------------------------------------------

with tab_batch:
    st.markdown(
        "Scan a root folder for all 3D models. "
        "Textures are auto-detected from each model's folder "
        "(looks for `textures/` subfolder, then same directory)."
    )

    bcol1, bcol2 = st.columns(2)
    with bcol1:
        batch_root = st.text_input(
            "Root folder (contains model subfolders)",
            placeholder=r"D:\models",
            key="batch_root",
        )
    with bcol2:
        batch_output = st.text_input(
            "Output folder",
            placeholder=r"D:\models\output",
            key="batch_out",
        )

    # --- Scan and preview ---
    if batch_root and Path(batch_root).is_dir():
        models = _scan_models(Path(batch_root))

        if models:
            st.markdown(f"**Found {len(models)} model(s):**")

            import pandas as pd
            df = pd.DataFrame([
                {
                    "Name": m["name"],
                    "Format": m["format"],
                    "Textures": m["tex_count"],
                    "Texture Dir": str(m["texture_dir"]),
                    "Model Path": str(m["model"]),
                }
                for m in models
            ])
            st.dataframe(df, use_container_width=True, hide_index=True)

            # --- Process all ---
            if st.button("Process All", type="primary", use_container_width=True):
                if not batch_output:
                    st.error("Output folder is required.")
                else:
                    output_base = Path(batch_output)
                    agent = _make_agent()
                    total = len(models)
                    bar = st.progress(0, text=f"0/{total}")
                    results_area = st.container()
                    t0 = time.time()

                    batch_results: list[tuple[str, ProcessingResult]] = []

                    for i, m in enumerate(models):
                        bar.progress(
                            (i) / total,
                            text=f"{i}/{total} — processing {m['name']}...",
                        )
                        model_out = output_base / m["name"]
                        try:
                            result = agent.process(
                                model_path=m["model"],
                                texture_dir=m["texture_dir"],
                                output_dir=model_out,
                                model_name=m["name"],
                            )
                        except Exception as exc:
                            result = ProcessingResult(success=False, errors=[str(exc)])

                        batch_results.append((m["name"], result))

                        with results_area:
                            if result.success:
                                st.success(f"{m['name']}")
                            else:
                                st.error(f"{m['name']}: {'; '.join(result.errors[:2])}")

                    elapsed = time.time() - t0
                    bar.progress(1.0, text="Done!")

                    passed = sum(1 for _, r in batch_results if r.success)
                    st.info(f"**Batch complete:** {passed}/{total} succeeded in {elapsed:.1f}s")

                    # Show previews for successful models
                    success_results = [(n, r) for n, r in batch_results if r.success and r.preview_path and r.preview_path.exists()]
                    if success_results:
                        st.subheader("Previews")
                        cols = st.columns(min(len(success_results), 4))
                        for idx, (name, r) in enumerate(success_results):
                            with cols[idx % 4]:
                                st.image(str(r.preview_path), caption=name, use_container_width=True)

                    _notify_slack(
                        f"Batch ({passed}/{total})",
                        passed == total,
                        elapsed,
                        errors=[f"{total - passed} failed"] if passed < total else None,
                    )
        else:
            st.warning("No model files found in this directory.")
    elif batch_root:
        st.error(f"Directory not found: {batch_root}")
