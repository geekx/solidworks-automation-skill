# 示例代码

本目录包含 SolidWorks 自动化的示例代码,从基础到高级逐步演示各种功能。

## 示例列表

### 01_basic_part.py
创建基本零件 - 圆柱体

**学习内容:**
- 连接 SolidWorks
- 创建新零件文档
- 绘制草图(圆)
- 拉伸特征
- 保存文件

**运行前提:**
- SolidWorks 已运行
- 确保 `C:\temp\` 目录存在

---

### 02_complex_part.py
创建复杂零件 - 带孔的矩形板

**学习内容:**
- 多个草图和特征
- 切除拉伸(孔)
- 倒角和圆角
- 面选择

**运行前提:**
- SolidWorks 已运行

---

### 03_assembly.py
创建装配体

**学习内容:**
- 添加组件
- 配合关系(重合、同心)
- 获取组件列表

**运行前提:**
- 需要准备两个零件文件:
  - `C:\parts\base.sldprt`
  - `C:\parts\shaft.sldprt`
- 或修改代码中的路径

---

### 04_batch_export.py
批量导出文件

**学习内容:**
- 批量处理文件
- 导出为 STEP 格式
- 错误处理

**运行前提:**
- `C:\parts\` 目录下有 .sldprt 文件
- 或修改代码中的路径

---

### 05_drawing.py
创建工程图

**学习内容:**
- 创建工程图文档
- 标准三视图
- 自动标注尺寸
- 添加注释
- 导出 PDF

**运行前提:**
- 需要一个零件文件: `C:\parts\mypart.sldprt`
- 或修改代码中的路径

---

### 06_friendly_api.py
使用更顺手的 `SolidWorksSession` API 创建并导出零件

**学习内容:**
- 使用 `SolidWorksSession` 新建、保存、导出文件
- 使用 `with sketch(...)` 自动进入/退出草图
- 减少硬编码草图名和重复路径处理

**运行前提:**
- SolidWorks 已运行，或允许脚本启动 SolidWorks
- 确保 `C:\temp\` 目录可写

---

### 07_motion_study_rotary_motor.py
给已有风扇装配体添加 Motion Study 旋转马达

**学习内容:**
- 创建并激活 Motion Study
- 加载 Motion Study 类型库
- 通过轴/叶轮圆柱面添加 60RPM 匀速旋转马达
- 计算并播放运动算例

**运行前提:**
- 需要一个已有风扇装配体
- 叶轮同心 Mate 未锁定旋转，且叶轮组件未固定
- 修改脚本中的 `assembly_path` 和组件名关键字

---

### 08_mini_fan_motion_assembly.py
自动生成桌面迷你散热风扇基准装配体

**学习内容:**
- 依次生成底座、中心立柱、带叶片叶轮、圆形防护前盖 4 个零件
- 自动新建装配体并添加组件
- 添加重合 Mate、同心 Mate，并保持叶轮同心 Mate 不锁旋转
- 固定底座、立柱、前盖，叶轮保持浮动
- 创建 Motion Study，添加 60RPM 匀速旋转马达
- 导出自审查报告

**运行前提:**
- SolidWorks 已运行，或允许脚本启动 SolidWorks
- `C:\temp\solidworks_mini_fan_demo\` 可写

**说明:**
- 该示例是稳定性基准，不是外观最终版。
- 圆角/倒角没有作为成功标准；边线自动选择在不同版本和语言环境下不稳定，后续应单独优化实体选择策略。

---

## 数据文件示例（无需 SolidWorks）

除上面 8 个真机脚本外，本目录还有可直接跑的**数据驱动**示例。它们不需要 SolidWorks，
由无头后端或独立 CLI 消费。

| 文件 | 用途 | 运行命令 |
|---|---|---|
| `part.cadstudio.json` | NeutralCadDocument 最小完整样例（盒体 + 圆柱凸台，含制造元数据） | `python scripts\cad_studio.py write-open-format --input examples\part.cadstudio.json --out-dir output --formats cadstudio step stl` |
| `supplier-profile.json` | DFM 供应商能力档案样例（`cadstudio.dfm-profile` 1.0） | 见下方 `check-dfm` |
| `fea_tetra_static.json` | 四面体线性静力分析请求 | `python scripts\cad_studio.py prepare-fea --input examples\fea_tetra_static.json --out-dir output\fea` |
| `ocp_loft_adapter.json` | OCP 受限封闭直纹 Loft 请求 | 由 `scripts/headless_cad_writer.py` 消费 |
| `weldment/coremark_hss1x1x16ga.json` | HSS 矩形焊接框架请求 | 见该文件头部注释 |
| `generate_fea_convergence_example.py` | 生成网格收敛算例 | `python examples\generate_fea_convergence_example.py` |

### `part.cadstudio.json` 的字段含义

```jsonc
{
  "documentId": "example-mounting-plate",   // 必需，非空
  "units": "mm",
  "metadata": { "manufacturing": {
      "process": "machining",               // 供 check-dfm 的 --process auto 读取
      "material": "AL6061-T6",              // DFM 必需
      "wallThickness": 8                    // DFM 必需，单位随 units
  }},
  "features": [                             // 必需，数组
    { "id": "plate", "type": "box",      "operation": "add", "parameters": { "length": 60, "width": 40, "height": 8 } },
    { "id": "boss",  "type": "cylinder", "operation": "add", "parameters": { "radius": 8, "height": 14, "segments": 64 } }
  ]
}
```

无头网格后端当前只支持 `box` 与 `cylinder` 的 `add` / `new` / `union` 操作；
`subtract` 等布尔操作会返回 `blocked` 并写入 `limitations`，不会写出不完整的网格冒充完整实体。

### DFM 检查

```powershell
python scripts\cad_studio.py check-dfm `
  --input .\examples\part.cadstudio.json `
  --output .\output\dfm.json `
  --profile "$PWD\examples\supplier-profile.json"
```

