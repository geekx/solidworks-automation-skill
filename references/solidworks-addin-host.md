# SolidWorks C# Add-in 宿主

## 定位

`dotnet/CadStudio.SolidWorks.AddinHost` 是面向长期事件回调和原生 UI 生命周期的
`net48/x64` 进程内宿主。它不替代 Python 的短生命周期建模脚本，也不冒充仅非托管
C++ 可用的 `I*` 指针接口；路由由 `capabilities.yaml::solidworks_addin_ui_events` 决定。

当前宿主包含：

- `ISwAddin.ConnectToSW/DisconnectFromSW` 与 `SetAddinCallbackInfo2`；
- `SldWorks` coclass 的活动文档、新建、打开和特殊关闭事件；
- 三命令 `CommandGroup`；
- WinForms `TaskPane`，使用 64 位窗口句柄；
- 完整实现 SW2026 `IPropertyManagerPage2Handler9` 的 37 个回调；
- `%LOCALAPPDATA%\CAD Studio\SolidWorksAddin\host-status.json` 诊断证据；
- 强名称程序集身份、CurrentUser COM 冒烟和 Machine 正式注册两条部署路径。

## 构建

本机 PIA 目录必须显式给出，避免误引用另一个 SolidWorks 主版本：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\sw_addin_host.ps1 `
  -Action Build `
  -SolidWorksApiDir 'C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\api\redist'
```

输出为程序集；Machine 注册时还会生成并注册同目录 TLB：

```text
dotnet\CadStudio.SolidWorks.AddinHost\bin\Release\net48\CadStudio.SolidWorks.AddinHost.dll
dotnet\CadStudio.SolidWorks.AddinHost\bin\Release\net48\CadStudio.SolidWorks.AddinHost.tlb
```

## 注册与验证

CurrentUser 模式只验证 CLR/COM 激活，不会写入虚假的 SolidWorks Add-ins 发现项：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\sw_addin_host.ps1 -Action Register -RegistrationScope CurrentUser -SolidWorksApiDir '<api\redist>'
powershell -ExecutionPolicy Bypass -File scripts\sw_addin_host.ps1 -Action ComSmoke -RegistrationScope CurrentUser
```

SolidWorks 进程内加载必须执行 Machine 注册。普通 PowerShell 会自动弹出一次 UAC，
提升后的子进程只执行固定 GUID 的构建/注册动作：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\sw_addin_host.ps1 -Action Register -RegistrationScope Machine -SolidWorksApiDir '<api\redist>'
powershell -ExecutionPolicy Bypass -File scripts\sw_addin_host.ps1 -Action Probe -RegistrationScope Machine
```

Machine 模式使用 64 位 `.NET Framework RegAsm /codebase /tlb`，同时写入 COM 类、类型库、
`HKLM\SOFTWARE\SOLIDWORKS\Addins\{GUID}` 和当前用户启动项。卸载时使用同一程序集路径：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\sw_addin_host.ps1 -Action Unregister -RegistrationScope Machine
```

## 验收门禁

`ISldWorks.LoadAddIn()` 返回 `swSuccess=0` 仍不单独算通过。真实 Probe 还必须读取诊断文件并同时满足：

1. `status == connected`；
2. `callbackRegistered == true`；
3. CommandGroup、TaskPane、PropertyManagerPage 三项均为 `true`；
4. 新建临时零件后 `file_new` 与活动文档事件计数增长，`CloseDoc` 后 `GetDocumentCount()==0`；
5. `errors` 为空。

可调用 `python scripts/sw_addin_host.py` 或 MCP `solidworks_addin_host_status` 查看程序集、TLB、注册层级、
诊断内容和精确阻塞码。当前用户 COM 注册不能替代上述 Machine 门禁。

## SW2026 真机证据

本机 SW2026 SP1.1（Revision `34.1.1`）已经通过完整回归：

- Add-in 由 `SLDWORKS.exe` 进程实例化，`LoadAddIn=0`；
- `SetAddinCallbackInfo2`、应用事件、三命令 CommandGroup、TaskPane、PropertyManagerPage 全部就绪；
- `IPropertyManagerPage2Handler9` 的 37 个回调由公开、COM 可见并提供 `AutoDispatch` 的处理器承载；
- 新建事件计数为 1，临时零件关闭后打开文档数为 0，诊断错误列表为空；
- 回归结果写入 `output/addin-host-real/addin_host_regression_result.json`。

该回归还验证了两个门禁：缺少 TLB 时，SW2026 可能装入 DLL 并返回 `LoadAddIn=0`，但不会实例化
`ISwAddin`；PMP Handler 只有 `IUnknown` 而不提供 `IDispatch` 时，`ICreatePropertyManagerPage`
会抛 `InvalidCastException`。

## 已知限制

- 当前仓库程序集做了强名称签名，用于稳定程序集身份；它不是可信发布者的 Authenticode
  代码签名。企业分发仍应由发布流水线签署 DLL/安装包。
