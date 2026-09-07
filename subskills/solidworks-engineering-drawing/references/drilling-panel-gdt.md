# 非标钻孔台面板：孔位规整 + 基准推测 + GD&T 自动出图

`scripts/drilling_panel_gdt.py` 面向「一块布满孔的非标自动化台面板从 3D 自动出图到 SLDDRW」这一
极端案例，把最难自动化、最有价值的**分析推理**做成纯函数、可离线单测，输出一份通过本子技能
schema 的 `drawing_spec`，再交给现有渲染器落图。

## 三件事

1. **孔位规整（detect_hole_patterns）**：按（孔径, 孔型）分组，一维聚类识别栅格/线性/离散阵列，
   给出行列数、节距、原点与规整度（`regular`/`complete`）。不同孔型（tapped/clearance/dowel）即使
   同名义直径也分开成组，避免把定位销并进螺纹孔栅格。
2. **基准自动推测（infer_datums）**：按 3-2-1 原则，从包围盒取基准角（默认左下角），沿长边定 B、
   短边定 C，A 为面板主平面；识别到恰好 2 个 dowel/小孔时列为基准孔候选，可改用基准孔目标。
3. **GD&T 自动给出（generate_gdt）**：为每组孔阵列生成 GB/T 1182 / ISO GPS 位置度框
   `⌖ ⌀t Ⓜ | A | B | C`（间隙孔默认最大实体 MMC），定位销孔位置度从严且 RFS；基准 A 加平面度
   `⏥ t`（区域公差，不带 ⌀）。

配套 `build_hole_table`（孔表：标签/规格/数量/阵列描述/相对基准原点）与
`build_ordinate_dimensions`（从基准原点的 X/Y 坐标基线）。

## 用法

```bash
python subskills/solidworks-engineering-drawing/scripts/drilling_panel_gdt.py \
  panel.cadstudio.json --source-model panel.SLDPRT \
  --analysis-out out/panel_analysis.json --spec-out out/panel_spec.json
```

输入是 NeutralCadDocument：`features[type=hole].parameters` 提供 `x`、`y`、`diameter`（或 `radius`）、
`holeKind`（tapped/clearance/dowel）；`metadata.plate` 提供 `widthMm`/`heightMm`/`thicknessMm`。
输出 `analysis`（阵列/基准/GD&T/孔表/坐标）与 `drawing_spec`（含 professionalAnnotations 的
datums、geometricTolerances、holeCallouts、centerMarks，holeRequirements 精确孔位，requiredDimensions
坐标基线与 GD&T）。

## 与渲染器的分工

- 分析/规划为纯函数，离线单测（`tests/test_drilling_panel_gdt.py`），并且生成的 `drawing_spec`
  已用本子技能 `validate_drawing_spec` 通过 schema 校验。
- 真正把基准标签、位置度框、孔表和坐标标注**落到 SLDDRW** 仍由本子技能的专项 COM 脚本执行
  （`InsertDatumTag2`、GTOL、孔标注等），为 `pilot`，须真机与工程复核；生成器不得声称已自动交付图纸。

## 边界与约定

- 单位统一 mm；坐标系为面板正面 XY，基准原点默认包围盒左下角。
- 聚类容差 `cluster_tol_mm` 默认 0.5，节距均匀容差 0.2；面板孔位偏差大或存在斜置阵列时需调参并复核。
- 斜向阵列、极坐标阵列、沉头/阶梯孔的多段标注暂按离散簇处理，交由人工补充。
- 位置度/平面度默认值来自 `default_gdt_profile()`，可用 profile 覆盖；最终公差必须按图纸功能与
  配合要求由工程师确认，本工具只做首版自动建议。