> `--profile` 的相对路径会被拒绝（`dfm_invalid_profile`：Profile 必须是存在的 `.json` 文件），
> 请使用绝对路径。上例用 PowerShell 的 `$PWD` 展开为绝对路径。

该样例的实际结果是 `review_required`（12 项检查：8 pass / 2 warning / 1 fail）：

| 状态 | 检查项 | 原因 |
|---|---|---|
| fail | `profile_internal_corner_tooling` | 盒体内角半径小于档案要求的 `minimumInternalCornerRadius: 1.5` |
| warning | `machining_internal_corner_radius` | 模型未声明内角半径/刀具半径（通用加工建议项） |
| warning | `hole_diameter_inventory` | 该样例不含孔特征，无法核对孔径清单 |

这是刻意的——示例要展示"档案能真正约束几何"，而不是永远全绿。

---

## 运行示例

1. 确保已安装依赖:
```bash
pip install pywin32
```

2. 启动 SolidWorks

3. 运行示例:
```bash
python 01_basic_part.py
```

## 修改路径

所有示例中的文件路径都可以根据你的实际情况修改:

```python
# 修改输入路径
part_path = r"C:\your\path\part.sldprt"

# 修改输出路径
output_path = r"C:\your\output\file.step"
```

## 常见问题

### 无法连接 SolidWorks?
确保 SolidWorks 已经运行,并且 Python 位数与 SolidWorks 一致(通常为 64 位)。

### 找不到基准面?
中英文版本的基准面名称不同:
- 英文: "Front Plane", "Top Plane", "Right Plane"
- 中文: "前视基准面", "上视基准面", "右视基准面"

### 特征创建失败?
检查:
1. 草图是否闭合
2. 单位是否正确(使用 `mm()` 函数)
3. 实体选择是否正确

更多问题请查看 [troubleshooting.md](../references/troubleshooting.md)
