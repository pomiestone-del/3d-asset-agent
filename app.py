"""Streamlit Web UI for 3D Asset Agent.

Launch with::

    streamlit run app.py
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
import streamlit as st

load_dotenv()

from asset_agent.agent import AssetAgent, ProcessingResult
from asset_agent.importers.generic_importer import _ALL_SUPPORTED
from asset_agent.utils.file_utils import IMAGE_EXTENSIONS, collect_images
from asset_agent.utils.slack import send_slack_notification

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(page_title="3D Asset Agent", page_icon=":package:", layout="wide")
st.title(":package: 3D Asset Agent")

# ---------------------------------------------------------------------------
# Sidebar
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
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ModelInfo:
    name: str
    model: Path
    texture_dir: Path
    tex_count: int
    format: str
    all_formats: list[str] = field(default_factory=list)
    folder: Path = field(default_factory=Path)


# Prefer formats in this order when a folder has multiple model files
_FORMAT_PRIORITY = [
    ".fbx", ".blend", ".gltf", ".glb", ".obj",
    ".stl", ".3ds", ".dxf", ".x3d", ".x3dv",
]


def _format_rank(ext: str) -> int:
    try:
        return _FORMAT_PRIORITY.index(ext)
    except ValueError:
        return 99


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    """Open a folder in the system file explorer."""
    os.startfile(str(folder))


@st.cache_data(show_spinner="Scanning for models...")
def _scan_models(root: str) -> list[dict]:
    """Scan root directory for model files — all files per folder included.

    Returns dicts (not dataclass) because st.cache_data requires serializable return types.
    """
    root_path = Path(root)
    by_folder: dict[Path, list[Path]] = {}
    for p in root_path.rglob("*"):
        if p.is_file() and p.suffix.lower() in _ALL_SUPPORTED:
            by_folder.setdefault(p.parent, []).append(p)

    found = []
    for folder, files in sorted(by_folder.items()):
        all_formats = sorted({f.suffix.lower() for f in files})
        for model_file in sorted(files, key=lambda f: f.name):
            texture_dir = AssetAgent.discover_texture_dir(model_file)
            tex_count = len(collect_images(texture_dir, recursive=True))
            found.append({
                "name": model_file.stem,
                "model": str(model_file),
                "texture_dir": str(texture_dir),
                "tex_count": tex_count,
                "format": model_file.suffix.lower(),
                "all_formats": all_formats,
                "folder": str(folder),
            })
    return found


def _to_model_info(d: dict) -> ModelInfo:
    return ModelInfo(
        name=d["name"],
        model=Path(d["model"]),
        texture_dir=Path(d["texture_dir"]),
        tex_count=d["tex_count"],
        format=d["format"],
        all_formats=d["all_formats"],
        folder=Path(d["folder"]),
    )


def _display_result_card(m: ModelInfo, result: ProcessingResult, item_elapsed: float, output_folder: Path):
    """Render a single result card with previews, info, and open-folder button."""
    # Collect available preview images
    previews: list[tuple[str, str]] = []  # (label, path)
    if result.preview_path and result.preview_path.exists():
        previews.append(("PBR Render", str(result.preview_path)))
    if result.glb_preview_path and result.glb_preview_path.exists():
        previews.append(("GLB Re-import", str(result.glb_preview_path)))

    # Info + open-folder row
    info_col, btn_col = st.columns([5, 1])
    with info_col:
        if result.success:
            st.markdown(f"**{m.name}** &nbsp; `{m.format}` &nbsp; {item_elapsed:.1f}s")
            if result.glb_path:
                st.caption(f"GLB: `{result.glb_path}`")
        else:
            st.markdown(f"**{m.name}** &nbsp; :red[FAILED]")
            st.caption("; ".join(result.errors[:2]))
    with btn_col:
        if output_folder.exists():
            st.button(
                ":open_file_folder: Open",
                key=f"open_{m.name}",
                on_click=_open_folder,
                args=(output_folder,),
            )

    # Preview images — one column per image
    if previews:
        img_cols = st.columns(len(previews))
        for col, (label, path) in zip(img_cols, previews):
            with col:
                st.caption(label)
                st.image(path, use_container_width=True)
    elif not result.success:
        st.markdown(":x: No preview available")

    st.divider()


def _process_single(m: ModelInfo, output_dir: Path):
    """Process one model and display results."""
    agent = _make_agent()
    progress = st.progress(0, text="Initializing...")
    t0 = time.time()

    try:
        progress.progress(20, text=f"Processing {m.name}...")
        result = agent.process(
            model_path=m.model,
            texture_dir=m.texture_dir,
            output_dir=output_dir,
            model_name=m.name,
        )
    except Exception as exc:
        result = ProcessingResult(success=False, errors=[str(exc)])

    elapsed = time.time() - t0
    progress.progress(100, text="Done!")

    _display_result_card(m, result, elapsed, output_dir)
    _notify_slack(
        m.name, result.success, elapsed,
        glb_path=str(result.glb_path) if result.glb_path else None,
        errors=result.errors,
    )


def _run_batch(models: list[ModelInfo], output_base: Path):
    """Process a list of models and display results."""
    agent = _make_agent()
    total = len(models)
    bar = st.progress(0, text=f"0/{total}")
    t0 = time.time()

    batch_results: list[tuple[ModelInfo, ProcessingResult, float]] = []

    for i, m in enumerate(models):
        bar.progress(i / total, text=f"{i}/{total} — {m.name}...")
        model_out = output_base / m.name
        item_t0 = time.time()
        try:
            result = agent.process(
                model_path=m.model,
                texture_dir=m.texture_dir,
                output_dir=model_out,
                model_name=m.name,
            )
        except Exception as exc:
            result = ProcessingResult(success=False, errors=[str(exc)])

        item_elapsed = time.time() - item_t0
        batch_results.append((m, result, item_elapsed))

        _notify_slack(
            m.name, result.success, item_elapsed,
            glb_path=str(result.glb_path) if result.glb_path else None,
            errors=result.errors,
        )

    elapsed = time.time() - t0
    bar.progress(1.0, text="Done!")

    passed = sum(1 for _, r, _ in batch_results if r.success)
    st.info(f"**{passed}/{total}** succeeded in {elapsed:.1f}s")

    st.subheader("Results")
    for m, result, item_elapsed in batch_results:
        _display_result_card(m, result, item_elapsed, output_base / m.name)

    _notify_slack(
        f"Batch done: {passed}/{total}", passed == total, elapsed,
        errors=[f"{total - passed} failed"] if passed < total else None,
    )


# ---------------------------------------------------------------------------
# Main UI
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

input_path = Path(input_path_str) if input_path_str else None
is_dir = input_path is not None and input_path.is_dir()
is_file = input_path is not None and input_path.is_file()

if is_dir:
    model_dicts = _scan_models(input_path_str)
    if model_dicts:
        st.markdown(f"Found **{len(model_dicts)}** model(s) (deduplicated by folder):")
        import pandas as pd
        df = pd.DataFrame([
            {
                "Name": m["name"],
                "Selected": m["format"],
                "All Formats": ", ".join(m["all_formats"]),
                "Textures": m["tex_count"],
                "Folder": m["folder"],
            }
            for m in model_dicts
        ])
        st.dataframe(df, use_container_width=True, hide_index=True)
    elif input_path_str:
        st.warning("No model files found in this folder.")

elif is_file:
    if input_path.suffix.lower() in _ALL_SUPPORTED:
        tex_dir = AssetAgent.discover_texture_dir(input_path)
        tex_count = len(collect_images(tex_dir, recursive=True))
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
        model_dicts = _scan_models(input_path_str)
        if not model_dicts:
            st.error("No model files found.")
        else:
            models = [_to_model_info(d) for d in model_dicts]
            _run_batch(models, Path(output_dir_str))
    elif is_file:
        if input_path.suffix.lower() not in _ALL_SUPPORTED:
            st.error(f"Unsupported format: {input_path.suffix}")
        else:
            tex_dir = AssetAgent.discover_texture_dir(input_path)
            m = ModelInfo(
                name=input_path.stem,
                model=input_path,
                texture_dir=tex_dir,
                tex_count=len(collect_images(tex_dir, recursive=True)),
                format=input_path.suffix.lower(),
                folder=input_path.parent,
            )
            _process_single(m, Path(output_dir_str))
    else:
        st.error(f"Path not found: {input_path_str}")
