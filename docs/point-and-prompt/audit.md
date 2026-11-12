# Phase 0 审计：现有能力基线与 Point & Prompt 差距

> 审计基线：`b5344ad`（2026-09-08），审计日期 2026-11-12。
> 目的：在新增 Hover/Pin 交互层之前，确认哪些现有能力可以直接复用，避免重复造宿主。

## 1. 审计范围与方法

逐项阅读并交叉验证:

- `dotnet/CadStudio.SolidWorks.AddinHost/`（`SwAddin.cs`、`TaskPaneControl.cs`、
  `PropertyPageHandler.cs`、`HostDiagnostics.cs`、`csproj`、`sw_addin_host.ps1/py`）；
- `references/solidworks-addin-host.md`、`references/mcp-server.md`；
- `scripts/sw_entity_reference.py`、`scripts/sw_connect.py`、`scripts/sw_part.py`、
  `scripts/sw_review.py`、`scripts/capabilities.py`；
- `mcp-server/server.py` 的工具注册、输入模型与 `_run_locked` 串行化；
- `capabilities.yaml` 的 32 条能力、8 条后端与 11 条路由；
- `tests/` 下 68 个测试文件（含 Add-in 宿主、实体引用、草图选择等）。

## 2. 现有基线（实测计数）

| 项 | 现状 |
|---|---|
| Python 脚本 | 93 个（`scripts/*.py`） |
| MCP 工具 | 81 个（含 11 个 Point & Prompt 工具） |
| 能力条目 | 50 条：`verified` 8 / `pilot` 39 / `not_implemented` 3 |
| 后端目录 | 8 个（COM pywin32/comtypes、C# PIA、C# Add-in、原生 C++、SWBasic、OCCT、AutoCAD .NET、外部求解器） |
| 操作路由 | 13 条，全部经 `backend_router.py` 解析 |
| 测试文件 | 145 个（`tests/test_*.py` + 版本矩阵回归脚本） |

## 3. Add-in 宿主能力

已有（SW2026 真机证据见 `references/solidworks-addin-host.md`）：

- `ISwAddin.ConnectToSW/DisconnectFromSW`、`SetAddinCallbackInfo2`；
- 应用级事件：`ActiveModelDocChangeNotify`、`FileOpenPostNotify`、`FileNewNotify2`、
  `FileCloseNotify`；
- 五命令 `CommandGroup`（菜单 + 工具栏 + 高 DPI 图标；含 Point & Prompt 的 Pin/Unpin）；
- WinForms TaskPane（64 位窗口句柄）；
- `IPropertyManagerPage2Handler9` 全 37 回调；
- `host-status.json` 诊断证据与 CurrentUser / Machine 两种注册路径。

**缺口：**

| 缺口 | 影响 |
|---|---|
| 没有文档级事件订阅（`IModelDoc2` 的 `NewSelectionNotify`/`ClearSelectionNotify`） | 无法感知选择变化，Pin 无从建立 |
| 没有 Hover 预选识别 | 路线图 Phase 1 的「指哪儿」不可用 |
| TaskPane 只有静态状态文本 | 无法显示目标卡片、计划卡片、Prompt 输入 |
| 没有与 MCP 的 IPC | 上下文/计划无法在两侧交换 |
| SW2024 / SW2025 无加载与事件证据 | 目标版本能力只能标 `pilot`/`not_implemented` |

> 本轮已补齐：文档级选择事件、Hover 预选、TaskPane 卡片、文件 IPC 与五命令 CommandGroup；
> 真机证据仍待 SW2024/SW2025 补测。

## 4. 语义实体引用能力

`scripts/sw_entity_reference.py`（75 行）已提供：

- `SemanticEntityReference(entity_type, feature_name, geometry_signature)`；
- `geometry_signature(values)`：对几何摘要做稳定哈希（保留 6 位小数），不落屏幕坐标；
- `resolve_semantic_reference(reference, candidates)`：按类型 +4 / 特征名 +3 / 几何签名 +5
  打分，返回 `resolved` / `ambiguous` / `not_found`。

**可复用：** token 的派生思路与打分解析。
**缺口：** 没有文档/选择/revision 级 token，没有 Feature 候选追溯，没有失效判定，
没有与 COM 实体（`IFace2`/`IEdge2`/`IFeature`）的适配层。

## 5. MCP Server 现状

- FastMCP + Pydantic 输入模型（`BaseInput`，`extra="forbid"`，`str_strip_whitespace`）；
- 全局 `_sw_lock`（`RLock`）串行所有工具调用，符合同一实例 COM 串行约束；
- `_run_locked(operation, response_format, load_automation)` 统一错误格式
  `{status, error_type, message, suggestion}`；
