# 3D Asset Agent

自动化 3D 资产处理管线 -- 输入白膜 `.obj` 和一组 PBR 贴图，自动完成材质构建、预览渲染和 GLB 导出。

## 功能概览

- **贴图智能匹配** -- 正则引擎自动识别 Albedo、Normal、Roughness、Metallic、AO、Emissive、Opacity、Displacement、ORM 等 9 种 PBR 通道，支持数十种常见命名约定
- **Principled BSDF 材质构建** -- 在 Blender 中创建完整节点树，自动处理色彩空间（sRGB / Non-Color）、Normal Map 中转节点、Glossiness 反转、ORM 打包贴图拆分
- **三点布光 + 自动相机** -- 根据模型包围盒自动设置 Key / Fill / Rim 灯光和相机位置
- **CYCLES 渲染** -- 支持降噪、GPU 加速、透明背景，输出 PNG 预览图
- **GLB 导出 + 验证** -- 导出符合 glTF 2.0 规范的 GLB 文件，并在全新场景中重新导入验证材质完整性

## 环境要求

| 依赖 | 版本 |
|---|---|
| Python | >= 3.10 |
| Blender | >= 3.4（推荐 4.0+，需独立安装，不通过 pip） |

Python 包依赖（安装时自动处理）：typer、pyyaml、rich、pydantic

## 安装

```bash
cd 3d-asset-agent
pip install -e .
```

安装后即可使用 `asset-agent` 命令。

## 配置 Blender 路径

编辑 `config/default.yaml`，将 `blender.executable` 设为你的 Blender 安装路径：

```yaml
blender:
  executable: "C:\\Program Files\\Blender Foundation\\Blender 4.0\\blender.exe"
```

如果 Blender 已在系统 PATH 中，可直接写 `"blender"`。

## 命令行用法

### 1. 完整处理流程

```bash
asset-agent process --obj <模型路径> --textures <贴图文件夹> --output <输出目录>
```

示例：

```bash
asset-agent process \
  --obj ./models/Sword.obj \
  --textures ./textures/Sword/ \
  --output ./output/Sword/ \
  --model-name Sword
```

执行流程：
1. 扫描贴图文件夹，自动匹配 PBR 通道
2. 启动 Blender 无头模式
3. 导入 OBJ，构建 Principled BSDF 材质并连接贴图
4. 设置三点布光和自动相机
5. 渲染 PNG 预览图
6. 导出 GLB 文件
7. 在全新场景中重新导入 GLB 验证材质完整性

输出：
- `<output>/Sword.glb` -- 带完整 PBR 材质的 GLB 文件
- `<output>/Sword_preview.png` -- 渲染预览图

### 2. 仅匹配贴图（调试用）

```bash
asset-agent match --textures <贴图文件夹> [--model-name <模型名>]
```

示例：

```bash
asset-agent match --textures ./textures/Sword/ --model-name Sword
```

输出一个表格，显示各通道匹配到的贴图文件和色彩空间，方便确认匹配结果是否正确。

### 3. 仅验证 GLB

```bash
asset-agent validate --glb <GLB文件路径>
```

在 Blender 中重新导入 GLB 并检查：
- 材质是否启用节点
- 是否存在 Principled BSDF 节点
- Base Color 是否已连接
- 所有纹理图片是否已嵌入

### 通用选项

所有子命令都支持 `--config <yaml>` 来覆盖默认配置，例如降低渲染质量加速测试：

```yaml
# my_fast_config.yaml
render:
  resolution: [640, 480]
  samples: 16
  denoise: false
  gpu_enabled: false
```

```bash
asset-agent process --obj model.obj --textures ./tex --output ./out --config my_fast_config.yaml
```

## 处理已有模型资产和贴图

如果你已经有一批模型和贴图文件需要批量处理，按以下步骤操作：

### 文件准备

**模型文件** -- 目前支持 `.obj` 格式（FBX 支持已预留接口）。如果你的模型是 FBX、GLTF 等格式，先用 Blender 手动转成 OBJ，或等后续版本支持。

**贴图文件** -- 放在一个文件夹中（子目录也会被递归扫描），命名只要包含以下关键词之一即可被自动识别：

| 通道 | 可识别的关键词（不区分大小写） |
|---|---|
| Albedo | `BaseColor` `Base_Color` `Albedo` `Diffuse` `Diff` `Color` `Col` |
| Normal | `Normal` `Nrm` `Norm` `Nor` `Nml` |
| Roughness | `Roughness` `Rough` `Rgh` `Gloss` `Glossiness` |
| Metallic | `Metallic` `Metalness` `Metal` `Met` |
| AO | `AO` `Occlusion` `AmbientOcclusion` |
| Emissive | `Emissive` `Emission` `Emit` `Glow` `SelfIllum` |
| Opacity | `Opacity` `Alpha` `Transparency` `Trans` `Mask` |
| Displacement | `Displacement` `Disp` `Height` `Hgt` |
| ORM (打包) | `ORM` `ARM` `RMA` `Packed` |

支持的图片格式：`.png` `.jpg` `.jpeg` `.tga` `.tiff` `.tif` `.exr` `.bmp`

**唯一硬性要求：必须有 Albedo 贴图。** 其他通道缺失会记录 warning 但不会中断处理。

### 典型文件结构示例

```
my_asset/
├── Sword.obj
└── textures/
    ├── Sword_BaseColor.png      <- Albedo (必须)
    ├── Sword_Normal.png         <- Normal
    ├── Sword_Roughness.png      <- Roughness
    ├── Sword_Metallic.png       <- Metallic
    ├── Sword_AO.png             <- Ambient Occlusion
    └── Sword_Emissive.png       <- Emissive (可选)
```

处理命令：

