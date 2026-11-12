# 架构决策记录（ADR）

## ADR-001 桥接用文件 IPC，而不是 Named Pipe

**状态：** 采纳（2026-11-12）

**背景：** Add-in（进程内）与 MCP（stdio 子进程）需要交换上下文、Prompt 与计划，
但 Hover 频率高、两侧生命周期独立。

**决策：** 使用 `%LOCALAPPDATA%\CAD Studio\SolidWorksAddin\point-and-prompt\` 下的
JSON 文件：`context.json`、`state.json`、`*.jsonl`。写入用临时文件 + 原子替换，
读取对半写文件重试。

**理由：**

1. 不引入新依赖与端口占用，崩溃后仍可人工检查；
2. 双方可独立启停，MCP 未运行时 Add-in 不报错；
3. 调试时可 `type context.json` 直接看到当前目标。

**代价：** 轮询有 100–500 ms 延迟；不适合高频几何流。故 Hover 只在实体 token 变化时写盘。

## ADR-002 实体身份用语义 token，不用 Face1/Edge1

**状态：** 采纳

**决策：** `entityToken = sha1(entityType | featureName | geometrySignature)`，
`documentToken = sha1(normalizedPath | documentType)`，`selectionToken = sha1(doc | tokens | revision)`。

**理由：** 重建后 `Face3` 可能变成 `Face7`；以几何签名 + 所属特征为锚点才有可解释性。
无法追溯的导入体会返回 `unresolved`，由用户确认或改用显式几何操作。

**限制：** C# 与 Python 的几何摘要算法不完全一致（C# 无 JSON 规范化），因此两侧
各自生成 token，MCP 消费而不重算；混合来源时以最后一次写入的上下文为准。

## ADR-003 只执行白名单参数，不执行生成的代码

**状态：** 采纳

**决策：** `EditPlan.operation.kind` 仅允许 `modify_sketch_dimension`、
`modify_feature_depth`、`modify_fillet_radius`、`modify_chamfer_distance`；
其余类型直接 `E_UNSUPPORTED_OPERATION`。

**理由：** 任意 Python/VBA 无法审计、无法回滚；白名单操作能保证旧值可读、新值可回读、
失败可恢复。

## ADR-004 Python COM 回退，Add-in 增强

**状态：** 采纳

**决策：** Add-in 不可用时，MCP 仍可用 `ISelectionMgr` 采集当前选择并 Pin
（`solidworks_pin_selection`）；Hover 与 TaskPane 卡片只在 Add-in 内提供。

**理由：** 让 Phase 2–4 的执行链路不依赖 Add-in 注册，先把「点选 → 改参数 → 验证」
跑通，再用 Add-in 提升交互体验；也符合路线图「先审计、再补 UI」的顺序。

## ADR-005 真机证据决定能力等级

**状态：** 采纳

**决策：** 新增 8 条 `solidworks_point_prompt_*` 原子能力，默认：

- `hover_selection`、`inline_prompt` → `not_implemented`（事件与 UI 未在 SW2024/2025 验证）；
- `selection_pin`、`selection_context`、`feature_resolution`、
  `edit_plan_preview`、`parametric_edit_apply`、`edit_verify_rollback` → `pilot`。

**理由：** 单元测试证明协议与事务逻辑，不证明 SolidWorks 事件行为；路线图要求
「未通过双版本真机测试的功能保持 pilot 或更低」。

## ADR-006 仓库脱离 fork 网络，保留上游只读同步

**状态：** 采纳（2026-11-12）

**背景：** 原仓库是 `wzyn20051216/solidworks-automation-skill` 的 fork；需要继续
吸收上游更新，但不再向上游合并。

**决策：** 把 fork 改名为 `solidworks-automation-skill-legacy` 作为备份，在原 URL
新建非 fork 仓库；本地 `origin` 指向新仓库，`upstream` 指向原上游，只做 `fetch` +
`merge`。

**理由：** GitHub 不支持「fork 转正」；新建独立仓库是唯一可靠方式。保留 upstream
remote 让同步与「不再反哺」解耦。详见 `upstream-sync.md`。

## ADR-007 7.1 运行时并行流水线的实现边界

**状态：** 采纳

**决策：** `sw_parallel_pipeline.py` 只实现调度骨架：revision 流、单写微操作队列、
评估报告聚合、gate 回退；真实评估沙箱（导出 STEP/网格后的干涉、DFM、强度计算）
通过 `evaluators` 注入，不在本轮实现。

**理由：** COM 写必须串行是硬约束；先把「写 → 评估 → 放行/回退」的契约与日志跑通，
再把计算型评估接到独立进程，避免为并行而并行。
