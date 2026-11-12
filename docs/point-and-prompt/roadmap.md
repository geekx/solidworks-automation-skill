# SOLIDWORKS Point & Prompt —— Claude Design 风格交互路线图

> 项目目标：在现有 `solidworks-automation-skill` 基础上，增加一种类似 Claude Design
> 的画布式交互体验：用户在 SOLIDWORKS 图形区悬停、点击或框选几何对象，在对象附近/右侧
> 发起自然语言指令；AI 理解所指对象及其设计上下文，提出修改方案，用户确认后调用现有
> 自动化能力执行，并在模型上反馈结果。
>
> 项目代号：**SOLIDWORKS Point & Prompt**\
> 交互范式：**Hover → Select/Pin → Prompt → Preview → Apply → Inspect**\
> 目标版本：SOLIDWORKS 2024 / 2025\
> 文档状态：Implementation Roadmap v1.2

## 文档信息

| 项 | 内容 |
|---|---|
| 版本 | v1.2 |
| 更新日期 | 2026-11-12 |
| 相对 v1.1 的变更 | 合并「同窗口并行建模/画图/评估」运行时流水线为新的 7.1；新增 7.2 非标自动化案例；废弃旧 7.1 的 Cursor 开发编组定义；按并行流水线重写第 12 节开发顺序 |
| 实现状态 | 交互层 Phase 0–4 已按本路线图落地代码骨架，`capabilities.yaml` 中的新能力默认 `pilot` 或 `not_implemented`，以真机证据升等级 |

> **阅读顺序建议：** 第 0–6 节定义交互层与执行契约；第 7 节给出分阶段计划与
> 运行时并行流水线（7.1）及其非标案例（7.2）；第 8–11 节是 MVP 范围、测试与验收；
> 第 12 节是当前实际开发顺序。

## 0. 结论与实施原则

这不是另起炉灶开发一套 CAD 自动化系统，而是在现有 Skill / MCP / CAD
Studio 能力上增加一个**交互式上下文层**。

### 已有基础（来自当前仓库资料）

-   根 Skill 已有统一能力清单 `capabilities.yaml`，能力等级包括
    `verified`、`pilot`、`reference_only`、`not_implemented`。
-   已有本地 `stdio` MCP Server：`mcp-server/server.py`。
-   已有 C# Add-in
    宿主相关入口：`scripts/sw_addin_host.ps1`、`scripts/sw_addin_host.py`，并有
    `references/solidworks-addin-host.md`。
-   仓库已将事件订阅、PropertyManagerPage、TaskPane、长期驻留 UI 归入
    `solidworks_addin_ui_events` 路由，优先使用 C# Add-in。
-   已有语义实体引用模块：`scripts/sw_entity_reference.py`，设计方向是逐步替代
    `Face1/Edge1` 和屏幕坐标。
-   已有 Python / C# PIA Add-in / 原生 C++ / OCCT 等后端路由，不应绕过
    `backend_router.py` 自行假定后端。
-   已有修改后重建、导出多视图预览、`sw_review.py` 自审查流程。

**因此第一步不是直接新建 Add-in，而是审计并复用现有 Add-in
宿主、注册流程、诊断状态和语义实体引用能力。** 如果当前宿主缺少图形区
Hover、Selection 事件或 TaskPane 交互，再在原结构上补齐。

### 核心原则

1.  **交互像 Claude Design，执行仍然是 CAD 原生语义。**
    用户点选的是画布对象，但 AI 最终应理解其所属
    Feature、Sketch、Dimension、Component，而不是只对屏幕坐标做动作。
2.  **Add-in 管实时交互，MCP 管工具调用，Skill/Agent 管理解和规划。**
3.  **悬停只预览，不执行；点击/固定后才建立修改上下文。**
4.  **先计划、再预览、后确认执行。**
    默认不允许自然语言直接触发不可逆的复杂修改。
5.  **不长期缓存 Face/Edge COM 对象。**
    模型重建后拓扑引用可能失效，必须用语义引用和版本校验重新解析。
6.  **复用现有能力，不重复造轮子。** 已有
    `sw_part.py`、`sw_assembly.py`、`sw_entity_reference.py`、`sw_review.py`、现有
    MCP 工具和 `capabilities.yaml` 优先复用。
7.  **版本声明必须基于证据。** 仓库资料显示 SW2026 的 Add-in
    宿主已有验证记录，SW2025 是兼容目标；本项目目标是
    2024/2025，必须分别做真机验证，不能把 SW2026 通过等同于 SW2024/2025
    通过。

------------------------------------------------------------------------

## 1. 产品体验：复刻 Claude Design 的交互逻辑

### 1.1 目标体验

用户不需要知道 Feature Tree 中对象叫什么，也不需要写 API 命令：

1.  鼠标移动到模型上的面、边、顶点或组件。
2.  目标对象产生轻量高亮，AI 面板显示对象类型和简短身份信息。
3.  用户点击对象，或使用快捷键/右键菜单将对象 **Pin（固定）**
    为当前上下文。
4.  在右侧 Task Pane 或对象附近的轻量 Prompt 输入框描述修改要求。
5.  AI 返回一个带目标对象、修改方式、参数变化和风险的修改卡片。
6.  用户预览并确认。
7.  Add-in 调用现有 MCP / CAD 执行链路。
8.  模型重建后回读参数、检查结果，并在画布中反馈成功/失败。
9.  用户可以 Undo、再次追问或继续选择下一个对象。

### 1.2 Claude Design 模式与 CAD 对应关系

  --------------------------------------------------------------------------------
  Claude Design 类交互                SOLIDWORKS 对应实现
  ----------------------------------- --------------------------------------------
  Hover on canvas                     鼠标预选几何对象，显示临时高亮

  Inline comment / point-and-prompt   对固定对象添加一条带锚点的修改请求

  Pin selected element                固定 Face / Edge / Feature / Component
                                      上下文

  Multi-select                        多选对象，形成 Selection Set

  Canvas preview                      模型临时预览、参数差异、临时特征或隔离配置

  Apply changes                       确认后执行 CAD API 操作

  Comment thread / iteration          围绕同一对象持续追问、修改、审查

  Global chat                         不绑定单一对象的整模型/装配体级任务
  --------------------------------------------------------------------------------

### 1.3 交互状态机

``` text
IDLE
  └─ 鼠标悬停 → HOVER_PREVIEW
       ├─ 离开对象 → IDLE
       └─ 点击 / Pin → TARGET_LOCKED
             └─ 输入自然语言 → PROMPTING
                   └─ AI 解析 → PLAN_READY
                         ├─ 用户修改要求 → PROMPTING
                         ├─ 取消 → TARGET_LOCKED
                         └─ 预览 → PREVIEWING
                               ├─ 取消预览 → PLAN_READY
                               └─ 确认 → EXECUTING
                                     ├─ 成功 → VERIFYING → APPLIED
                                     └─ 失败 → ROLLING_BACK → FAILED
```

**重要交互规则：** - Hover
不触发大模型请求；本地先完成对象拾取与轻量信息展示。 - Hover
事件做节流/去重，只在对象身份变化时更新 UI。 - Pin
后锁定上下文，鼠标移动到其他对象不能悄悄改变目标。 - `Esc` 先关闭
Prompt/预览，再解除 Pin；不应直接取消整个模型操作。 -
多选集合应可逐个移除、重新高亮和查看对象详情。 -
选中对象丢失或模型版本变化时，必须提示重新定位，禁止静默修改相似对象。

------------------------------------------------------------------------

## 2. 目标架构