- SolidWorks 会锁定已加载 DLL；重建前应先 `UnloadAddIn` 或正常退出 SolidWorks，不要强行覆盖。
- 应用级 `FileCloseNotify` 只在工程图 `CloseAndReopen` 后触发；普通零件/装配体关闭应检查文档数，或订阅对应文档事件。
- CommandManager 布局会进入用户配置。GUID、命令组 ID 和命令用户 ID 发布后应保持稳定。
- Add-in 适合 UI、事件和长期驻留回调；常规建模仍优先现有 Python Automation 封装。

## Point & Prompt 交互层（v1.0 代码，待真机）

### 事件

| 事件 | 来源 | 用途 |
|---|---|---|
| `ActiveModelDocChangeNotify` | `SldWorks`（已有） | 切换文档时重挂文档事件 |
| `NewSelectionNotify` | `PartDoc` / `AssemblyDoc` / `DrawingDoc` | 选择集变化 → 重建 SelectionContext |
| `ClearSelectionNotify` | 同上 | 清空选择 → 清 Pin、写 `unpin(clear_selection)` |
| `DynamicHighlightNotify` | 同上 | Hover 预选 → 写 `mode=hover` 上下文（已 Pin 时不覆盖） |
| `DestroyNotify` | 同上 | 文档关闭 → 清 Pin、写 `unpin(document_closed)` |

Hover 实体读取顺序：`ISelectionMgr.GetPreSelectedObject()` →
`GetSelectedObject6(-1, -1)`；两者都失败时 `state.json::hover.status` 记为
`unavailable`，TaskPane 显示「降级为点选」。

### 桥接文件

```text
%LOCALAPPDATA%\CAD Studio\SolidWorksAddin\point-and-prompt\
├── context.json          # SelectionContext（Add-in 或 MCP 写）
├── state.json            # bridgeRevision / pinned / addin / hover / documentRevisions
├── history.jsonl         # pin / unpin / prompt / highlight
├── prompt-requests.jsonl # TaskPane 提交的 Prompt
├── highlights.json       # MCP 请求高亮
└── plan-previews\*.json  # MCP 写入的计划卡片
```

`CADSTUDIO_POINT_PROMPT_DIR` 可覆盖目录（测试/多实例）。写入均为临时文件 + 原子替换。

### TaskPane 与命令

- TaskPane 卡片：文档 + 配置 + revision、目标实体（类型/几何/直径）、候选特征、
  hover 可用性、Prompt 输入、计划卡片（旧值 → 新值、风险、状态）。
- CommandGroup 新增两条命令：「Pin 当前选择」「解除 Pin」。
- Add-in 不调用大模型；Prompt 只写文件，计划卡片由 MCP 生成后轮询显示。

### 构建与验证

```powershell
powershell -ExecutionPolicy Bypass -File scripts\sw_addin_host.ps1 `
  -Action Build `
  -SolidWorksApiDir 'C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\api\redist'
```

本轮**未在本机编译**：当前环境没有 SolidWorks `api\redist` 与 PIA，`-Action Build`
会在 `ValidateSolidWorksApiDir` 目标处明确报错。需在 SW2024/SW2025 机器上构建后，
再跑 `-Action Probe` 与 `docs/point-and-prompt/test-matrix.md` 的 L4/L5 项。

### 已知限制

- Hover 依赖 `DynamicHighlightNotify`；若目标版本不触发或不提供预选对象，
  交互降级为「点选 + Pin」，不影响 Apply/Verify。
- 高亮复用原生选择作为可见反馈（COM 无临时高亮接口）；命中不了 token 时不猜测目标。
- 未通过 SW2024/SW2025 真机前，`capabilities.yaml` 中 Hover/Inline Prompt 为
  `not_implemented`，其余 Point & Prompt 能力为 `pilot`。

## 官方依据

- [ISwAddin Members（SOLIDWORKS API Help 2026）](https://help.solidworks.com/2026/english/api/swpublishedapi/SolidWorks.Interop.swpublished~SolidWorks.Interop.swpublished.ISwAddin_members.html)
- [Using the C# Add-in Wizard（SOLIDWORKS API Help 2026）](https://help.solidworks.com/2026/English/api/sldworksapiprogguide/Overview/Using_SolidWorks_C__Add-In_Wizard_to_Create_C__Add-In.htm)
- [IPropertyManagerPage2Handler9 Members（SOLIDWORKS API Help 2026）](https://help.solidworks.com/2026/English/api/swpublishedapi/SolidWorks.Interop.swpublished~SolidWorks.Interop.swpublished.IPropertyManagerPage2Handler9_members.html)
- [LoadAddIn Method（SOLIDWORKS API Help 2026）](https://help.solidworks.com/2026/English/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.ISldWorks~LoadAddIn.html)
- [FileCloseNotify Delegate（SOLIDWORKS API Help 2026）](https://help.solidworks.com/2026/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.DSldWorksEvents_FileCloseNotifyEventHandler.html)
