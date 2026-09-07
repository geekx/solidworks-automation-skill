# 设计规则检查（DRC / 设计审计）

`scripts/design_rule_check.py` 是面向**设计意图层**的规则检查器，与 `dfm_review.py`（制造性）分工：

- **DFM**：能不能按某工艺造出来（壁厚、割缝、成形空间、供应商能力…）。
- **DRC**：设计本身是否合理、完整、自洽（几何健全性、孔位规则、标准件合理性、装配约束、
  工程图完整性），按严重度聚合成一份可审计报告。

两者都消费同一个 NeutralCadDocument，可组合使用；DRC 不重复实现 DFM/几何/图纸审查，而是把它们
作为证据源。

## 通过 MCP 调用与补充

DRC 面向 harness 通过 MCP 使用：

- `cadstudio_list_drc_rules`：列出内置规则、默认阈值和自定义规则 schema。**先调它**，让代理知道
  哪些能配置。
- `cadstudio_check_drc`：在中性文档上运行检查，可传入声明式 Profile（内联对象或文件路径）。

CLI 等价：

```bash
python scripts/cad_studio.py list-drc-rules
python scripts/cad_studio.py check-drc --input part.cadstudio.json --output out/drc.json --profile strict.json
```

## 自然语言配置与补充（声明式 Profile）

规则集通过 **Profile**（`scripts/design_rule_profiles.py`，schema `cadstudio.drc-profile`）配置。Profile
是纯声明式数据，**不包含也不执行任何代码或路径**，因此代理可以安全地把用户的自然语言翻译成 Profile：

用户说：“螺纹孔到边缘至少 2 倍孔径、壁厚不低于 1.5mm、弹簧指数控制在 5 到 10、孔径小于 3mm 要报警”

代理生成：

```json
{
  "schema": "cadstudio.drc-profile",
  "thresholds": {
    "holeEdgeDistanceRatio": 2.0,
    "minWallThicknessMm": 1.5,
    "springIndexMin": 5.0,
    "springIndexMax": 10.0
  },
  "disabledRules": [],
  "customRules": [
    {"id": "CUST-SMALL-HOLE", "appliesTo": "hole", "field": "diameter", "operator": "lt", "value": 3.0, "severity": "warning", "message": "孔径小于3mm，注意钻头刚性"}
  ]
}
```

- `thresholds`：覆盖内置规则阈值（白名单字段，见 `THRESHOLD_FIELDS`）。
- `disabledRules`：停用指定规则 id。
- `customRules`：**补充**数据驱动的新规则——选定 `appliesTo`（hole/feature/standard_part/document）、
  `field`、`operator`（lt/lte/gt/gte/eq/ne）、`value` 与 `severity`，命中即报。

多个 Profile 合并时，minimum 类阈值取更严值，disabledRules 取并集，customRules 按 id 去重。

## 内置标准规则

| 规则 | 类别 | 说明 | 阈值 |
|---|---|---|---|
| DRC-GEO-001 | geometry | 最小壁厚 | minWallThicknessMm |
| DRC-HOLE-001 | holes | 孔到边缘距离 | holeEdgeDistanceRatio / holeEdgeDistanceMinMm |
| DRC-HOLE-002 | holes | 孔间距 | holeSpacingRatio |
| DRC-HOLE-003 | holes | 螺纹啮合深度 | threadEngagementRatio |
| DRC-HOLE-004 | holes | 孔尺寸有效性 | — |
| DRC-STD-001 | standard_parts | 齿轮尖齿 | — |
| DRC-STD-002 | standard_parts | 齿轮根切齿数 | gearMinTeeth |
| DRC-STD-003 | standard_parts | 弹簧指数范围 | springIndexMin/Max/HardMin/HardMax |
| DRC-STD-004 | standard_parts | 弹簧实体/自由长度 | — |
| DRC-STD-005 | standard_parts | 链轮最小齿数 | sprocketMinTeeth |
| DRC-ASM-001 | assembly | 装配干涉 | — |
| DRC-ASM-002 | assembly | 装配欠约束 | — |
| DRC-DRW-001 | drawing | 缺必需尺寸 | — |
| DRC-DRW-002 | drawing | 尺寸缺公差 | — |

## 中性文档需要的截面

- `features[]` 中 `type=hole` 的 `parameters`：diameter/radius、depth、edgeDistance、thread、
  threadNominal、threadEngagement、x、y。
- `metadata.design.minWallThicknessMm`（或退化到 `metadata.manufacturing.wallThickness`）。
- `standardParts[]`（或 `metadata.standardParts`）：`{type, teeth, is_pointed, spring_index,
  solid_length_mm, free_length_mm, ...}`，可直接由 `standard_parts_geometry.py` 的派生量填充。
- `metadata.assembly`：componentCount、mateCount、interferences[] 或 interferenceCount。
- `metadata.drawing`：requiredDimensionsMissing[]、dimensionsWithoutTolerance。

缺失的截面按 `info` 跳过，**不代表合格**。报告始终 `reviewRequired=true`。

## 边界

- 从活动 SolidWorks 模型抽取快照为 pilot，需真机验证。
- DRC 不判定安全/认证，只做规则级审计；fail/warning 都要工程复核。
- 新规则优先用声明式 customRules 表达；确需复杂逻辑时再在 `BUILTIN_RULES` 增补纯函数并补单测。