``` text
┌─────────────────────────────────────────────────────┐
│                 Agent / Skill Client                │
│  意图理解 · 对象上下文推理 · 修改计划 · 风险说明       │
└────────────────────────┬────────────────────────────┘
                         │ MCP tools / structured result
┌────────────────────────▼────────────────────────────┐
│                 Existing MCP Server                 │
│  mcp-server/server.py                                │
│  复用现有 solidworks_* 工具                           │
│  新增 context / plan / apply / verify 工具            │
└────────────────────────┬────────────────────────────┘
                         │ 本机 IPC / 命令队列
                         │ JSON 消息，不传递 COM 对象
┌────────────────────────▼────────────────────────────┐
│           C# SOLIDWORKS Add-in / Interaction Host   │
│                                                     │
│  Hover Monitor      Selection / Pin Manager         │
│  Context Builder    TaskPane / Inline Prompt        │
│  Highlight Manager  Preview & Apply Coordinator     │
│  COM Main-thread Dispatcher                          │
└────────────────────────┬────────────────────────────┘
                         │ SOLIDWORKS API / COM
┌────────────────────────▼────────────────────────────┐
│               SOLIDWORKS 2024 / 2025                │
│  Part · Assembly · Drawing · FeatureManager         │
└─────────────────────────────────────────────────────┘
```

### 2.1 各层职责

  ----------------------------------------------------------------------------------------------------
  层                      职责                                                 不负责什么
  ----------------------- ---------------------------------------------------- -----------------------
  Skill / Agent           理解自然语言、判断设计意图、选择工具、生成修改计划   不直接持有 COM
                                                                               对象，不模拟鼠标

  MCP Server              提供结构化工具、参数校验、任务路由、返回执行证据     不监听高频
                                                                               Hover，不直接承担 UI

  C# Add-in               鼠标/选择事件、Pin、TaskPane、对象高亮、COM          不负责大模型推理
                          主线程调度                                           

  现有 CAD 脚本/模块      具体建模、装配、审查、导出等操作                     不重复实现交互 UI

  `capabilities.yaml`     能力状态和后端路由的唯一真源                         不允许 UI
                                                                               自己宣称某能力已验证
  ----------------------------------------------------------------------------------------------------

### 2.2 通信建议

优先采用**本机 Named Pipe 或现有宿主已经使用的 IPC 方式**，而不是把
Hover 事件塞进 MCP stdio 通道。

-   Hover：Add-in 本地处理；只有对象身份变化时更新 TaskPane。
-   Pin：Add-in 生成 `SelectionContext`，通过 IPC 暴露给 MCP。
-   Prompt：Agent 通过 MCP 读取已 Pin 的上下文。
-   Apply：MCP 发出结构化操作请求，Add-in 在 SOLIDWORKS 主线程执行。
-   Result：Add-in 返回 JSON 结果和验证证据。

如果现有 Add-in Host 已有状态文件、命令队列或
IPC，先沿用现有机制，避免平行维护两套通信协议。

**COM 线程约束：** 所有 SOLIDWORKS COM 对象读取和写入必须在正确的 STA /
SOLIDWORKS UI 执行上下文内串行执行。后台线程只处理
JSON、自然语言和纯计算，不得把 `Face2`、`Feature`、`ModelDoc2` 等 COM
RCW 传到任意线程使用。

------------------------------------------------------------------------

## 3. 核心模块设计

建议在现有 Add-in
项目中扩展，不预设必须新建同名工程。以下是逻辑模块建议。

  ------------------------------------------------------------------------------------------------------
  模块                    建议文件/类                        主要职责
  ----------------------- ---------------------------------- -------------------------------------------
  鼠标预选                `HoverMonitor`                     获取预选对象、类型、位置；事件节流和去重

  选择管理                `SelectionManager`                 正式选择、多选、清除、Pin/Unpin

  上下文构建              `SelectionContextBuilder`          组合文档、配置、对象、几何、特征信息

  语义追溯                `FeatureResolver`                  Face/Edge → Body → Feature →
                                                             Sketch/Dimension

  实体引用                复用                               创建可重新解析的实体标识，不依赖
                          `scripts/sw_entity_reference.py`   Face1/Edge1
                          的语义思路                         

  高亮管理                `HighlightManager`                 Hover 高亮、Pin 高亮、修改结果高亮

  Prompt UI               `TaskPane / InlinePrompt`          输入、目标摘要、对话线程、修改卡片

  计划管理                `EditPlanService`                  将 AI 意图转换为白名单 CAD 操作计划

  执行协调                `ApplyCoordinator`                 确认、执行、重建、校验、失败处理

  事务记录                `OperationJournal`                 记录参数前后值、目标、操作、Undo/恢复信息

  通信                    `CadBridge`                        与现有 MCP Server 交换 JSON 请求/结果
  ------------------------------------------------------------------------------------------------------

### 3.1 对象类型分层

第一版只承诺可靠支持有限对象类型，不要一开始宣称"任何位置都能改"。

  对象                 首期行为                                 后续能力
  -------------------- ---------------------------------------- ------------------------
  Face                 识别平面/圆柱/圆锥等几何，追溯所属特征   面偏移、局部结构修改
  Edge                 识别直线/圆弧/圆边，追溯相邻面和特征     圆角、倒角、边界编辑
  Vertex               记录三维位置和关联边                     定位、尺寸/约束辅助
  Feature              直接读取特征类型和参数                   修改拉伸、切除、圆角等
  Sketch / Dimension   读取草图实体、约束和尺寸                 修改尺寸、约束关系
  Component            获取组件路径、实例、变换矩阵             替换、定位、Mate 修改
  Drawing Entity       识别视图、尺寸、注释等                   工程图标注和属性修改

### 3.2 Feature Resolver 策略

点选一个面不等于知道应修改哪个特征。必须建立逐级解析：

``` text
Selected Face / Edge
       ↓
Owning Body / Adjacent Geometry
       ↓
Candidate Feature(s)
       ↓
Parent Sketch / Feature Parameters
       ↓
Editable Design Intent
```

解析结果应返回候选列表及置信度，而不是强行返回唯一 Feature。

-   若唯一、可编辑且关系明确：可提出参数修改计划。
-   若存在多个候选：让用户在高亮候选之间确认。
-   若为导入实体、复杂曲面或历史特征不可追溯：说明限制，转为新增特征或显式几何操作计划。
-   不允许因为某个 Face 当前是 `Face3` 就将其视为永久身份。

------------------------------------------------------------------------

## 4. SelectionContext 数据协议

建议所有 Add-in / MCP / Agent 之间只传递 JSON DTO，不传递 COM 对象。

示意结构：

``` json
{
  "schemaVersion": "1.0",
  "document": {
    "documentToken": "doc-token",
    "path": "C:/work/Bracket.SLDPRT",
    "title": "Bracket",
    "documentType": "Part",
    "configuration": "Default"
  },
  "modelState": {
    "revision": 42,
    "dirty": true,
    "units": "MMGS"
  },
  "selection": {
    "selectionToken": "sel-token",
    "mode": "pinned",
    "objects": [
      {
        "entityToken": "entity-token",
        "entityType": "Face",
        "geometryType": "Cylinder",
        "persistentReference": "opaque-reference",
        "approximateGeometry": {
          "diameterMm": 6.0,
          "axis": [0, 0, 1],
          "centerMm": [20, 10, 5]
        },
        "featureCandidates": [
          {
            "featureName": "Cut-Extrude3",
            "featureType": "Cut",
            "confidence": 0.93
          }
        ]
      }
    ]
  }
}
```

说明： - 上述 JSON 仅为协议示意，不代表所有字段都能从每一种 SOLIDWORKS
对象直接取得。 - `persistentReference` 应由 SOLIDWORKS
支持的持久引用机制生成并在使用前重新解析；无法持久化的对象必须提供失效处理。 -
`documentToken`、`selectionToken`、`modelState.revision`
用于防止跨文档、跨配置、跨重建误操作。 -
坐标、长度、角度必须明确单位；内部 API 常用 SI 单位，向 AI
展示时统一格式化为用户文档单位。 - 不把完整模型树、全部几何或完整 COM
属性无差别发送给模型；采用按需扩展的上下文。

