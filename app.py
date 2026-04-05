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

# ---------------------------------------------------------------------------
# Main form
# ---------------------------------------------------------------------------

st.subheader("Process a 3D Model")

col1, col2 = st.columns(2)

with col1:
    model_path_str = st.text_input(
        "Model file path",
        placeholder=r"D:\models\chair.obj",
        help="Supports .obj, .fbx, .blend, .gltf, .glb, .stl, .3ds, .dxf, .x3d",
    )

with col2:
    texture_dir_str = st.text_input(
        "Textures directory",
        placeholder=r"D:\models\textures",
    )

output_dir_str = st.text_input(
    "Output directory",
    placeholder=r"D:\models\output",
)

model_name = st.text_input(
    "Model name (optional)",
    help="Base name for output files. Defaults to model filename stem.",
)

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if st.button("Start Processing", type="primary", use_container_width=True):
    # --- Validation ---
    errors = []
    if not model_path_str:
        errors.append("Model file path is required.")
    elif not Path(model_path_str).is_file():
        errors.append(f"Model file not found: {model_path_str}")
    if not texture_dir_str:
        errors.append("Textures directory is required.")
    elif not Path(texture_dir_str).is_dir():
        errors.append(f"Textures directory not found: {texture_dir_str}")
    if not output_dir_str:
        errors.append("Output directory is required.")

    if errors:
        for e in errors:
            st.error(e)
    else:
        model_path = Path(model_path_str)
        texture_dir = Path(texture_dir_str)
        output_dir = Path(output_dir_str)
        name = model_name.strip() or model_path.stem

        progress = st.progress(0, text="Initializing...")
        status_area = st.empty()
        t0 = time.time()

        try:
            # Init agent
            progress.progress(10, text="Loading config...")
            agent = AssetAgent()
            agent.config.render.samples = samples
            agent.config.render.resolution = [int(res_w), int(res_h)]
            agent.config.render.gpu_enabled = use_gpu
            agent.config.render.denoise = denoise

            # Run pipeline
            progress.progress(20, text="Running Blender pipeline...")
            result: ProcessingResult = agent.process(
                model_path=model_path,
                texture_dir=texture_dir,
                output_dir=output_dir,
                model_name=name,
            )
            elapsed = time.time() - t0
            progress.progress(100, text="Done!")

            # Display result
            if result.success:
                st.success(f"Pipeline completed in {elapsed:.1f}s")
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
                st.error(f"Pipeline failed after {elapsed:.1f}s")
                for err in result.errors:
                    st.warning(err)

            # Slack notification
            if slack_webhook:
                send_slack_notification(
                    slack_webhook,
                    model_name=name,
                    success=result.success,
                    elapsed_seconds=elapsed,
                    glb_path=str(result.glb_path) if result.glb_path else None,
                    errors=result.errors,
                )
                st.caption(":bell: Slack notification sent.")

        except Exception as exc:
            elapsed = time.time() - t0
            progress.progress(100, text="Error!")
            st.error(f"Fatal error: {exc}")

            if slack_webhook:
                send_slack_notification(
                    slack_webhook,
                    model_name=name,
                    success=False,
                    elapsed_seconds=elapsed,
                    errors=[str(exc)],
                )

# ---------------------------------------------------------------------------
# Batch mode
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Batch Processing")

batch_col1, batch_col2 = st.columns(2)
with batch_col1:
    batch_input = st.text_input("Input directory (scan for models)", key="batch_in")
with batch_col2:
    batch_output = st.text_input("Output directory", key="batch_out")

if st.button("Start Batch", use_container_width=True):
    if not batch_input or not batch_output:
        st.error("Both input and output directories are required.")
    elif not Path(batch_input).is_dir():
        st.error(f"Input directory not found: {batch_input}")
    else:
        t0 = time.time()
        agent = AssetAgent()
        agent.config.render.samples = samples
        agent.config.render.resolution = [int(res_w), int(res_h)]
        agent.config.render.gpu_enabled = use_gpu
        agent.config.render.denoise = denoise

        with st.spinner("Scanning and processing..."):
            results = agent.batch_process(Path(batch_input), Path(batch_output))

        elapsed = time.time() - t0
        if not results:
            st.warning("No model files found.")
        else:
            passed = sum(1 for r in results if r.success)
            st.info(f"Batch done: {passed}/{len(results)} succeeded in {elapsed:.1f}s")

            for r in results:
                name = r.glb_path.stem if r.glb_path else "(unknown)"
                if r.success:
                    st.success(f"{name}")
                else:
                    st.error(f"{name}: {'; '.join(r.errors[:2])}")

            # Slack summary
            if slack_webhook:
                send_slack_notification(
                    slack_webhook,
                    model_name=f"Batch ({passed}/{len(results)})",
                    success=(passed == len(results)),
                    elapsed_seconds=elapsed,
                    errors=[f"{len(results) - passed} failed"] if passed < len(results) else None,
                )
