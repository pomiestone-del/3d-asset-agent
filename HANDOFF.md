# 3D Asset Agent — 交接文档

> 撰写时间：2026-04-04
> 项目路径：`d:\CodingProjects\BlenderTexAgent\3d-asset-agent`
> Blender 版本：4.0.2 (`C:\Program Files\Blender Foundation\Blender 4.0\blender.exe`)

---

## 一、项目完成状态

6 个实现步骤全部完成，81 个测试通过（74 单元测试 + 7 端到端测试），并在 3 个真实资产上完成了实际验证。

### 已实现模块

```
src/asset_agent/
├── cli.py                     ✅ 三个子命令 (process / match / validate)
├── agent.py                   ✅ 主编排器 AssetAgent
├── exceptions.py              ✅ 9 个自定义异常
├── core/
│   ├── texture_matcher.py     ✅ 正则匹配引擎 + 基名推断 + 消歧
│   ├── blender_runner.py      ✅ subprocess 启动 Blender
│   └── validator.py           ✅ GLB 验证封装
├── blender_scripts/           ✅ 运行在 Blender 内嵌 Python（隔离环境）
│   ├── process_asset.py       ✅ 主入口，argparse 接参数
│   ├── material_builder.py    ✅ Principled BSDF 节点构建
│   ├── scene_setup.py         ✅ 三点布光 + 自动相机 + CYCLES 配置
│   └── utils.py               ✅ 场景清理、OBJ 导入、GLB 导出、验证
├── importers/
│   ├── base.py                ✅ ABC 接口
│   ├── obj_importer.py        ✅ OBJ 导入
│   └── fbx_importer.py        ⬜ 桩（NotImplementedError）
├── exporters/
│   └── glb_exporter.py        ✅ 导出设置 + textures payload 构建
└── utils/
    ├── config.py              ✅ YAML 加载 + dataclass 映射
    ├── logging.py             ✅ Rich 日志
    └── file_utils.py          ✅ 图片扫描
```

---

## 二、本次会话的修改记录

### Step 1-5：从零构建完整项目

按照 SPEC 从目录结构开始逐步实现，详见 `README.md`。

### Step 6：端到端测试

- `tests/test_e2e.py` — 7 个测试覆盖 Blender 脚本执行、完整管线、Agent 集成、GLB 验证
- `tests/conftest.py` — 新增 `blender_exe` fixture 和 `requires_blender` skip marker

### 真实资产测试中的增强（Step 6 之后）

处理 `C:\Users\Pomie\Downloads\AgentTest` 下的三个真实资产时，发现并修复了以下问题：

#### 修改 1：正则新增 `_bc` 和 `_n` 缩写

**文件**：`config/texture_patterns.yaml`

**原因**：Long House 资产的贴图用 `boards_and_planks_bc.png`（bc = base color）和 `thatch_n.png`（n = normal），原有正则匹配不到。

**改动**：
- albedo pattern 末尾追加 `|(?<![a-z])bc(?![a-z])`
- normal pattern 末尾追加 `|(?<![a-z])n(?![a-z])`

两个 lookbehind/lookahead 确保不会误匹配到单词中间的 "bc" 或 "n"。

#### 修改 2：基名推断 albedo

**文件**：`src/asset_agent/core/texture_matcher.py`，`TextureMatcher._infer_albedo()` 方法

**原因**：Cultist Monk 资产的 albedo 文件名为 `texture_pbr_20250901.png`，无任何 PBR 关键词。但同目录下的 `texture_pbr_20250901_normal.png` / `_roughness.png` / `_metallic.png` 都匹配上了。

**逻辑**：
1. 收集已匹配文件的 stem，计算公共前缀（按 `_` / `-` 分隔符逐段截取）
2. 在未匹配文件中找 stem 恰好等于该前缀的图片
3. 如果找到，视为 albedo 候选

在 `match()` 方法中，正则匹配结束后、抛出 `MissingAlbedoError` 之前插入此推断逻辑。

#### 修改 3：无贴图模式

**文件**：
- `src/asset_agent/blender_scripts/process_asset.py` — `textures` 为空列表时跳过 `build_material()`，保留 OBJ 导入时的 MTL 颜色材质
- `src/asset_agent/agent.py` — `process()` 中 catch `MissingAlbedoError`，降级为空 `TextureMap`，让 Blender 用 MTL 材质渲染

**原因**：Audi RS6 只有 MTL 颜色定义（25 种材质），无图片贴图。

#### 修改 4：默认 Blender 路径

**文件**：`config/default.yaml`

`blender.executable` 从 `"blender"` 改为 `"C:\\Program Files\\Blender Foundation\\Blender 4.0\\blender.exe"`。

---

## 三、已知 Bug / 待解决问题

### Bug 1：Audi 无贴图模型验证误报 FAIL

**现象**：`agent.process()` 返回 `success=False`，errors 为 25 条 "Base Color input is not connected"。

**原因**：GLB 验证器 (`blender_scripts/utils.py:validate_glb`) 要求每个材质的 Base Color 输入必须有 link（图片节点连接）。但纯色 MTL 材质的 Base Color 是默认值而非图片连接。