### 4.1 上下文分级加载

  级别   内容                                        何时加载
  ------ ------------------------------------------- --------------------
  L0     文档名、对象类型、对象 token                Hover / Pin
  L1     几何类型、尺寸、坐标、组件名                Pin 后
  L2     所属 Feature、父级 Sketch、相关 Dimension   用户开始提出修改时
  L3     邻接关系、相关组件、装配 Mate、周边特征     AI 判断需要时
  L4     整个模型树、全局约束、整机上下文            明确提出全局任务时

------------------------------------------------------------------------

## 5. MCP 工具规划

先检查 `mcp-server/server.py`
和现有工具定义，复用已有工具。下表是新增能力的逻辑接口，不要求全部都暴露成
MCP Tool；高频 Hover 尤其不应变成模型工具调用。

  --------------------------------------------------------------------------------------------------
  工具建议名                                作用                             阶段
  ----------------------------------------- -------------------------------- -----------------------
  `solidworks_get_pinned_context`           获取当前 Pin 对象的上下文        P0

  `solidworks_get_entity_details`           按 entity token                  P0
                                            扩展几何/特征信息                

  `solidworks_resolve_feature_candidates`   返回候选 Feature 和解析证据      P0

  `solidworks_preview_edit_plan`            校验并预览修改计划，不写模型     P0

  `solidworks_apply_edit_plan`              用户确认后执行白名单修改计划     P0

  `solidworks_verify_edit_result`           重建后回读参数、几何和模型状态   P0

  `solidworks_highlight_entities`           高亮指定语义实体                 P1

  `solidworks_pin_selection`                Pin 当前选择集                   P1

  `solidworks_unpin_selection`              解除 Pin                         P1

  `solidworks_get_selection_history`        查看本次交互的目标和操作历史     P1

  `solidworks_undo_last_ai_operation`       回退最近一次 AI 操作             P1
  --------------------------------------------------------------------------------------------------

### 5.1 修改计划必须结构化

不要让 Agent 生成任意 Python/VBA
代码后直接执行。建议采用白名单操作计划：

``` json
{
  "planId": "plan-001",
  "target": {
    "documentToken": "doc-token",
    "selectionToken": "sel-token",
    "entityToken": "entity-token"
  },
  "operation": {
    "kind": "modify_sketch_dimension",
    "feature": "Cut-Extrude3",
    "dimension": "D1@Sketch4",
    "oldValue": 0.006,
    "newValue": 0.008,
    "unit": "m"
  },
  "invariants": [
    "preserve_hole_center",
    "preserve_feature_suppression_state"
  ],
  "risk": "low",
  "requiresConfirmation": true
}
```

执行前必须校验： - 文档仍然是同一个文档和配置。 - 模型 revision
与计划生成时一致，或已重新验证。 - 目标实体仍可解析，且仍属于预期
Feature。 - 旧值与当前回读值一致。 - 新值在允许范围内。 - 该操作已在
`capabilities.yaml` 中声明并有相应能力等级。 -
操作不会静默修改非目标配置或其他组件。

------------------------------------------------------------------------

## 6. 修改安全与验证机制

### 6.1 修改分级

  -----------------------------------------------------------------------------------------------------------
  风险级别                例子                                         默认策略
  ----------------------- -------------------------------------------- --------------------------------------
  Low                     修改已识别草图尺寸、特征深度                 展示差异，用户确认后执行

  Medium                  新增孔、圆角、倒角、局部切除                 先预览/计划，明确确认后执行

  High                    改动基准、父级草图、关键配合、跨零件结构     展示影响范围，逐项确认

  Critical                删除大量特征、重构树、批量替换、覆盖原文件   默认禁止自动执行，另存副本并人工确认
  -----------------------------------------------------------------------------------------------------------

### 6.2 执行事务

``` text
Validate Document + Selection Token
              ↓
Read Current Parameters
              ↓
Create Operation Journal
              ↓
Apply Whitelisted CAD Operation
              ↓
Force Rebuild
              ↓
Read Back Feature / Dimension
              ↓
Run Geometry & Model Checks
       ┌──────┴──────┐
       │             │
     PASS           FAIL
       │             │
 Commit Journal   Rollback / Restore
       │             │
 Update UI        Return Failure Evidence
```

### 6.3 每次修改的验收证据

-   COM 调用结果及错误信息。
-   修改前后参数值。
-   `ForceRebuild3` / 重建结果。
-   目标 Feature 是否仍存在、是否报错。
-   关键几何测量是否符合计划。
-   模型是否有意外实体/零件变化。
-   必要时调用 `scripts/sw_review.py` 生成多视图预览和审查报告。
-   保存动作与另存副本策略。
-   事务日志和回退结果。

**注意：** SOLIDWORKS Undo
并不等于完整事务回滚。对于高风险操作，优先在副本/临时配置上试执行；恢复参数、重建和回读仍需验证。

------------------------------------------------------------------------

## 7. 分阶段开发路线图

以一个熟悉现有仓库、Python、C# 和 SOLIDWORKS API
的开发者估算。时间是开发排期参考，不是能力保证；版本适配、COM
调试和真机回归可能拉长周期。

### Phase 0 --- 仓库审计与基线确认（1--2 天）

**目标：确认现有能力，避免重复开发。**

任务： - \[ \] 阅读 `references/solidworks-addin-host.md`。 - \[ \] 检查
`scripts/sw_addin_host.ps1`、`scripts/sw_addin_host.py` 和实际 Add-in
工程目录。 - \[ \] 检查 `host-status.json`、注册脚本、64 位 TLB
注册和加载诊断。 - \[ \] 检查 `capabilities.yaml` 中
`solidworks_addin_ui_events` 的定义与等级。 - \[ \] 检查
`scripts/sw_entity_reference.py` 的语义引用协议。 - \[ \] 检查
`mcp-server/server.py`、`mcp-server/README.md` 和现有 MCP
工具命名/错误格式。 - \[ \] 在 SW2024、SW2025 分别运行 Add-in
加载、连接、文档切换、退出测试。 - \[ \] 形成"复用 / 补齐 / 新增"清单。

**交付物：** - `docs/point-and-prompt/audit.md` - SW2024 / SW2025
基线测试记录 - Add-in / MCP 现有能力映射表

**退出条件：** 能明确现有 Add-in
宿主是否可直接扩展，且两个目标版本的加载问题有可复现记录。

### Phase 1 --- Hover & Pin 交互原型（3--5 天）

**目标：先实现 Claude Design 式"指哪儿、锁哪儿"，不修改模型。**

任务： - \[ \] 增加图形区预选对象监听。 - \[ \] 支持
Face、Edge、Vertex、Feature、Component 的基础类型识别。 - \[ \] Hover
高亮和轻量状态展示。 - \[ \] Hover 节流、去重、异常清理。 - \[ \] 点击
Pin / Unpin。 - \[ \] `Esc` 取消、文档切换时清理上下文。 - \[ \]
TaskPane 显示当前目标摘要。 - \[ \] 多选对象形成 Selection
Set（可先做基础版）。

**交付物：** - `HoverMonitor` - `SelectionManager` -
`HighlightManager` - TaskPane 交互原型 - 录屏或截图证据

**验收：** - 鼠标指向不同面/边时，高亮对象与显示信息正确。 - Pin
后移动鼠标不改变目标。 -
清除选择、切换文档、关闭文档后无残留引用或崩溃。 - Hover
不触发大模型调用，不产生明显卡顿。

### Phase 2 --- SelectionContext & Feature Resolver（4--7 天）

**目标：从"选中一个面"提升到"理解这个面代表什么"。**

任务： - \[ \] 定义 `SelectionContext` JSON Schema。 - \[ \] 加入
DocumentToken、SelectionToken、ModelRevision。 - \[ \]
采集几何类型、位置、方向、尺寸和所属 Component。 - \[ \] 复用/扩展
`sw_entity_reference.py`。 - \[ \] 建立 Face/Edge → Body → Feature
候选追溯。 - \[ \] 识别父级 Sketch 和关联 Dimension。 - \[ \]
处理导入实体、镜像/阵列、派生零件和多候选情况。 - \[ \] 增加 token
过期、模型重建后重新解析机制。

