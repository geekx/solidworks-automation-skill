# 贡献指南 / Contributing Guide

感谢你对 SolidWorks Automation Skill 项目的关注!

## 治理与维护模式

本仓库采用**单人核心维护 + 开放社区贡献**模式。任何外部开发者都可以通过 Fork + Pull Request 提交代码，由仓库 owner 评估代码质量、测试证据和实际价值后决定是否合并。

- **维护者**：[@wzyn20051216](https://github.com/wzyn20051216)（当前唯一具有写权限的账号）
- **不设置固定的独立审查人**，也不要求 PR 必须获得他人 approve
- **所有合并决策由 owner 一人作出**。本项目不提供独立复核能力，也不对外声称具备该能力
- 外部贡献者不持有写权限，无法直接推送任何受保护分支；一切外部变更只能经 PR 由 owner 合并

**该模式的已知边界**：合并质量依赖 owner 个人判断，不存在第二人交叉验证。这适用于工具型开源项目的社区协作，不适用于需要合规审计或第三方签核的场景。

### 当前治理限制（如实记录，截至 2026-10-27）

- `main` 的 ruleset 当前 `required_approving_review_count = 0`（单人维护模式的既定取值，不因此判定为治理失败）
- owner 仍位于 ruleset `bypass_actors` 中且 `bypass_mode = always`。**这是当前未解除的阻断项**：策略要求 bypass 列表为空，
  代码、文档与测试均按"任何 bypass 都视为不达标"执行
- 远程 CAD 准入的 `bypass_actors` 证据**不来自 CI 实时读取**——GitHub 仅在对该 ruleset 有写权限时才返回该字段，
  只读审计凭据读不到。因此改由**离线采集 + 签名证明**承担，CI 只做只读校验，详见
  [`docs/architecture/governance-attestation.md`](docs/architecture/governance-attestation.md)
- **该证明由维护者自行签发，公钥信任根存放于本仓库**，只提供防篡改留痕，**不构成独立审计、独立审批或双人复核**
- 仓库变量 `GOVERNANCE_TRUST_ROOT_ANCHOR` 提供仓库文件之外的第二道锚点，可阻止"公钥 + 日志 + 证明"被整体替换；
  但拥有仓库管理员权限者仍可一并修改该变量，因此它只是提高成本，不是独立可信根
- `.github/CODEOWNERS` 已声明治理敏感路径的负责人，但 `require_code_owner_review = false`，
  **文件本身不构成强制门禁**（单人仓库开启会因"作者不能批准自己的 PR"而死锁）
- `test-and-build` 已包含 `node --test tests/test_cad_ci_guard.cjs` 与
  `node scripts/ci/check_governance_drift.cjs`；后者只能把治理代码改动变成显式差异，**不能阻止有合并权者一并修改**
- **在上述调整完成并实际验证之前，不得对外声称本仓库具备独立审批能力或强制 CI 门禁**

上述限制的跟踪条目见发布阻断清单 B-02；让 owner 也无法绕过必需检查的最小变更方案（单字段清空
`bypass_actors`，含验收判据与回滚）见
[`docs/architecture/governance-bypass-remediation.md`](docs/architecture/governance-bypass-remediation.md)。
治理代码、工作流与信任根的修改仍需维护者人工确认。

### 维护者：规则集变更后必须重新采集证明

任何 ruleset 修改（含清空 `bypass_actors`）都会让 `updated_at` 与两个摘要（`policy_digest` / `snapshot_digest`）
同时变化，**旧证明立即失效**，远程 CAD 准入随之阻断。变更后需在本机执行一次采集并随 PR 提交新证明：

```bash
GH_TOKEN="$(gh auth token)" node scripts/ci/capture_governance_attestation.cjs --verify
```

采集工具在远端状态不合规时**默认拒签**（不写文件、非零退出）。若确需记录非合规状态用于诊断，
使用 `--allow-noncompliant`，产物会标记 `compliant: false`，永远无法通过准入。

改完信任根或证明后，还需运行 `node scripts/ci/check_governance_drift.cjs --update` 并一并提交清单，
否则 `test-and-build` 会因漂移而失败。

私钥仅存于本机 `%USERPROFILE%\.cad-governance\`，不得入库、不得写入 CI。完整流程、双摘要语义与密钥轮换步骤见上述文档。

## 如何贡献

### 报告 Bug

如果你发现了 Bug,请创建一个 Issue 并包含以下信息:

- SolidWorks 版本
- Python 版本
- 操作系统版本
- 详细的错误信息和堆栈跟踪
- 重现步骤

### 提交功能请求

如果你有新功能的想法:

1. 先检查 Issues 中是否已有类似请求
2. 创建新 Issue 描述你的想法
3. 说明使用场景和预期效果

### 提交代码

1. **Fork 仓库**
   ```bash
   git clone https://github.com/yourusername/solidworks-automation-skill.git
   cd solidworks-automation-skill
   ```

2. **创建分支**
   ```bash
   git checkout -b feature/your-feature-name
   ```

3. **编写代码**
   - 遵循现有代码风格
   - 添加必要的注释(中文)
   - 确保代码在 SolidWorks 2020 及以上版本可运行；当前真机验收基线为 **2026**，CAD 相关改动另见下文「CAD 相关修改的额外要求」

4. **测试**
   - 在 SolidWorks 中测试你的代码
   - 确保没有破坏现有功能

5. **提交**
   ```bash
   git add .
   git commit -m "添加: 你的功能描述"
   ```

6. **推送并创建 PR**
   ```bash
   git push origin feature/your-feature-name
   ```
   然后在 GitHub 上创建 Pull Request

## PR 审查与合并标准

外部 PR 由 owner 审查后决定合并或关闭。以下标准对内部与外部 PR 同等适用。

### 1. 提交前要求

- 分支基于**最新 `main`** 创建，避免"旧分支过 CI"式的假通过
- **一个 PR 只解决一个问题**。功能、修复、重构、格式化不要混在同一个 PR 里
- 不要夹带与 PR 目标无关的格式化或重排改动，这类改动会增加审查成本
- 破坏性变更（修改公开函数签名、改变默认行为、删除能力）必须在标题与描述中显式标注 `BREAKING`

### 2. PR 描述必须包含的六项

| 项 | 要求 |
| --- | --- |
| 问题 / 动机 | 要解决什么问题，复现条件；关联 Issue 编号 |
| 方案概述 | 改了什么、为什么这样改，替代方案为何不选 |
| 影响范围 | 是否触及生产门禁（Review / Verification / Execution Core）、依赖、导出格式、配置文件 |
| 向后兼容性 | 既有调用是否受影响；默认参数下行为是否保持不变 |
| 回滚方式 | 出问题时如何撤回，是否需要数据迁移 |
| 测试证据位置 | 离线测试与真机证据的实际路径 |

缺失上述任一项的 PR，owner 可以直接要求补齐而不进入代码审查。

### 3. 测试证据要求

**离线测试（必需）**

- 提交前本地执行 `python -m pytest -q`，必须全部通过
- 新增或修改功能必须附带测试；仅改文档或注释可豁免，但需在描述中说明
- 故障注入类测试不得删除，也不得为了通过而放宽断言

**真机测试（涉及 CAD 行为时必需）**

- 明确标注 SolidWorks 版本；本仓库当前真机验收基线为 **SolidWorks 2026**
- 明确区分"真实执行"与"合成 / mock"。mock 结果**不计入**真机统计，也不得用于宣称能力已验证
- 证据写入**新的独立目录**，不得覆盖或改写既有输出目录与历史证据

### 4. CI 检查

- PR 必须通过 `test-and-build` 状态检查（ruleset `protect-main` 的实际必需检查名）
- 该检查自 2026-10-28 起是**聚合门禁**：它 `needs` 五个并行作业，任一失败即失败，
  且失败时给出 `failure` 而不是 `skipped`（避免"没跑"被误读为"通过"）：

  | 作业 | 覆盖内容 |
  | --- | --- |
  | `governance-gate` | `node --test tests/test_cad_ci_guard.cjs`（当前 93 项）与 `check_governance_drift.cjs`（10 个敏感文件的漂移检测） |
  | `python-tests` | `pytest` 全量、skill / MCP / release 契约校验 |
  | `frontend-tests` | 工作台依赖安装、启动诊断、前端测试与构建 |
  | `rust-tests` | `cargo fmt -- --check`、`cargo test`、`cargo clippy -- -D warnings` |
  | `package-build` | 打包、updater 契约校验、便携版与安装包端到端 |

- 上述结构由 `tests/test_release_workflow_contract.py` 锁定：删掉 `merge_group` 触发器、
  改必需检查名、`needs` 漏作业、缺 `timeout-minutes`、丢弃首次 E2E 诊断等都会让该测试失败，
  而不是静默失效
- 必需检查要求**分支与 main 同步**（`strict_required_status_checks_policy = true`）：
  main 前进后必须更新分支并重跑，否则不能合并。目的是确保"合并后的树"就是 CI 验证过的那棵树
- **改动 `scripts/ci/`、`.github/workflows/`、`tests/test_cad_ci_guard.cjs`、
  `docs/architecture/evidence/` 或信任根时**，必须运行 `check_governance_drift.cjs --update`
  并一并提交 `.github/governance-sensitive-manifest.json`，否则该检查会阻断
- **本地通过不能替代 CI 通过**。两者结论不一致时，以 CI 为准并排查差异
- 端到端步骤允许**重试 1 次**以吸收 WebView2/CDP 启动竞态：第 1 次诊断会存为
  `release-output/e2e-diagnostics.attempt1.*` 并随构件上传（上传步骤为 `if: always()`）；
  重试后仍失败即判失败——**不允许用重试掩盖稳定缺陷**
- **真实 CAD 真机测试不是合并前置条件**：仓库没有自托管 Runner，`Windows CAD Regression` 为手动触发、
  非必需检查，无法被调度。合并判断只看托管 CI（`test-and-build`），真机证据由维护者在本机验收后写入 PR 描述

### 4.1 合并流程与独立审核的边界

**外部贡献者的 PR**：

1. 贡献者 Fork 并开 PR（本仓库外部账号无写权限，无法直接推受保护分支）
2. owner **人工**逐行审查代码、测试证据与影响面（维度见下一节）
3. `test-and-build` 必须**真实通过**；失败即不合并，不接受"本地跑过了"作为替代
4. 由 owner 合并；合并后 owner 负责跟进后续回归

**owner 自己提交的 PR（如实披露限制）**：

- 单人维护模式下，owner 的 PR **无法获得独立第三方审查**——没有第二人具备批准权限。
  本项目**不声称**这类 PR 经过独立审核。
- 因此 owner 的 PR 必须做到：PR 描述写明"无独立审查，因单人维护"；列出自查清单与测试输出；
  不得把自审写成"已审核通过"或伪造 approve 记录。
- **禁止**用 `bypass_actors`、管理员强制推送、关闭规则集、或把失败检查标记为 skipped 来让 PR 通过。
  唯一允许的例外是"检查因基础设施故障（如 Actions 配额耗尽）未能运行"，此时必须记录故障证据并说明未验证。

**合并队列（merge queue）当前不可用——如实记录**：

- 本仓库属**个人账号（`User`）**所有，GitHub 的 `merge_queue` 规则仅对组织（`Organization`）仓库开放。
  2026-10-28 实测：向 `protect-main` 增加该规则返回 `422 Invalid rule 'merge_queue'`；
  另用一个仅含该规则的**独立探针 ruleset** 提交，同样被拒，确认是能力缺失而非参数错误。
- 工作流已声明 `merge_group` 触发器，将来仓库迁入组织后可**直接启用**队列，无需再改工作流。
- **等效替代**：`strict_required_status_checks_policy = true`。`pull_request` 运行检查的是
  `refs/pull/N/merge`（main 与 PR 的合并结果），配合"必须与 main 同步"这一条，
  即可保证**合并后的树正是 CI 验证过的那棵树**。代价是 main 前进后要更新分支并重跑一次。
- **不要**把"子作业全绿"说成"已通过 merge queue"——当前没有队列，这只是强同步 + 真实 PR 检查。

**stacked PR 的已知缺口（历史记录与后续纪律）**：

- 2026-10-28 之前存在一条栈 `main ← #39 ← #42 ← #43`，中间层的目标分支是另一条特性分支，
  **不受必需检查约束**。该栈已于 2026-10-28 按序合并（`4e4df52` → `d9878f6` → `3237eca`）。
- 期间出现过**两次中间态 main 红灯**（`4e4df52`、`d9878f6` 的 push 运行），
  根因是便携版 E2E 的 page 竞态，已由 `6b9bee0` 修复；失败运行与日志全部保留可查。
- 今后若再开栈，必须逐层合并后对下一层**重新触发并确认** `test-and-build` 通过；
  "底层 PR 过了"不能推定为"上层已受门禁保护"，这条事实不得隐去。

### 5. 代码审查维度

owner 按以下维度判断是否合并：

1. **正确性**：逻辑是否成立，边界条件（空值、越界、并发、COM 断开）是否处理
2. **兼容性**：是否向后兼容；默认参数下既有行为是否逐位不变
3. **安全边界**：是否放宽了校验、是否可能把失败伪装成成功、是否引入自动审批或跳过审查的路径
4. **可维护性**：是否符合现有代码风格，是否有必要的文档字符串
5. **证据质量**：测试是否覆盖改动点，真机证据是否可复核，数值是否可追溯到原始产物
6. **实际价值**：是否解决真实问题，是否与项目方向一致

### 6. 拒绝或要求修改的条件

满足任一条即**不予合并**：

- 未通过 `test-and-build`，或提交了会破坏 CI 的改动
- 把工具调用成功当作任务完成，或在未验证时声称能力已通过
- 删除、注释掉或放宽既有测试与故障注入以"让测试变绿"
- 放宽生产门禁（`scripts/sw_review.py`、`scripts/core/verification.py`、`scripts/core/execution.py`）
- 引入绕过人工审查、自动审批或可配置跳过检查的路径
- 覆盖、改写或删除历史证据目录
- 一个 PR 混杂多个无关目标，且拒绝拆分
- 描述缺失关键信息，且在提醒后仍未补充

可以合并但需**要求修改**的情形：命名或注释不规范、缺少边界处理、测试覆盖面不足、文档未同步更新。

### 7. CAD 相关修改的额外要求

涉及建模、装配、配合、导出、干涉检测、行程验证的改动，还必须提供：

- **版本与后端**：实际 SolidWorks 版本、后端标识（如 `SldWorks.Application.34`），以及是否为自建独占实例
- **原生几何证据**：以原生 B-Rep / 特征树 / 配合回读为准，**不接受包围盒代替几何结论**
- **干涉与接触**：真实调用干涉检测 API；有意的零体积共边接触与体积穿透必须分开判定，不得把"非零接触条目"一律当作穿透
- **终态与清理**：说明文档保存、关闭、重开后的回读结果，以及是否残留 CAD 进程或文档
- **未验证范围声明**：明确写出本次**没有**验证的内容（例如：自由拖动、连续扫掠、接触动力学、载荷、Motion Study、制造放行）
- **不得外推**：静态工况的通过不能表述为"运动能力已验证"；本机验收不能表述为远程 CI 验收

### 8. 桌面端端到端测试（`ai_team/`）

`ai_team/` 下是 CAD Studio（Tauri + React）的真实浏览器端到端测试。
它们**不被 `pytest` 收集**，也不会在普通 `npm test` 中运行，需要单独执行。

**统一前置条件（缺任一项都会失败，属环境问题而非代码缺陷）：**

1. 已安装依赖并**构建过前端产物**：`cd apps/workbench-ui && npm ci && npm run build`
   （脚本从 `apps/workbench-ui/node_modules/playwright` 引入 Playwright，从 `apps/workbench-ui/dist` 加载页面）
2. 已安装 Chromium：`cd apps/workbench-ui && npx playwright install chromium`
3. 在**仓库根目录**执行（脚本用 `path.resolve(__dirname, "..")` 定位仓库根）

| 脚本 | 验证内容 | 额外前置 | 预估耗时 |
| --- | --- | --- | --- |
| `release-portable-e2e.cjs` | 便携包启动、诊断产物 | 先跑 `scripts/build_windows_release.ps1` 生成 `release-output/` | 3–8 min |
| `nsis-install-e2e.ps1` | NSIS 安装/卸载、升级路径 | 同上，且需管理员权限 | 5–15 min |
| `delivery-gate-e2e.cjs` | 交付门禁、版本记录、响应式布局 | 前端已构建 | 1–3 min |
| `preview-viewport-e2e.cjs` | JS 预览清单、DXF Worker、图层面板、canvas 非空 | 前端已构建 | 1–3 min |
| `workbench-e2e-final.cjs` | 工作台完整流程 | 前端已构建 + Tauri 可执行文件 | 3–8 min |
| `provider-ui-e2e.cjs` | Agent Provider 配置界面 | 前端已构建 | 2–5 min |
| `ui-layout-check.cjs` | 布局截图对比，输出到 `ai_team/ui_review/` | 前端已构建 | 1–2 min |

```powershell
# 典型顺序
cd apps/workbench-ui; npm ci; npm run build; npx playwright install chromium; cd ../..
node ai_team/delivery-gate-e2e.cjs
node ai_team/preview-viewport-e2e.cjs
```

`ai_team/*` 默认被 `.gitignore` 排除，只放行上述 7 个脚本（见 `.gitignore` 的白名单）。
新增脚本时需同时加入白名单，否则不会入库。

## 代码规范

### Python 代码风格

- 使用 4 个空格缩进
- 函数和变量使用 snake_case
- 类名使用 PascalCase
- 常量使用 UPPER_CASE
- 中文注释和文档字符串

### 文档规范

- 所有公共函数必须有文档字符串
- 文档字符串使用中文
- 包含参数说明和返回值说明

示例:
```python
def extrude_boss(model, sketch_name, depth, direction=True, merge=True):
    """
    凸台拉伸

    参数:
        model: IModelDoc2
        sketch_name: 草图名称(如 "Sketch1")
        depth: 拉伸深度(米)
        direction: True=正方向
        merge: True=合并结果

    返回:
        IFeature 对象
    """
    # 实现代码...
```

### 提交信息规范

使用中文,格式如下:

- `添加: 新功能描述`
- `修复: Bug 描述`
- `更新: 更新内容描述`
- `文档: 文档更新描述`
- `重构: 重构内容描述`

## 项目结构

```
solidworks-automation-skill/
├── scripts/              # 核心 Python 模块
│   ├── sw_connect.py    # 连接和文档管理
│   ├── sw_part.py       # 零件建模 API
│   ├── sw_assembly.py   # 装配体 API
│   ├── sw_drawing.py    # 工程图 API
│   └── sw_export.py     # 文件导出 API
├── references/          # API 参考文档
├── examples/            # 示例代码
├── README.md            # 项目说明
└── CONTRIBUTING.md      # 本文件
```

## 添加新功能

如果你想添加新的 API 封装:

1. 在对应的 `scripts/sw_*.py` 文件中添加函数
2. 在 `references/` 中更新相关文档
3. 在 `examples/` 中添加使用示例
4. 更新 README.md

## 需要帮助?

- 查看 [references/](./references/) 目录下的文档
- 查看 [examples/](./examples/) 目录下的示例
- 在 Issues 中提问
- 参考 [SolidWorks API 官方文档](https://help.solidworks.com/2024/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks_namespace.html)

## 行为准则

- 尊重所有贡献者
- 保持友好和专业
- 接受建设性的批评
- 关注对项目最有利的事情

感谢你的贡献! 🎉
