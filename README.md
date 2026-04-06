# 3D Asset Agent

自动化 3D 资产处理管线 -- 输入 3D 模型 + PBR 贴图，自动完成材质构建、预览渲染和 GLB 导出。支持 OBJ/FBX/BLEND/glTF/STL 等 10 种格式，提供 Streamlit Web UI 和 CLI 两种使用方式。

## 功能概览

- **多格式支持** -- OBJ、FBX、BLEND、glTF/GLB、STL、3DS、DXF、X3D 等 10 种 3D 格式
- **贴图智能匹配** -- 正则引擎自动识别 Albedo、Normal、Roughness、Metallic、AO、Emissive、Opacity、Displacement、ORM 等 9 种 PBR 通道
- **Principled BSDF 材质构建** -- 自动处理色彩空间（sRGB / Non-Color）、Normal Map 节点、Glossiness 反转、ORM 拆分
- **黄色法线图自动修复** -- 自动检测双通道压缩法线图（BC5/DXT5nm/ATI2 等），重建 B 通道为标准蓝色法线图
- **三点布光 + 自动相机** -- 根据模型包围盒自动设置灯光和相机
- **CYCLES 渲染** -- 降噪、GPU 加速、透明背景，输出 PNG 预览图
- **GLB 导出 + 验证** -- glTF 2.0 规范 GLB 文件，全新场景重导入验证
- **Streamlit Web UI** -- 拖拽文件夹批量处理，实时预览，进度跟踪
- **Slack 通知** -- 每个模型处理完成后自动推送状态通知

## 快速开始

### 新机器一键部署

```bash
# 1. 安装 Python 3.10+ 和 Blender 4.0+（如果没有）
winget install Python.Python.3.12
winget install BlenderFoundation.Blender

# 2. 克隆项目
git clone https://github.com/pomiestone-del/3d-asset-agent.git
cd 3d-asset-agent

# 3. 双击 start.bat
```

`start.bat` 首次运行会自动执行环境检测（`setup_env.py --auto`）：
- 检测 Python >= 3.10
- `pip install -e .` 安装所有依赖
- 搜索 Blender 安装路径，自动更新 `config/default.yaml`
- 检查 Git / GitHub CLI
- 检查 `.env`（Slack Webhook，可选）

后续启动直接打开 Web UI。手动运行环境检测：`python setup_env.py`（加 `--auto` 自动修复）。

### 手动安装

```bash
cd 3d-asset-agent
pip install -e .
```

编辑 `config/default.yaml`，设置 Blender 路径：

```yaml
blender:
  executable: "C:\\Program Files\\Blender Foundation\\Blender 4.0\\blender.exe"
```

## Web UI 使用

```bash
streamlit run app.py
```

浏览器自动打开，输入模型文件路径或文件夹路径即可开始处理。

**文件夹模式**：自动扫描文件夹内所有 3D 模型，同一文件夹下同名不同格式的模型会自动去重（按 FBX > BLEND > glTF > GLB > OBJ > STL 优先级保留一个），贴图目录自动检测。

**处理结果**：每个模型显示预览图、输出路径和处理状态卡片，可一键打开输出文件夹。

**Slack 通知**（可选）：在项目根目录创建 `.env` 文件：

```
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
```

## 命令行用法

### 完整处理

```bash
asset-agent process --model <模型路径> --textures <贴图文件夹> --output <输出目录>
```

示例：

```bash
asset-agent process \
  --model ./models/Sword.fbx \
  --textures ./textures/Sword/ \
  --output ./output/Sword/ \
  --model-name Sword
```

执行流程：
1. 扫描贴图文件夹，自动匹配 PBR 通道
2. 启动 Blender 无头模式
3. 导入模型，构建 Principled BSDF 材质
4. 设置三点布光和自动相机
5. 渲染 PNG 预览图
6. 导出 GLB 文件
7. 全新场景重导入验证

输出：
- `<output>/Sword.glb` -- GLB 文件
- `<output>/Sword_preview.png` -- 渲染预览图

### 仅匹配贴图（调试用）

```bash
asset-agent match --textures <贴图文件夹> [--model-name <模型名>]
```

### 仅验证 GLB