**交付物：** - `SelectionContext` schema - `FeatureResolver` -
`entity-token` 生命周期与失效策略 - 几何对象解析测试集

**验收：** - 选中测试孔面，能返回几何尺寸和候选切除特征。 -
选中圆边，能返回相邻面/边及相关候选特征。 -
对导入实体或无法追溯的面明确返回 `unresolved`，不伪造 Feature 归属。 -
模型重建后旧 token 能被判定为有效、需重解析或失效。

### Phase 3 --- Inline Prompt & Edit Plan（3--5 天）

**目标：实现点选对象后自然语言描述修改，先只生成计划。**

任务： - \[ \] TaskPane / Inline Prompt 输入框。 - \[ \] Prompt 自动绑定
Pin 的 SelectionContext。 - \[ \] 对话线程保存目标锚点。 - \[ \] AI
生成结构化 EditPlan。 - \[ \]
显示修改目标、旧值、新值、修改方式、保持不变条件。 - \[ \]
发现歧义时要求用户选择候选目标。 - \[ \]
增加计划预览、取消、重新描述。 - \[ \] 将计划 Schema 与 MCP
工具契约固定下来。

**交付物：** - Inline Prompt UI - `EditPlan` JSON Schema -
修改计划卡片 - MCP context / plan 工具

**验收：** - 点击一个孔面并输入"把这个孔改成 Ø8，中心位置不变"。 - AI
能提出"修改哪个草图尺寸/特征"的明确计划。 -
不确定时会澄清，而不是直接执行。 - 本阶段不写入模型。

### Phase 4 --- Apply / Rebuild / Verify 闭环（4--7 天）

**目标：完成第一个真正可用的 Point & Prompt 闭环。**

首批只支持低风险、已有成熟封装的参数修改： - \[ \]
已定位草图尺寸修改。 - \[ \] 拉伸/切除深度修改。 - \[ \]
已识别圆角/倒角参数修改（能力等级允许时）。 - \[ \]
执行前重新校验文档、配置、selection token 和旧参数值。 - \[ \]
用户确认后执行。 - \[ \] 强制重建并回读新参数。 - \[ \]
生成修改前后差异。 - \[ \] 失败时恢复参数/撤销并验证。 - \[ \] 调用现有
`sw_review.py` 做必要的模型审查。

**交付物：** - `ApplyCoordinator` - `OperationJournal` - Apply / Verify
MCP 工具 - 第一版端到端 Demo

**验收 Demo：** 1. 打开一个简单支架零件。 2. Hover 一个圆孔面。 3. Pin
该面。 4. 输入"把这个孔改成 Ø8，保持中心位置不变"。 5. AI 显示修改计划。
6. 用户确认。 7. 模型完成重建。 8. 回读孔径为 8 mm，中心坐标不变。 9. UI
显示修改成功和差异。 10. Undo 后回读恢复为原始值。

### Phase 5 --- Claude Design 式体验完善（5--8 天）

**目标：让它从"能用"变成"好用"。**

任务： - \[ \] 多对象 Pin / Selection Set。 - \[ \] 对象附近的轻量
Prompt / 注释锚点。 - \[ \] 多条修改请求列表，可逐条执行或取消。 - \[ \]
对话线程绑定目标对象。 - \[ \] 修改前后模型状态对比。 - \[ \]
执行结果在画布上高亮。 - \[ \] 重新定位目标、编辑原请求、再次执行。 - \[
\] 支持"这个"和"刚才那个"指代的上下文解析。 - \[ \] 支持全局 Chat 与局部
Inline Prompt 共存。 - \[ \] 操作历史、撤销、重做入口。

**交付物：** - Point & Prompt 交互 UI v1 - 多对象上下文与线程模型 -
操作历史面板 - 用户交互测试记录

### Phase 6 --- 扩展到装配体、工程图与设计审查（按需排期）

  ---------------------------------------------------------------------------------------
  场景                    示例指令                         依赖
  ----------------------- -------------------------------- ------------------------------
  装配体组件              "这个气缸换成 50 缸径型号"       Component
                                                           上下文、文件替换、Mate 复核

  装配定位                "这个支架向右移动 20 mm"         组件变换、Mate 冲突检查

  干涉检查                "检查这个零件与周围零件的干涉"   装配体邻域和干涉分析

  工程图                  "给这个孔增加位置度标注"         Drawing Entity、基准与 GD&T
                                                           语义

  制造审查                "这个面加工是否有风险"           复用 DFM / DRC 能力

  结构审查                "这个位置是否需要加强筋"         设计规则、工程知识和人工确认

  参数族                  "这个支架生成 3 种厚度配置"      Configuration / Design Table
                                                           能力
  ---------------------------------------------------------------------------------------

这些是扩展方向，不应纳入第一版 MVP 的验收承诺。

------------------------------------------------------------------------

### 7.1 同窗口并行建模、画图与评估（Cursor Swarm 运行时）

**目标：** Coordinator + 2–5 个 Agent 在**同一个 SOLIDWORKS 窗口、同一份模型上下文**里同时推进三条工作流：建模 Agent 改几何，绘图 Agent 出图标注，评估 Agent 审查验证。三者不是“你做完我再做”，而是**微操作级交错 + 计算级并行**：写操作进入串行执行器快速落地，规划、评估、标注准备在旁路并行计算，重建后立即汇合校验。

**核心判断：** SOLIDWORKS 的 COM 写操作必须串行，但**“串行”不等于“排队等整轮”**。把写操作切到微操作粒度（一个尺寸、一个特征、一次视图更新），每次占用主线程几十到几百毫秒，三条线就能以交错方式同步推进；评估和绘图准备完全可以放到主线程之外真并行。

#### 7.1.1 角色与职责

| 角色 | 主要职责 | 并行方式 |
|---|---|---|
| **Coordinator** | 意图拆解、任务卡、revision 流管理、冲突裁决、用户确认、提交/回退决策 | 全程在线，订阅所有 Agent 状态 |
| **建模 Agent** | 生成 EditPlan 微操作（改尺寸、加特征、调参数），提交到写执行队列 | 规划并行；落地串行 |
| **绘图 Agent** | 视图生成、尺寸/公差/注释标注、视图更新；监听建模 revision 流做增量更新 | 标注计划并行；写工程图文档串行 |
| **评估 Agent** | 几何检查、干涉/间隙、DFM/DFR、尺寸约束审查、重建回读验证 | **真并行**：基于导出快照/网格在独立进程计算，不占 SOLIDWORKS 主线程 |
| **上下文/计划 Agent（可选）** | Hover/Pin 上下文、Feature/Sketch/Dimension 候选解析、EditPlan 草案 | 与建模规划合并也可 |

最小可用编组：**Coordinator + 建模 + 评估**（3 个）。涉及工程图加绘图 Agent；复杂目标解析加上下文 Agent。

#### 7.1.2 并行架构：单写执行器 + 多路旁路计算