- 懒加载 COM 模块，`solidworks_addin_host_status` 可在无 pywin32 时运行。

**缺口：** 没有 context / plan / apply / verify 类工具；没有按 token 取实体详情的入口；
没有桥接目录的读写封装。

## 6. 能力清单（capabilities.yaml）现状

- 等级语义：`verified` 需双版本真机证据；`pilot` 可运行但未完成版本矩阵；
  `reference_only` 仅作参考；`not_implemented` 尚未落地。
- 与 Point & Prompt 相关的只有 `solidworks_addin_host`（`verified`，仅 SW2026）。
- `solidworks_addin_ui_events` 路由已存在，候选为 C# Add-in（priority 10）与原生 C++（20）。

**结论：** 新能力必须按原子能力拆分，不能复用 `solidworks_addin_host` 的 `verified`
去暗示交互层已验证。

## 7. 复用 / 补齐 / 新增清单

| 对象 | 决策 | 落点 |
|---|---|---|
| Add-in 宿主、注册、诊断 | 复用 | 保持 `SwAddin.cs` 结构，新增事件与卡片 |
| TaskPane WinForms | 补齐 | 重写为「目标卡片 + Prompt + 计划卡片」 |
| 应用/文档事件 | 补齐 | 新增 `SelectionManager`（文档级事件） |
| Hover 预选 | 新增 | `HoverMonitor`（能力缺失时降级为 selection-only） |
| 语义实体引用 | 扩展 | `sw_selection_context.py` + `sw_feature_resolver.py` |
| MCP 串行与错误格式 | 复用 | 新工具全部走 `_run_locked` |
| 白名单修改执行 | 新增 | `sw_edit_plan.py` + `sw_apply_plan.py` |
| 事务日志 | 新增 | `journal.jsonl` + `OperationJournal` |
| 能力声明 | 新增 | 8 条 `solidworks_point_prompt_*` 原子能力 |

## 8. 版本基线（待真机确认）

| 测试项 | SW2024 | SW2025 | SW2026 |
|---|---|---|---|
| Add-in 注册/加载/卸载 | 未验证 | 未验证 | 已验证（Revision 34.1.1） |
| TaskPane 显示/关闭 | 未验证 | 未验证 | 已验证 |
| 文档级选择事件 | 未验证 | 未验证 | 未验证 |
| Hover 预选 | 未验证 | 未验证 | 未验证 |
| 草图尺寸修改与回读 | 未验证 | 未验证 | 部分（`part_and_features` verified） |

> 本审计无法替代真机测试：脚本层的单元测试只证明协议与解析逻辑，不证明 Add-in 在
> SW2024/2025 的事件行为。所有新增能力在真机证据出现前保持 `pilot` 或 `not_implemented`。

## 9. 基线测试执行说明

- 仓库测试使用 pytest（`pyproject.toml` 的 `testpaths=["tests"]`）。
- 本轮在隔离虚拟环境（`pydantic` / `mcp` / `jsonschema` / `comtypes`）中执行了新增的
  Point & Prompt 测试：**70 项全部通过**；`mcp-server/server.py` 可导入并注册
  **81 个工具**（上游 69 个 + 本仓库 11 个 Point & Prompt + 1 个分支独有工具）。
- 全仓回归：**1793 passed / 18 skipped / 4 failed**；4 个失败在干净 `HEAD` 上同样复现
  （Windows 中文编码与路径相关的既有测试），与本轮改动无关。
- 2026-11-12 合并了两条历史线：`claude/repo-connect-skill-integration-pc591r`（79 个提交）
  与上游 `wzyn20051216` 最新 80 个提交；冲突处理见 `upstream-sync.md`。
- C# Add-in 本轮**未编译**：当前机器未安装 SolidWorks API（`api\redist`）与对应 PIA，
  无法通过 `sw_addin_host.ps1 -Action Build`。新增 C# 文件的构建与真机行为需在
  装有 SW2024/SW2025 的机器上验证，命令见 `references/solidworks-addin-host.md`。

## 10. 退出条件核对

| 退出条件 | 结论 |
|---|---|
| 明确 Add-in 宿主能否直接扩展 | 能：事件、TaskPane、命令组均为现成扩展点 |
| 两个目标版本的加载问题有可复现记录 | 尚未：SW2024/SW2025 需真机补测（明日） |
| 避免重复开发 | 已确认：语义引用、MCP 串行、白名单执行框架直接复用 |