```bash
asset-agent validate --glb <GLB文件路径>
```

### 通用选项

所有子命令支持 `--config <yaml>` 覆盖默认配置：

```bash
asset-agent process --model model.fbx --textures ./tex --output ./out --config my_config.yaml
```

## 支持的 3D 格式

| 格式 | 扩展名 |
|---|---|
| Wavefront OBJ | `.obj` |
| Autodesk FBX | `.fbx` |
| Blender | `.blend` |
| glTF | `.gltf` `.glb` |
| STL | `.stl` |
| 3D Studio | `.3ds` |
| DXF | `.dxf` |
| X3D | `.x3d` `.x3dv` |

## 贴图匹配规则

命名只要包含以下关键词之一即可自动识别（不区分大小写）：

| 通道 | 可识别的关键词 |
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

**唯一硬性要求：必须有 Albedo 贴图。** 其他通道缺失不会中断处理。

## 特殊贴图处理

### 黄色法线图（双通道压缩）

如果法线贴图是 BC5/DXT5nm/ATI2 等双通道压缩格式（只有 R/G 通道有数据，B 通道为 0，外观呈黄色），Agent 会自动检测并重建 B 通道：

- **文件名检测**：包含 `_BC5`、`_Yellow`、`_RG`、`_2ch`、`_DXT5nm`、`_ATI2` 关键词
- **像素检测**：采样分析 B 通道均值接近 0 且 R/G 有正常分布
- **重建算法**：`Z = sqrt(1 - X² - Y²)`，numpy 向量化计算

无需手动操作，处理时自动执行。

### Glossiness 工作流

文件名包含 `Gloss` 或 `Glossiness` 会自动插入 Invert 节点转换为 Roughness。

### ORM / ARM 打包贴图

R=AO, G=Roughness, B=Metallic。文件名包含 `ORM`、`ARM`、`RMA` 或 `Packed` 即可自动拆分。

## 渲染配置

通过 `config/default.yaml` 或 `--config` 参数调整：

```yaml
render:
  engine: "CYCLES"       # 或 "EEVEE"
  resolution: [1920, 1080]
  samples: 128
  denoise: true
  film_transparent: true
  gpu_enabled: true      # 自动检测 OPTIX/CUDA/HIP，失败自动回退 CPU
```

## 项目结构

```
3d-asset-agent/
├── app.py                      # Streamlit Web UI
├── start.bat                   # 一键启动（首次自动配置环境）
├── setup_env.py                # 环境检测和自动安装
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
│   ├── blender_scripts/        # Blender 内嵌 Python 脚本（隔离环境）
│   │   ├── process_asset.py    # 主入口
│   │   ├── material_builder.py # Principled BSDF 节点构建 + 黄色法线修复
│   │   ├── scene_setup.py      # 灯光/相机/渲染设置
│   │   └── utils.py            # 场景清理、多格式导入导出、验证
│   ├── importers/              # 3D 格式导入器
│   ├── exporters/              # GLB 导出设置
│   └── utils/                  # 配置加载、日志、文件工具、Slack通知
└── tests/                      # 92 个测试（单元测试 + 集成测试）
```

## 运行测试

```bash
cd 3d-asset-agent

# 单元测试（不需要 Blender，~2s）
python -m pytest tests/test_texture_matcher.py tests/test_agent_multi_material.py tests/test_blender_runner.py -v

# 全部测试（需要 Blender，~25s）
python -m pytest tests/ -v
```

## 常见问题

**Q: 贴图命名不符合默认规则？**
编辑 `config/texture_patterns.yaml`，修改正则表达式即可，无需重新安装。

**Q: GLB 在其他引擎中 Normal Map 不正确？**
确保 `config/default.yaml` 中 `export.export_tangents: true`（默认已开启）。

**Q: 渲染太慢？**
降低采样数和分辨率：`render.samples: 16`，`render.resolution: [640, 480]`。或切换到 EEVEE。

**Q: Blender 报 GPU 错误？**
设置 `render.gpu_enabled: false` 回退 CPU 渲染。

**Q: 新电脑如何部署？**
克隆项目后双击 `start.bat`，首次运行自动检测环境并安装依赖。