```text
┌──────────────────────────────────────────────────────────────┐
│                     Coordinator Agent                        │
│   任务卡 · revision 流 · 冲突裁决 · 用户确认 · 提交/回退        │
└──────┬───────────────┬───────────────┬───────────────┬───────┘
       │               │               │               │
┌──────▼──────┐ ┌──────▼──────┐ ┌──────▼──────┐ ┌──────▼──────┐
│  建模 Agent  │ │  绘图 Agent  │ │  评估 Agent  │ │ 上下文 Agent │
│ 规划并行     │ │ 标注计划并行 │ │ 评估真并行   │ │ 解析并行     │
└──────┬──────┘ └──────┬──────┘ └──────┬──────┘ └──────┬──────┘
       │ 微操作        │ 写请求        │ 只读快照      │ DTO
       └───────┬───────┴───────┬───────┴───────┬───────┘
               ▼               ▼               ▼
        ┌─────────────────────────────┐  ┌──────────────────┐
        │  写执行器（单写者，串行）      │  │ 评估沙箱（独立进程）│
        │  微操作队列 · 主线程 COM      │  │ 导出 STEP/网格     │
        │  每操作后发 revision 事件     │  │ OCCT/规则引擎计算  │
        └──────────────┬──────────────┘  └────────▲─────────┘
                       │                          │
                       ▼                          │ 快照
                ┌──────────────┐                  │
                │ SOLIDWORKS   │──────────────────┘
                │ 同一实例/窗口 │  重建后导出快照
                └──────────────┘
```

三条并行通道：

1. **写通道（串行但细粒度）：** 建模/绘图的写请求进入微操作队列，写执行器逐个落地，每个微操作后广播 `revision` 事件。队列常驻，不必等整轮。
2. **评估通道（真并行）：** 评估 Agent 不等写通道空闲。它订阅 revision 流，拿到新快照（STEP/网格/测量数据）后在自己的进程里算干涉、间隙、DFM 规则，算完回报 `EvaluationReport`。
3. **规划通道（真并行）：** 建模 Agent 的下一步 EditPlan、绘图 Agent 的标注计划、上下文 Agent 的候选解析，都在写通道忙的时候并行生成，生成完入队。

#### 7.1.3 时间线：三条线如何同步推进

```text
时间 →
建模:  [规划Ø8]──[写尺寸]──[规划圆角]──────[写圆角]──[规划下一处]
写执行:       [尺寸][导出]──[视图更新]──[圆角][导出]
绘图:  [标注计划]────[写标注]──────────[增量更新标注]
评估:      [评估rev43]────────[评估rev44]────────[评估rev45]
用户看到:  孔径变化 → 视图更新 → 评估绿灯 → 圆角落地 → ...
```

要点：

- 建模 Agent 写下一个操作时，评估 Agent 已经在算上一个 revision，绘图 Agent 已经在更新视图——三者时间上重叠。
- 写执行器每个微操作后**立即导出快照**，评估不等“整轮建模结束”。
- 评估结果分两种：`PASS` 放行下一微操作；`FAIL` 由 Coordinator 插入回退微操作，建模 Agent 继续在修正后的 revision 上规划。

#### 7.1.4 同步协作机制

| 机制 | 作用 |
|---|---|
| **Revision 流** | 每次写微操作后递增 revision 并广播；所有 Agent 订阅，按 revision 对齐上下文 |
| **微操作队列** | 写请求切到最小可验证单元，单写者串行落地，避免长时间独占主线程 |
| **快照导出** | 写后导出 STEP/网格/测量 JSON，供评估沙箱和绘图 Agent 只读使用 |
| **乐观并行** | 评估可先基于“预测几何”（EditPlan 参数推算）预评估，revision 到达后快速复核 |
| **冲突检测** | 写前校验 documentToken / selectionToken / revision；不匹配则重解析或退回任务卡 |
| **评估门禁** | 高风险微操作标记 `gate: true`，评估 FAIL 时自动回退，不进入下一微操作 |
| **绘图增量更新** | 绘图 Agent 按 revision 差量更新视图和标注，不整图重出，减少主线程占用 |

#### 7.1.5 设计任务卡模板

Coordinator 给每个 Agent 的任务卡必须声明并行边界和汇合条件：

```yaml
task_id: P4-parallel-hole-and-drawing
agents:
  modeling:
    goal: 孔径 Ø6→Ø8，保持中心；随后加 R2 圆角
    micro_ops:
      - modify_sketch_dimension
      - force_rebuild
      - add_fillet
    invariants: [preserve_hole_center]
  drawing:
    goal: 孔径尺寸跟随更新，补位置度标注
    watch_revisions: true
    update_mode: incremental
  evaluation:
    goal: 每 revision 检查孔径、中心、圆角半径、干涉、DFM
    snapshot_source: step_export
    tolerances: {diameter_mm: 0.01, center_mm: 0.05}

coordination:
  gate_on_fail: true
  max_parallel_writers: 1          # 写执行器单写者
  evaluation_deadline_ms: 5000
  user_confirm: medium_and_above
evidence_required:
  - per_revision: [params, rebuild_result, evaluation_report]
  - final: [operation_journal, drawing_diff, review_report]
```

#### 7.1.6 并行协作硬性规则

1. **写串行、算并行：** 同一 SOLIDWORKS 实例的 COM 写操作由单写执行器串行落地；评估、规划、标注准备可在独立进程/线程并行。
2. **微操作粒度：** 写请求必须切到可独立验证的最小单元；禁止一个 Agent 长时间独占主线程做整轮建模。
3. **评估独立且提前：** 评估 Agent 不写模型，基于快照计算；允许乐观预评估，但最终结论必须绑定具体 revision。
4. **Revision 对齐：** 所有 Agent 的报告、标注、EditPlan 必须标注 revision；跨 revision 的结论视为过期。
5. **冲突即停：** 写前校验失败或 revision 冲突时，写执行器拒绝该微操作，Coordinator 重新解析目标后再放行。
6. **失败按边界返工：** 评估 FAIL 只退回产生该 revision 的 Agent，其他线继续在稳定 revision 上工作。
7. **高风险串行确认：** Medium 及以上风险微操作标记 `gate`，由 Coordinator 汇总并等待用户确认，不因并行而跳过确认。
8. **COM 不共享：** Agent 之间只传 JSON DTO 和快照，不传 COM RCW；写执行器是唯一接触写 API 的组件。
9. **能力声明归 Coordinator：** 子 Agent 提交建议，Coordinator 按双版本真机证据定级。

#### 7.1.7 协作节奏

- **Cycle 0（半天）：** Coordinator 建 revision 流、微操作队列、快照导出和任务卡协议。
- **Cycle 1（1–2 天）：** 建模 + 评估两线跑通“改孔径 → 导出快照 → 评估 → 放行”；绘图 Agent 离线准备标注计划。
- **Cycle 2（2–4 天）：** 接入绘图 Agent 增量更新；三线并行跑一个含尺寸修改 + 出图 + 评估的完整件。
- **Cycle 3（3–5 天）：** 多轮并行迭代，覆盖 SW2024/SW2025；评估 FAIL 回退路径做真机验证。
- **Integration Gate：** 每轮汇总 revision 日志、EvaluationReport、绘图 diff，更新风险清单。

**默认启动配置：Coordinator + 建模 + 评估（3 个），绘图 Agent 按需加入。** 第一轮先验证“写一个微操作 → 导出快照 → 评估并行回报 → 放行下一微操作”的流水线是否稳定；稳定后再把绘图标注和更复杂的建模规划加进来，确保并行度提升不以模型状态污染为代价。

---

**一句话总结这次改动：** 7.1 从“单令牌串行轮次”改成“单写执行器 + 微操作交错 + 评估/规划真并行”，让建模、画图、评估在时间线上重叠推进，同时守住 COM 串行和 revision 一致性两条底线。

### 7.2 非标自动化中等复杂案例：气缸推料挡停机构行程与负载变更

本节把 7.1 的并行协作机制落到一个典型非标自动化机构上，用于验证三线并行是否在真实装配关系、标准件换型和工程评估压力下仍然可控。

#### 7.2.1 场景与初始模型

**项目：** 皮带线侧向推料挡停机构（Push & Stop Mechanism），用于包装线纸箱定位。

**初始模型（已存在，SW2024/SW2025 各一份）：**