```bash
asset-agent process --obj my_asset/Sword.obj --textures my_asset/textures/ --output my_asset/output/
```

### 消歧：多套贴图在同一个文件夹

如果文件夹里有多个模型的贴图混在一起（例如 `Sword_BaseColor.png` 和 `Shield_BaseColor.png` 都在），用 `--model-name` 参数告诉 agent 优先匹配哪个模型：

```bash
asset-agent process \
  --obj Sword.obj \
  --textures ./shared_textures/ \
  --output ./output/ \
  --model-name Sword
```

匹配引擎会优先选择文件名包含模型名的贴图。如果仍然有歧义，按格式优先级（PNG > EXR > TGA > JPG > BMP）选择。

### 先预览再处理

建议先用 `match` 命令确认匹配结果，再跑完整流程：

```bash
# 第一步：确认贴图匹配是否正确
asset-agent match --textures my_asset/textures/ --model-name Sword

# 第二步：确认无误后执行完整处理
asset-agent process --obj my_asset/Sword.obj --textures my_asset/textures/ --output ./output/
```

### 批量处理脚本示例

```bash
#!/usr/bin/env bash
# batch_process.sh -- 批量处理一个目录下的所有 OBJ 文件

MODELS_DIR="./models"
OUTPUT_DIR="./output"

for obj in "$MODELS_DIR"/*.obj; do
    name=$(basename "$obj" .obj)
    tex_dir="$MODELS_DIR/${name}_textures"

    if [ -d "$tex_dir" ]; then
        echo "Processing: $name"
        asset-agent process \
            --obj "$obj" \
            --textures "$tex_dir" \
            --output "$OUTPUT_DIR/$name" \
            --model-name "$name"
    else
        echo "Skipping $name: texture directory not found at $tex_dir"
    fi
done
```

### Glossiness 工作流

如果你的贴图使用的是 Glossiness（光泽度）而非 Roughness（粗糙度），只要文件名包含 `Gloss` 或 `Glossiness`，agent 会自动检测并在材质中插入一个 Invert 节点将其转换为 Roughness。无需手动操作。

### ORM / ARM 打包贴图

如果贴图是 ORM 打包格式（R=AO, G=Roughness, B=Metallic），文件名包含 `ORM`、`ARM`、`RMA` 或 `Packed` 即可。agent 会自动用 Separate Color 节点拆分通道。此时如果同目录下还有独立的 Roughness 和 Metallic 贴图，ORM 的拆分结果会优先使用。

## 渲染配置

默认使用 CYCLES 引擎、1920x1080 分辨率、128 采样、启用降噪和 GPU。可通过 `config/default.yaml` 或 `--config` 参数调整：

```yaml
render:
  engine: "CYCLES"       # 或 "EEVEE"
  resolution: [1920, 1080]
  samples: 128           # 采样数，越高越清晰但越慢
  denoise: true          # Cycles 降噪
  film_transparent: true # 透明背景
  gpu_enabled: true      # 优先使用 GPU（自动检测 OPTIX/CUDA/HIP）
```

## 项目结构

```
3d-asset-agent/
├── pyproject.toml              # 项目元数据和依赖
├── config/
│   ├── default.yaml            # 默认配置（Blender路径、渲染参数等）
│   └── texture_patterns.yaml   # 贴图匹配正则规则（可自定义扩展）
├── src/asset_agent/
│   ├── cli.py                  # CLI 入口 (typer)
│   ├── agent.py                # 主流程编排
│   ├── exceptions.py           # 自定义异常层级
│   ├── core/
│   │   ├── texture_matcher.py  # 贴图匹配引擎
│   │   ├── blender_runner.py   # Blender 子进程管理
│   │   └── validator.py        # GLB 验证 (host-side)
│   ├── blender_scripts/        # 运行在 Blender 内嵌 Python 的脚本
│   │   ├── process_asset.py    # 主入口
│   │   ├── material_builder.py # Principled BSDF 节点构建
│   │   ├── scene_setup.py      # 灯光/相机/渲染设置
│   │   └── utils.py            # 场景清理、导入导出、验证
│   ├── importers/              # 3D 格式导入器 (OBJ, FBX预留)
│   ├── exporters/              # GLB 导出设置
│   └── utils/                  # 配置加载、日志、文件工具
└── tests/
    ├── test_texture_matcher.py # 74 个贴图匹配单元测试
    └── test_e2e.py             # 7 个端到端集成测试
```

## 运行测试

```bash
cd 3d-asset-agent

# 仅单元测试（不需要 Blender）
python -m pytest tests/test_texture_matcher.py -v

# 全部测试（需要 Blender）
python -m pytest tests/ -v
```

## 常见问题

**Q: 我的贴图命名不符合默认规则怎么办？**
编辑 `config/texture_patterns.yaml`，修改对应通道的 `pattern` 正则表达式，或者添加新的关键词。修改后不需要重新安装，立即生效。

**Q: 导出的 GLB 在其他引擎中 Normal Map 显示不正确？**
确保 `config/default.yaml` 中 `export.export_tangents: true`（默认已开启）。Tangent 数据是 Normal Map 在运行时正确计算的前提。

**Q: 渲染太慢了？**
降低采样数和分辨率：`render.samples: 16`，`render.resolution: [640, 480]`。或切换到 EEVEE 引擎（`render.engine: "EEVEE"`）。

**Q: Blender 报 GPU 相关错误？**
设置 `render.gpu_enabled: false` 回退到 CPU 渲染。Agent 会自动尝试 OPTIX > CUDA > HIP > METAL，全部失败后自动 fallback 到 CPU。

**Q: 支持 FBX 输入吗？**
目前只支持 OBJ。FBX 导入器接口已预留（`importers/fbx_importer.py`），后续版本实现。
