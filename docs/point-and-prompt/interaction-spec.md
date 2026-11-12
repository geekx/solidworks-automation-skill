# Point & Prompt 交互与 IPC 契约

> 状态：Phase 1–4 实现依据。任何实现改动先改本文件，再改代码。

## 1. 组件与职责边界

| 组件 | 负责 | 不负责 |
|---|---|---|
| C# Add-in（`dotnet/CadStudio.SolidWorks.AddinHost`） | 图形区 Hover 预选、Selection/Pin、TaskPane 卡片、原生高亮、把上下文写入桥接目录 | 大模型推理、执行白名单写入、修改模型 |
| Python MCP Server（`mcp-server/server.py`） | 读取 Pin 上下文、解析 Feature 候选、生成/校验 EditPlan、执行 Apply/Rebuild/Verify、写 Journal | 高频 Hover、UI 事件循环、长期驻留 |
| Agent / Skill | 理解自然语言、选择工具、生成 EditPlan、解释风险 | 持有 COM 对象、模拟鼠标、直接写模型 |
| 桥接目录（本文件的 IPC） | 两侧交换 JSON 状态与请求 | 传输 COM RCW、二进制几何、凭据 |

**硬约束：** 只有写执行器（MCP 的 Apply 链路）调用写 API；Add-in 不写模型；Agent 不直接调用 COM。

## 2. 桥接目录与文件

默认目录：

```text
%LOCALAPPDATA%\CAD Studio\SolidWorksAddin\point-and-prompt\
```

可用环境变量 `CADSTUDIO_POINT_PROMPT_DIR` 覆盖（测试与多实例隔离用）。两侧都必须用
`scripts/sw_cad_bridge.py::bridge_dir()` 的同一规则解析，不允许各自硬编码。

| 文件 | 写入方 | 读取方 | 内容 |
|---|---|---|---|
| `context.json` | Add-in（选择/Hover 变化）、MCP（Python COM Pin 回退） | 双方 | 一份完整 `SelectionContext`（`selection-context.schema.json`） |
| `state.json` | Add-in | 双方 | 桥接层状态：`bridgeRevision`、`pinned`、`addin.status`、`hover.status`、`lastError` |
| `history.jsonl` | 双方追加 | 双方 | 交互历史：hover/pin/unpin/plan/apply/rollback |
| `prompt-requests.jsonl` | Add-in 追加 | Agent 侧轮询 | 用户在 TaskPane 发出的 Prompt 请求 |
| `plan-previews/<planId>.json` | MCP 写入 | Add-in 读取并渲染计划卡片 | `{plan, preview, status}` |
| `highlights.json` | MCP 写入 | Add-in 读取 | 高亮请求（entityToken 列表 + 过期时间） |
| `journal.jsonl` | MCP 追加 | 双方 | `OperationJournal` 事务记录 |

**写入协议：** 先写同目录临时文件，再 `os.replace` / `File.Replace` 原子替换；读取方遇到
`FileNotFound`、空文件或 JSON 截断时最多重试 3 次（间隔 50 ms），仍失败则报告
`E_BRIDGE_UNREADABLE`，不得静默使用旧上下文。

**大小上限：** `context.json` ≤ 256 KB；单条 jsonl ≤ 64 KB；`plan-previews` 单文件 ≤ 64 KB。

## 3. 交互状态机

```text
IDLE
  └─ 鼠标悬停 → HOVER_PREVIEW            (仅本地，不请求大模型)
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

规则：

1. Hover 只更新 `context.json` 的 `selection.mode = "hover"`，节流 ≥150 ms，仅在实体
   token 变化时写盘；Hover 不得产生日志噪音，也不得改变已 Pin 的上下文。
2. Pin 后 `mode = "pinned"`，鼠标移动不得改写 target；只有 Unpin、切换文档、目标失效
   或用户显式改选才清除。
3. `Esc` 先关闭 Prompt/预览，再解除 Pin；不直接取消整个模型操作。
4. 文档切换（`ActiveDocChangeNotify`）与文档关闭必须清空 Pin，并在 `history.jsonl` 记
   `unpin(reason=document_changed|document_closed)`。
5. 模型 revision 变化后，所有旧 token 标记 `stale`，执行前必须重新解析；禁止静默改到
   相似对象。
6. 多选时 `selection.objects` 保序，`activeEntityToken` 指向 Prompt 锚定的对象。

## 4. 消息与错误码

错误码统一放在结果 JSON 的 `error_code` 字段，且必须附带 `retryable`：

| 错误码 | 含义 | 典型处理 |
|---|---|---|
| `E_BRIDGE_UNREADABLE` | 桥接文件缺失/损坏 | 重试或提示重新选择 |
| `E_NO_PINNED_CONTEXT` | 没有 Pin 上下文 | 提示用户先点选对象 |
| `E_DOCUMENT_MISMATCH` | 文档 token/路径不符 | 拒绝执行，重新采集上下文 |
| `E_STALE_CONTEXT` | revision 或 token 已过期 | 重新解析目标后再生成计划 |
| `E_PLAN_INVALID` | 计划不符合 Schema/白名单 | 返回校验错误，不执行 |
| `E_CAPABILITY_BLOCKED` | capability 等级或版本不允许 | 降级为预览或人工执行 |
| `E_CONFIRMATION_REQUIRED` | 需要用户确认但未确认 | 返回计划卡片等待确认 |
| `E_READBACK_MISMATCH` | 回读值不等于计划值 | 回滚并报告证据 |
| `E_COM_UNAVAILABLE` | 无 SolidWorks/Python COM | 提示先启动 SolidWorks |
| `E_ROLLBACK_FAILED` | 回滚后仍不一致 | 停止并升级为人工处理 |
| `E_UNSUPPORTED_OPERATION` | 操作类型未在白名单 | 拒绝，提示改用特征级编辑 |

## 5. 时序（一次 Apply）

```text
Add-in: 选面 → context.json(mode=pinned, revision=42)
User:   TaskPane 输入 "把这个孔改成 Ø8，中心位置不变" → prompt-requests.jsonl
Agent:  MCP get_pinned_context → resolve_feature_candidates → preview_edit_plan
MCP:    plan-previews/<planId>.json（计划卡片，旧值/新值/不变量/风险）
User:   确认 → MCP apply_edit_plan(planId, confirmed=true)
MCP:    校验 token/revision/旧值 → journal(start) → 写参数 → ForceRebuild3
        → 回读 → 几何检查 → journal(commit) → revision+1 → context.json 更新
Add-in: 轮询 state.json/plan-previews → 显示成功与差异，高亮目标
```

## 6. 线程与生命周期规则

1. COM 只允许在 SolidWorks 主线程（Add-in 内）或 MCP 的全局锁内（进程外）使用；
   Python 侧所有工具调用沿用 `_sw_lock` 串行执行。
2. Add-in 事件回调中禁止长时间计算：Hover 只做拾取与 DTO 组装，写盘异步化或延后到
   定时器；单次回调预算 ≤ 30 ms。
3. Add-in 退出/断开时删除 `state.json` 的 pinned 段并写 `unpin` 历史，不删除
   `context.json`（MCP 可据此判断上下文过期）。
4. MCP 不得缓存 COM 对象跨调用；每次调用重新连接、重新校验。
5. 桥接目录只允许本机当前用户访问；不写任何凭据或模型几何原文件。