| 对象 | 类型 | 关键参数 |
|---|---|---|
| `Frame_ASSY.SLDASM` | 装配体 | 4080 铝型材机架，安装面高 600 mm |
| `Rail_MGN12.SLDPRT` | 标准件（配置驱动） | MGN12 导轨，L=200 mm |
| `Carriage_MGN12H.SLDPRT` | 标准件 | MGN12H 滑块 |
| `Cylinder_CDQ2B32-50.SLDPRT` | 标准件（配置驱动） | 缸径 32，行程 50 |
| `PushPlate.SLDPRT` | 定制机加件 | 6061-T6，6 mm 厚，带 PU 垫 |
| `SensorBracket.SLDPRT` | 定制钣金件 | 2 mm 折弯，装光电传感器 |
| `Workpiece.SLDPRT` | 参考件 | 200×150×80 纸箱，3 kg |
| `PushStop_ASSY.SLDASM` | 总装配 | 上述零件 + 紧固件 |

**已有 Pin 上下文：** 用户 Hover 并 Pin 了 `PushPlate` 的工作面与 `Cylinder` 的活塞杆端面，TaskPane 显示候选 Feature：`PushPlate` 的拉伸特征、`Cylinder` 的配置参数、`Rail` 的长度配置。

#### 7.2.2 变更需求与设计意图

**用户指令：** “工件换成了 8 kg 的箱子，行程要从 50 加到 80，节拍 30 次/分钟。气缸换成缸径 40 的，导轨加长，推料板加强，传感器位置跟着调。出装配图和两张零件图，检查干涉和推力够不够。”

**Coordinator 拆解出的设计意图：**

| 意图 | 类型 | 风险 | 是否需要用户确认 |
|---|---|---|---|
| 气缸配置 32-50 → 40-80 | 标准件换型 | Medium | 是 |
| 导轨长度 200 → 280 | 参数修改 | Medium | 是 |
| 滑块安装位置后移 30 mm | 装配定位 | Low | 否（在计划内） |
| 推料板 6 → 8 mm，加两道筋 | 特征修改 | Medium | 是 |
| 传感器支架后移 30 mm、加长 | 钣金修改 | Low | 否 |
| 机架安装孔位跟随调整 | 装配孔修改 | Medium | 是 |
| 出装配图 + 推料板图 + 支架图 | 工程图 | Low | 否 |
| 干涉/行程/推力/导轨负载/DFM 评估 | 评估 | — | 结果需汇总给用户 |

**不变量：** 推料面中心高度不变；气缸轴线与导轨平行度 ≤0.1 mm；传感器检测面与推料板前端的相对位置保持不变；机架外形不超出原占位。

#### 7.2.3 三线任务卡

```yaml
task_id: NA-PushStop-travel-80
coordinator: coordinator-main

modeling:
  goal: 完成气缸换型、导轨加长、推料板加强、支架调整、孔位更新
  micro_ops:
    - swap_cylinder_config: {from: CDQ2B32-50, to: CDQ2B40-80}
    - modify_rail_length: {from: 200, to: 280}
    - move_carriage: {delta_mm: 30, direction: +Y}
    - modify_plate_thickness: {from: 6, to: 8}
    - add_ribs: {count: 2, thickness_mm: 4}
    - move_sensor_bracket: {delta_mm: 30, direction: +Y}
    - extend_bracket: {delta_mm: 40}
    - update_frame_holes: {pattern: follow_rail_mount}
  invariants:
    - preserve_push_face_center_height
    - preserve_cylinder_rail_parallelism
    - preserve_sensor_relative_position
  gate_ops: [swap_cylinder_config, modify_rail_length, modify_plate_thickness]

drawing:
  goal: 更新装配图与两张零件图
  outputs:
    - PushStop_ASSY.SLDDRW: 主视图 + 俯视图 + 行程尺寸 80 + BOM 更新
    - PushPlate.SLDDRW: 厚度 8、筋板尺寸、孔位、表面处理
    - SensorBracket.SLDDRW: 展开图、折弯半径、孔位
  watch_revisions: true
  update_mode: incremental

evaluation:
  goal: 每 revision 做工程校核
  checks:
    - interference_all_pairs
    - travel_range_check: {required_mm: 80}
    - cylinder_thrust_check: {load_kg: 8, cycle_per_min: 30}
    - rail_load_check: {moment_arm_mm: 80}
    - dfm_machined: [min_wall, hole_depth, fillet_radius]
    - dfm_sheetmetal: [bend_radius, bend_relief, hole_to_bend]
    - assembly_reachability: [wrench_clearance, sensor_access]
  snapshot_source: step_export
  tolerances: {parallelism_mm: 0.1, travel_mm: 0.5, center_height_mm: 0.2}

coordination:
  max_parallel_writers: 1
  evaluation_deadline_ms: 8000
  user_confirm: gate_ops + medium_and_above
  fail_policy: gate_fail_rollback_and_replan
```

#### 7.2.4 并行时间线（revision 流）

```text
时间 →
建模:   [规划气缸换型]─[写气缸配置]─[规划导轨]──[写导轨L280]──[规划板厚]─[写板厚8]─[加筋]─[支架]─[孔位]
写执行:          [气缸][导出]──[导轨][导出]──[板厚][导出]──[筋][导出]──[支架][导出]──[孔位][导出]
绘图:   [装配图视图计划]──[更新主视图]────[更新俯视图]────[行程标注]────[BOM更新]──[零件图更新]
评估:        [rev43:气缸推力]──[rev44:行程/干涉]──[rev45:板强度]──[rev46:导轨负载]──[rev47:DFM]──[rev48:可达性]
用户:          ↑确认气缸换型        ↑确认导轨/板厚          ↑查看评估汇总         ↑确认出图
```

**关键并行点：**

- 建模 Agent 写气缸配置（rev43）后，评估 Agent 立即用快照算推力，同时建模 Agent 已在规划导轨修改，不等评估结果。
- 绘图 Agent 在 rev44 后开始更新装配图主视图，不等全部建模完成。
- rev45 板厚修改的评估（板强度、DFM）与 rev46 导轨负载评估可**并行计算**，因为基于不同快照。
- 任一 gate 微操作评估 FAIL，Coordinator 插入回退微操作，其他线在稳定 revision 上继续。

#### 7.2.5 关键微操作与 EditPlan 示例

**示例 1：气缸换型（gate，需用户确认）**

```json
{
  "planId": "plan-cylinder-swap-01",
  "target": {"documentToken": "doc-ps-assy", "selectionToken": "sel-cyl", "entityToken": "ent-cyl-config"},
  "operation": {
    "kind": "swap_component_configuration",
    "component": "Cylinder_CDQ2B32-50-1",
    "fromConfig": "CDQ2B32-50DZ",
    "toConfig": "CDQ2B40-80DZ",
    "mountingInterface": "preserve_if_compatible"
  },
  "invariants": ["preserve_cylinder_axis", "preserve_mount_face"],
  "risk": "medium",
  "requiresConfirmation": true,
  "gate": true
}
```

**示例 2：推料板加筋（不 gate，计划内）**

```json
{
  "planId": "plan-plate-ribs-01",
  "target": {"documentToken": "doc-pushplate", "selectionToken": "sel-plate-face", "entityToken": "ent-plate-body"},
  "operation": {
    "kind": "add_rib_features",
    "feature": "PushPlate",
    "count": 2,
    "thicknessMm": 4,
    "placement": "symmetric_about_cylinder_axis",
    "filletMm": 2
  },
  "invariants": ["preserve_push_face_flatness", "preserve_mounting_holes"],
  "risk": "medium",
  "requiresConfirmation": true
}
```

**示例 3：导轨长度（gate，参数修改）**

```json
{
  "planId": "plan-rail-length-01",
  "target": {"documentToken": "doc-ps-assy", "selectionToken": "sel-rail", "entityToken": "ent-rail-config"},
  "operation": {
    "kind": "modify_component_configuration_parameter",
    "component": "Rail_MGN12-1",
    "parameter": "Length",
    "oldValueMm": 200,
    "newValueMm": 280
  },
  "invariants": ["preserve_rail_mount_face", "preserve_rail_axis"],
  "risk": "medium",
  "requiresConfirmation": true,
  "gate": true
}
```