**影响**：GLB 和 preview 都正确生成，只是 `result.success` 被标为 False。

**修复方向**：验证逻辑应区分"有贴图材质"和"纯色材质"。对纯色材质，检查 Base Color 默认值是否非零即可，不要求有 link。或者在 agent 层面，当 `textures_payload` 为空时跳过验证（`skip_validation=True`）。

### Bug 2：Windows GBK 编码导致 stderr 解析崩溃

**现象**：处理 Long House 和 Audi 时，PowerShell 输出 `UnicodeDecodeError: 'gbk' codec can't decode byte 0x94`。

**原因**：`subprocess.run(capture_output=True, text=True)` 在 Windows 中文环境下使用 GBK 解码 Blender 的 stderr。Blender 输出包含 UTF-8 字符（如路径中的特殊字符），GBK 无法解码。

**影响**：不影响处理结果（异常发生在 stderr 读取线程），但会在终端打印一个 traceback。

**修复方向**：在 `blender_runner.py` 的 `subprocess.run` 中显式指定 `encoding="utf-8", errors="replace"`。

### Bug 3：Long House 多材质场景只应用了一套贴图

**现象**：Long House 有 boards / planks / thatch 三套贴图（对应三种材质），但 agent 只选了其中一张贴图给每个通道，构建了单一材质覆盖所有 mesh。

**原因**：当前架构设计为"单模型单材质"，`build_material()` 清空所有对象的材质再赋新材质。

**影响**：渲染和 GLB 能跑通，但材质不完全正确（所有面用同一套贴图）。

**修复方向**：需要多材质支持，见后续目标。

---

## 四、接下来的目标

### 优先级 P0（应尽快修复）

1. **修复 stderr 编码问题**
   - `blender_runner.py` 第 95 行 `subprocess.run` 加 `encoding="utf-8", errors="replace"`
   - 一行改动，零风险

2. **无贴图模型跳过验证**
   - `agent.py` 中当 `textures_payload` 为空时传 `skip_validation=True`
   - 或者在 `validate_glb` 中对无贴图材质放宽检查

### 优先级 P1（功能增强）

3. **多材质支持**
   - 解析 MTL 文件获取材质名 → mesh 面组映射
   - 支持每个材质独立的贴图目录（按材质名子文件夹匹配）
   - `build_material()` 改为对每个材质组分别构建节点树
   - 这是最大的架构改动，影响 `texture_matcher`、`material_builder`、`process_asset`

4. **FBX 导入器实现**
   - `fbx_importer.py` 目前是桩
   - Audi 同时提供了 FBX 文件，FBX 格式保留材质信息更完整

5. **MTL 文件解析辅助匹配**
   - 从 `map_Kd`、`map_Bump` 等字段提取贴图路径
   - 在正则匹配之前，先用 MTL 的显式声明作为匹配结果
   - Cultist Monk 的 MTL 已经完整声明了四张贴图

### 优先级 P2（体验优化）

6. **渲染质量参数 CLI 化**
   - 当前渲染参数只能通过 `--config` YAML 覆盖
   - 考虑在 `process` 子命令直接暴露 `--samples`、`--resolution` 等参数

7. **进度反馈**
   - Blender 子进程执行时间较长（Audi 约 22 秒）
   - 考虑实时转发 Blender stdout 或用 Rich progress bar

8. **批量处理子命令**
   - 新增 `asset-agent batch --input-dir <dir> --output-dir <dir>` 子命令
   - 自动发现 OBJ + 贴图文件夹的配对关系

---

## 五、关键架构约束（给接手者）

1. **`blender_scripts/` 是隔离环境** — 只能用 stdlib + bpy，不能 `from asset_agent import ...`。数据通过 `sys.argv` JSON 传入，结果通过 stdout JSON 行传出。

2. **Color Space 必须正确** — Albedo/Emissive 用 `sRGB`，其余全部 `Non-Color`。写错 gamma 校正会导致材质看起来完全错误。

3. **export_tangents=True 是硬性要求** — Normal Map 在 glTF 规范中依赖 tangent 数据，缺失会导致法线效果丢失。

4. **GLB 验证必须在全新场景中** — `bpy.ops.wm.read_factory_settings(use_empty=True)` 清空后再导入，否则残留数据干扰结果。

5. **ORM 打包贴图** — R=AO, G=Roughness, B=Metallic。Blender 的 glTF 导出器会自动重新打包 metallicRoughnessTexture，不需要手动处理。

---

## 六、测试命令速查

```bash
cd d:\CodingProjects\BlenderTexAgent\3d-asset-agent

# 仅单元测试（不需要 Blender，1.5 秒）
python -m pytest tests/test_texture_matcher.py -v

# 全部测试（需要 Blender，约 25 秒）
python -m pytest tests/ -v

# 匹配预览
python -m asset_agent.cli match --textures <dir> --model-name <name>

# 完整处理
python -m asset_agent.cli process --obj <path> --textures <dir> --output <dir>

# 批量测试脚本
python scripts/batch_agent_test.py
```
