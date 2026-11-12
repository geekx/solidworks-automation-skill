# 测试矩阵与证据清单

> 目标版本：SOLIDWORKS 2024 / 2025。所有「自动化」项都指仓库内脚本；「真机」项必须在
> 装有 SolidWorks 的机器上执行并附证据文件。

## 1. 分层

| 层 | 覆盖对象 | 运行方式 | 当前状态 |
|---|---|---|---|
| L1 纯逻辑 | token、Schema、计划校验、事务、流水线调度 | `pytest tests/test_point_prompt_*.py` | 70 项通过（见第 4 节） |
| L2 协议 | SelectionContext / EditPlan 与 JSON Schema 一致 | 同上（jsonschema 校验） | 通过 |
| L3 MCP | 工具注册、参数模型、错误码 | `import mcp-server/server.py` + 工具清单 | 59 个工具注册成功 |
| L4 Add-in | 加载、选择事件、Hover、TaskPane | SW 真机 + `scripts/sw_addin_host.ps1 -Action Probe` | 未验证（本机无 SolidWorks） |
| L5 端到端 | 指向孔 → Prompt → 计划 → 应用 → 回读 | SW 真机 + MCP 客户端 | 未验证 |

## 2. 版本矩阵

| 测试项 | SW2024 | SW2025 | 自动化 | 证据 |
|---|---|---|---|---|
| Add-in 注册 / 加载 / 卸载 | 必测 | 必测 | 部分 | `host-status.json` |
| TaskPane 显示 / 关闭 | 必测 | 必测 | 否 | 截图 + 诊断事件 |
| 文档级 `NewSelectionNotify` | 必测 | 必测 | 否 | `history.jsonl` 的 pin 记录 |
| `ClearSelectionNotify` 清 Pin | 必测 | 必测 | 否 | `history.jsonl` 的 unpin(reason=clear_selection) |
| Hover（`DynamicHighlightNotify`） | 必测 | 必测 | 否 | `state.json::hover.status` + `context.json(mode=hover)` |
| Hover 实体与显示一致 | 必测 | 必测 | 否 | 录屏 |
| Face / Edge / Vertex 类型识别 | 必测 | 必测 | 否 | `context.json` 的 `entityType` |
| Feature 候选追溯 | 必测 | 必测 | 否 | `solidworks_resolve_feature_candidates` 返回 |
| 草图尺寸修改与回读 | 必测 | 必测 | 是（COM） | `journal.jsonl` commit + `verify` 记录 |
| 回读不一致 → 回滚 | 必测 | 必测 | 是 | `journal.jsonl` rollback |
| 失败恢复后模型有效 | 必测 | 必测 | 部分 | `sw_review` 报告 |
| 文档切换 / 关闭 | 必测 | 必测 | 否 | `history.jsonl` |
| Force Rebuild 后 token 失效 | 必测 | 必测 | 是 | `check_staleness` → stale |
| 中文 / 英文界面 | 建议 | 建议 | 否 | 截图 |
| 高 DPI / 多显示器 | 建议 | 建议 | 否 | 截图 |

## 3. 最小测试模型

1. 简单板件：平面、Ø6 通孔、沉孔、倒角（Demo：把孔改成 Ø8）。
2. 带多个切除特征的支架：验证 Feature Resolver 候选排序。
3. 导入 STEP 实体：验证 `unresolved` 降级（不得伪造特征归属）。
4. 多零件装配体：验证 Component 上下文与文档切换。
5. 草图阵列/镜像零件：验证尺寸关联与候选歧义。
6. 7.2 非标案例：推料挡停机构（气缸换型 + 导轨加长 + 出图 + 评估）。

## 4. L1 自动化基线（2026-11-12）

```text
pytest tests/test_point_prompt_bridge.py
       tests/test_point_prompt_selection_context.py
       tests/test_point_prompt_feature_resolver.py
       tests/test_point_prompt_edit_plan.py
       tests/test_point_prompt_apply_plan.py
       tests/test_point_prompt_parallel_pipeline.py
       tests/test_point_prompt_schemas.py
→ 70 passed
```

覆盖点：

- 桥接原子写、半写 JSON、jsonl 残行、revision 计数、Pin/历史/计划卡片/高亮；
- 文档/实体/选择 token 的稳定性与失效判定；
- Face → Feature、Edge 相邻面、Dimension 全名、导入实体降级、尺寸枚举；
- Schema 校验、白名单拒绝、token 不匹配、能力门禁、确认状态、值域告警；
- 事务提交、回读不一致回滚、重建失败回滚、撤销、revision 冲突拒绝撤销；
- revision 流、单写队列顺序、gate 失败回退、WARN 不中断、任务卡校验。

## 5. 验收门槛（MVP）

- 指向孔 → 改 Ø8 → 中心不变：SW2024 / SW2025 各连续 10 次无错目标、无崩溃；
- 每次 Apply 都有 `journal.jsonl` 的 start / commit（或 rollback）与回读证据；
- 回读容差 ≤ 0.01 mm；失败必须回滚并再次回读；
- 未通过双版本真机的功能在 `capabilities.yaml` 保持 `pilot` 或 `not_implemented`。

## 6. 证据落点

```text
%LOCALAPPDATA%\CAD Studio\SolidWorksAddin\
├── host-status.json                     # Add-in 诊断
└── point-and-prompt\
    ├── context.json                     # 最近一次 SelectionContext
    ├── state.json                       # Bridge 状态 / Pin / revision
    ├── history.jsonl                     # 交互历史
    ├── journal.jsonl                     # 操作事务
    ├── prompt-requests.jsonl             # TaskPane Prompt
    ├── highlights.json                   # 高亮请求
    └── plan-previews\*.json              # 计划卡片
```

## 7. 尚未自动化的原因

- Hover/选择事件依赖 SolidWorks 进程内 STA 与真实鼠标交互，无法在无 GUI 的 CI 中复现；
- COM 事务测试需要许可证与可写文档；
- TaskPane 布局、高 DPI、中文界面属于视觉验收，必须录屏或截图。