#### 7.2.6 评估 Agent 检查清单与报告

每个 revision 输出结构化报告，Coordinator 据此放行或回退。

```json
{
  "revision": 44,
  "snapshot": "step-export-rev44.step",
  "checks": [
    {"id": "interference_all_pairs", "result": "PASS", "evidence": "0 clashes, min gap 2.3mm"},
    {"id": "travel_range_check", "result": "PASS", "measuredMm": 80.2, "requiredMm": 80},
    {"id": "cylinder_thrust_check", "result": "PASS", "requiredN": 78.5, "availableN": 402, "margin": 5.1},
    {"id": "rail_load_check", "result": "WARN", "momentNm": 18.4, "ratedNm": 20.0, "note": "余量 8%，建议复核"},
    {"id": "dfm_machined", "result": "PASS"},
    {"id": "dfm_sheetmetal", "result": "FAIL", "detail": "SensorBracket 折弯孔距边 1.8mm < 2.5mm", "fix_hint": "孔位内移 1mm 或折弯线外移"}
  ],
  "overall": "FAIL",
  "action": "return_to_drawing_and_modeling: sensor_bracket_hole_shift"
}
```

**评估要点（非标自动化特有）：**

| 检查项 | 为什么非标场景必须做 |
|---|---|
| 行程范围 | 变更行程后必须验证推料板不撞机架、不脱轨 |
| 气缸推力/节拍 | 8 kg、30 次/分钟对缸径和缓冲有要求，不能只看行程 |
| 导轨力矩负载 | 推料板加长后力臂变大，MGN12 额定力矩可能不够 |
| 钣金折弯 DFM | 传感器支架加长后孔到折弯边距离常被忽略 |
| 装配可达性 | 换大气缸后扳手空间、传感器调试空间是否还在 |
| 干涉全对 | 非标件与标准件、紧固件、工件之间的间隙 |

#### 7.2.7 绘图 Agent 出图与标注

| 图纸 | 更新内容 | 与建模的联动 |
|---|---|---|
| `PushStop_ASSY.SLDDRW` | 主视图、俯视图、行程尺寸 80、气缸型号标注、BOM 更新 | 订阅 rev44/rev48，增量更新视图 |
| `PushPlate.SLDDRW` | 厚度 8、筋板位置与厚度、孔位、表面处理 | 订阅 rev45/rev46 |
| `SensorBracket.SLDDRW` | 展开图、折弯半径 R2、孔位（按评估修正后） | 订阅 rev48 和评估 FAIL 回退后的 rev49 |

**绘图与评估的交互：** 评估在 rev48 发现支架孔距折弯边不足，Coordinator 退回绘图 Agent 和建模 Agent；绘图 Agent 先改孔位标注，建模 Agent 同步改孔位，revision 推进到 rev49 后重新评估 PASS，绘图 Agent 再定稿。

#### 7.2.8 Coordinator 冲突裁决与用户确认点

**用户确认点（gate）：**

1. 气缸 32-50 → 40-80 换型（安装接口是否兼容、是否接受成本变化）。
2. 导轨 200 → 280（机架是否允许加长、占位是否超限）。
3. 推料板 6 → 8 mm 加筋（重量增加对气缸负载的影响）。
4. 最终出图前汇总：评估报告 + BOM 变更 + 图纸清单。

**冲突示例与裁决：**

| 冲突 | 裁决 |
|---|---|
| 建模 Agent 想直接改机架孔位，评估 Agent 报“导轨力矩 WARN” | Coordinator 要求先确认导轨型号是否升级到 MGN15，再决定孔位 |
| 绘图 Agent 已按旧孔位标注，建模 Agent 随后改孔位 | revision 不匹配，绘图 Agent 标注作废，按新 revision 重标 |
| 评估 FAIL 支架 DFM，建模和绘图都想改 | 指定建模 Agent 改几何、绘图 Agent 改标注，同一 revision 汇合 |

#### 7.2.9 验收标准与失败回退

**MVP 验收（SW2024/SW2025 各跑 5 次）：**

- [ ] 三线并行完成全部微操作，用户确认点不超过 4 个。
- [ ] 最终行程回读 80±0.5 mm，推料面中心高度不变。
- [ ] 气缸推力校核余量 ≥1.5，导轨力矩评估有明确结论（PASS 或升级型号）。
- [ ] 干涉检查 0 冲突，最小间隙 ≥2 mm。
- [ ] 三张图纸标注与最终模型 revision 一致。
- [ ] 任一 gate 评估 FAIL 能回退并重新规划，不回滚已 PASS 的微操作。
- [ ] 全流程有 OperationJournal 和 EvaluationReport 证据链。

**失败回退策略：**

- 单微操作 FAIL：只回退该微操作，其他线继续。
- gate 微操作 FAIL：暂停写通道，Coordinator 重新规划该步，用户重新确认。
- revision 冲突：拒绝写入，重新解析 SelectionContext，不静默改相似对象。
- 图纸与模型不一致：以模型 revision 为准，图纸增量重出。

#### 7.2.10 非标自动化适配要点（可复用模式）

1. **标准件用配置驱动，不用重建几何。** 气缸、导轨、滑块换型走配置参数，评估 Agent 直接读配置做选型校核。
2. **定制件走特征级微操作。** 板厚、筋、孔位、折弯边按特征拆分，便于单步评估和回退。
3. **评估清单按机构类型模板化。** 推料/挡停机构固定检查行程、推力、导轨力矩、干涉、DFM、可达性；换机构类型换模板。
4. **工程图跟随 revision 增量更新。** 非标交期紧，不能等建模全部完成再出图；但每张图必须绑定最终 revision。
5. **用户确认点集中在换型和关键参数。** 不让用户在几十个微操作上逐个确认，只 gate 高风险决策。
6. **失败证据必须可追溯。** 非标现场调试依赖设计证据，OperationJournal + EvaluationReport 是交付物的一部分。

## 8. 第一版 MVP 的范围控制

### 必须做（P0）

-   [ ] SW2024 / SW2025 Add-in 加载与诊断。
-   [ ] Hover 识别 Face / Edge。
-   [ ] 点击 Pin / Unpin。
-   [ ] TaskPane 当前对象卡片。
-   [ ] SelectionContext JSON。
-   [ ] Feature / Sketch / Dimension 基础追溯。
-   [ ] Inline Prompt。
-   [ ] 生成结构化修改计划。
-   [ ] 修改一个已明确定位的草图尺寸。
-   [ ] 重建、回读、验证、失败恢复。
-   [ ] 完整操作日志。

### 可以后做（P1）

-   [ ] 多选对象。
-   [ ] 画布锚点式评论。
-   [ ] Feature 直接选择和参数编辑。
-   [ ] 圆角/倒角参数修改。
-   [ ] 装配体 Component 选择和定位。
-   [ ] 修改前后视觉对比。
-   [ ] 操作历史与多轮线程。

### 暂不做（P2 / Out of Scope）

-   任意面直接"智能重塑"。
-   无确认的复杂特征树重构。
-   任意 VBA / Python 代码自动执行。
-   全自动机械设计和工程责任判断。
-   复杂曲面逆向修改。
-   全部工程图标注类型的自然语言修改。
-   无证据的 SW2024/2025 全版本兼容承诺。

------------------------------------------------------------------------

## 9. 测试策略与版本矩阵

### 9.1 目标版本矩阵

  测试项                            SW2024   SW2025
  --------------------------------- -------- --------
  Add-in 注册、加载、卸载           必测     必测
  TaskPane 显示/关闭                必测     必测
  Part Hover / Pin                  必测     必测
  Assembly Component Hover / Pin    必测     必测
  Face / Edge / Vertex 类型识别     必测     必测
  文档切换与关闭                    必测     必测
  Force Rebuild 后 token 失效处理   必测     必测
  草图尺寸修改与回读                必测     必测
  失败恢复                          必测     必测
  中文/英文界面                     建议     建议
  高 DPI / 多显示器                 建议     建议

### 9.2 最小测试模型

1.  简单板件：平面、圆孔、沉孔、倒角。
2.  带多个切除特征的支架：测试 Feature Resolver。
3.  导入 STEP 实体：测试无法追溯时的降级。
4.  多零件装配体：测试 Component 上下文和文档引用。
5.  有草图阵列/镜像的零件：测试候选 Feature 和尺寸关联。

### 9.3 质量门槛

-   Hover 更新不阻塞 SOLIDWORKS 主交互。
-   Pin 对象不因鼠标移动而改变。
-   所有 Apply 请求都校验文档、配置、目标 token 和模型 revision。
-   所有修改都有明确的 EditPlan 和用户确认记录。
-   修改后有参数回读和模型重建证据。
-   失败不得报告成功；回滚后必须再次验证。
-   任何未通过 SW2024 / SW2025 真机测试的功能保持 `pilot` 或更低等级。

------------------------------------------------------------------------

## 10. 建议的代码与文档落点

先按现有仓库结构增量开发，具体路径以 Phase 0 审计为准。

``` text
solidworks-automation-skill/
├── scripts/
│   ├── sw_addin_host.ps1              # 已有：复用/补齐宿主安装与诊断
│   ├── sw_addin_host.py               # 已有：复用/补齐宿主控制
│   └── sw_entity_reference.py         # 已有：扩展语义实体引用
├── mcp-server/
│   ├── server.py                      # 已有：增加 context / plan / apply 工具
│   └── ...
├── references/
│   └── solidworks-addin-host.md       # 已有：补充 Point & Prompt 事件/IPC 说明
├── apps/
│   └── ...                            # 如现有 UI 容纳 TaskPane 外部辅助界面再扩展
├── docs/
│   └── point-and-prompt/
│       ├── roadmap.md                 # 本路线图
│       ├── audit.md                   # Phase 0 审计
│       ├── interaction-spec.md        # 交互与状态机
│       ├── selection-context.schema.json
│       ├── edit-plan.schema.json
│       ├── test-matrix.md
│       └── decisions.md               # 架构决策记录
└── capabilities.yaml                  # 新增原子能力与验证等级
```

如果现有 Add-in
工程在仓库其他位置，新增模块应放在现有工程中，不要为了匹配上面的示意目录而复制宿主。

### 10.1 能力清单建议新增原子能力

建议按原子能力拆分，避免一个总开关掩盖实际成熟度：

-   `solidworks_hover_selection`
-   `solidworks_selection_pin`
-   `solidworks_selection_context`
-   `solidworks_feature_resolution`
-   `solidworks_inline_prompt`
-   `solidworks_edit_plan_preview`
-   `solidworks_parametric_edit_apply`
-   `solidworks_edit_verify_rollback`

每项分别维护 backend、版本、证据、限制和 capability level。

------------------------------------------------------------------------

## 11. 首个 Demo 的产品验收脚本

**Demo 名称：Point to Hole --- Click, Prompt, Modify**

准备一个包含单个可编辑圆孔的简单支架模型，且孔径由明确的草图尺寸控制。

  步骤   用户动作                              系统反馈
  ------ ------------------------------------- ------------------------------------
  1      鼠标悬停圆孔内壁                      高亮圆柱面，显示"圆柱面 / Ø6"
  2      点击固定                              显示 Pin 状态和所属 Feature 候选
  3      输入"把这个孔改成 Ø8，中心位置不变"   AI 生成 EditPlan
  4      查看修改计划                          显示旧值、新值、目标尺寸和保持条件
  5      点击确认                              Add-in 在主线程执行
  6      重建模型                              孔径变化，模型保持有效
  7      自动回读                              Ø8；中心位置与修改前一致
  8      查看结果                              显示成功、参数差异和目标高亮
  9      Undo                                  恢复 Ø6 并再次回读验证

**MVP 成功标准：** 以上流程在 SW2024 和 SW2025 分别连续完成 10
次，不发生错目标修改、崩溃或无法恢复的模型损坏。若尚未达到该标准，功能标记为
`pilot`，不作为稳定能力宣传。

------------------------------------------------------------------------

## 12. 开发顺序：现在就做什么

按「先契约、再并联、后收口」的顺序开工，不再同时铺开所有功能：

1. **Phase 0 审计（已完成基线）。**
   审计 `dotnet/CadStudio.SolidWorks.AddinHost`、`scripts/sw_entity_reference.py`、
   `mcp-server/server.py` 与 `capabilities.yaml`，输出「复用 / 补齐 / 新增」清单；
   结论见 `docs/point-and-prompt/audit.md`。
2. **冻结契约。**
   `SelectionContext`、`EditPlan`、Add-in ↔ MCP 文件 IPC 与错误码先定稿
   （`selection-context.schema.json`、`edit-plan.schema.json`、`interaction-spec.md`），
   再分派实现；协议变更必须先改契约。
3. **先跑通单写微操作流水线（3 个角色：Coordinator + 建模 + 评估）。**
   验证「写一个微操作 → 导出/回读快照 → 评估并行回报 → 放行下一微操作」是否稳定；
   这一轮只做低风险参数修改（草图孔径 Ø6→Ø8，保持中心）。
4. **接入绘图 Agent 的增量更新（4 个角色）。**
   在 7.2 的推料挡停案例上跑「改孔径/导轨 → 更新视图与标注 → 评估 → 回退修正」，
   图纸必须绑定 revision。
5. **补 Claude Design 式交互体验。**
   Hover 高亮、Pin 上下文、Inline Prompt、计划卡片、多选、线程与视觉对比；
   交互层以 Phase 1–3 的验收标准收口。
6. **双版本真机回归与能力定级。**
   SW2024 / SW2025 分别跑通验收脚本，按证据更新 `capabilities.yaml`；
   未通过真机的功能保持 `pilot` / `not_implemented`。

> **最重要的产品判断：第一版不是「AI 能修改所有 CAD」，而是「用户点到哪里，AI 就准确
> 理解哪里；用户说改什么，AI 能提出可审查、可确认、可验证的修改」。**
> 先把这个交互闭环做到可靠，再逐步扩大可编辑对象和工程能力。

## 附录 A：实现落点对照

| 路线图条目 | 代码/文档落点 |
|---|---|
| 3.1 对象类型分层 / 3.2 Feature Resolver | `scripts/sw_feature_resolver.py` |
| 4. SelectionContext 协议 | `docs/point-and-prompt/selection-context.schema.json`、`scripts/sw_selection_context.py` |
| 5.1 结构化修改计划 | `docs/point-and-prompt/edit-plan.schema.json`、`scripts/sw_edit_plan.py` |
| 6.2 执行事务 / 6.3 验收证据 | `scripts/sw_apply_plan.py`、`journal.jsonl` |
| 2.2 通信建议 | `scripts/sw_cad_bridge.py`、`docs/point-and-prompt/interaction-spec.md` |
| 5. MCP 工具规划 | `mcp-server/server.py` 的 `solidworks_*` 工具 |
| 7.1 并行流水线 | `scripts/sw_parallel_pipeline.py`（微操作队列 + revision 流 + 快照） |
| 10.1 原子能力 | `capabilities.yaml` 的 `solidworks_point_prompt_*` 条目 |
| 9. 测试矩阵 | `tests/test_point_prompt_*.py`、`docs/point-and-prompt/test-matrix.md` |

## 附录 B：变更记录

- **v1.2（2026-11-12）**：合并运行时并行流水线（新 7.1）与非标案例（7.2）；
  删除旧的 Cursor 开发编组 7.1；第 12 节改为并行流水线推进顺序；新增能力等级约束说明。
- **v1.1**：加入 Cursor Swarm 多 Agent 并行开发规划（本轮已废弃该开发编组定义）。
