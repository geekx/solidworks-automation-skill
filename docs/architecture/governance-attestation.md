# 治理规则集证明：离线采集、签名与只读校验

**本文是治理证明的唯一定义源。** 其他文档若与本文件冲突，以本文件为准；
`CONTRIBUTING.md` 与 `docs/architecture/real-golden-merge-readiness.md` 只描述策略与验收，不复述字段细节。

本文说明 `main` 分支 ruleset 的 `bypass_actors` 如何在 **CI 不具备写权限**的前提下被证明为空，
以及该证明的信任边界、双摘要语义、生成流程、密钥轮换与失效规则。

证明格式版本：`cad-governance-attestation/v2`。

---

## 1. 为什么需要离线证明

GitHub 的规则集详情端点只需 `Metadata: read` 即可访问，但 `bypass_actors` 被单独门控：

> `bypass_actors` is only returned if the user making the API request has **write access** to the ruleset.

本项目实测结论（2026-10-27，公开仓库匿名请求对照）：

| 字段 | 管理员凭据 | 无写权限（匿名等价） |
| --- | --- | --- |
| `bypass_actors` | 有值 | **字段整体缺失**（不是 `null`） |
| `current_user_can_bypass` | 有值 | **同样缺失** |
| `updated_at` | 有值 | 有值（唯一可用的只读锚点） |
| 列表端点 `/rulesets` | **从不返回** `bypass_actors` | 从不返回 |

因此不存在"只用只读凭据读到 bypass"的路径。把写权限放进 CI 等于放弃最小权限，
故改为：**维护者在本机一次性采集并签名 → 证明入库 → CI 只读验签。**

---

## 2. 信任边界（必须如实披露）

### 2.1 本机制能做到

- **防篡改留痕**：证明被改动后签名必然失效；
- **配置漂移检测**：远端任何配置变更都会改变 `updated_at`，旧证明随即失效；
- **身份绑定**：证明绑定不可变 `repository.id` 与 `ruleset.id`，不能张冠李戴；
- **完整性分离**：`policy_digest` 与 `snapshot_digest` 分别覆盖"规则内容"与"完整状态"。

### 2.2 本机制做不到

| 限制 | 原因 | 代码中的披露位 |
| --- | --- | --- |
| 不是独立审计 | 证明由维护者自行签发 | `governance.independent_audit` 恒 `false` |
| 信任根与被保护对象同仓 | 公钥在仓库内，能合并 main 者可同时替换公钥与证明 | `trust_root_stored_in_repository` |
| 仓库变量锚点非绝对 | 有仓库管理员权限者可连变量一起改 | `trust_root_anchor` 检查 + 手册条件 |
| CODEOWNERS 未强制 | `require_code_owner_review = false`，单人仓库开启会死锁 | `code_owner_review_not_enforced` |
| PR 可同时改验证代码 | 无可信基线工作流执行判定逻辑 | `trusted_baseline_workflow_not_established` |

**仓库变量 `GOVERNANCE_TRUST_ROOT_ANCHOR` 的作用**：它存放在仓库文件之外，因此
"同时改公钥 + 改轮换日志 + 用新私钥重签证明"这一整组操作会与锚点不符而被拒绝。
这提高了攻击成本并留下痕迹，但**不阻止**拥有仓库管理员权限的人一并修改变量。

### 2.3 信任锚点的计算

```bash
node -e "const l=require('./scripts/ci/governance_attestation.cjs');
const {TRUSTED_KEYS}=require('./scripts/ci/governance_trust_root.cjs');
console.log(l.trustRootAnchor(TRUSTED_KEYS))"
```

输出形如 `sha256:<64 位十六进制>`，写入仓库变量 `GOVERNANCE_TRUST_ROOT_ANCHOR`。
计算覆盖 `keys`（含 `pem` / `not_before` / `revoked_at`）与完整 `log`；PEM 会先归一化 CRLF 与首尾空白。
**锚点缺失、格式非法、实际信任根不可计算、或不匹配，一律阻断。**

---

## 3. 双摘要语义（不可混用）

| 摘要 | 覆盖范围 | 用途 | 不覆盖 |
| --- | --- | --- | --- |
| `policy_digest_sha256` | `id`, `name`, `target`, `enforcement`, 分支 include/exclude, 全部 `rules`（type + parameters） | 证明"调整 bypass 前后规则内容没有被误改" | **不含** `bypass_actors`、`updated_at` |
| `snapshot_digest_sha256` | 上述全部 + `bypass_actors`（归一化排序） + `updated_at` + `created_at` | 绑定"采集那一刻的完整治理状态" | — |

规则：

1. **不能用 `policy_digest` 未变来声称完整配置未变化。** 改 bypass 只改 `snapshot_digest`。
2. `policy_digest` 必须三方一致：证明自述值 = 按证明内容重算 = 按线上只读内容重算。
3. `snapshot_digest` 必须自洽；若线上凭据**能**看到 `bypass_actors`（管理员场景），还须与线上完整快照一致。
4. 只读凭据看不到 bypass 时，`snapshot_digest` 与线上状态的绑定由 `updated_at` 相等承担——
   这是**间接绑定**，不得表述为"逐字段比对线上完整配置"。

---

## 4. 证明结构（v2）

```jsonc
{
  "schema": "cad-governance-attestation/v2",
  "repository": { "id": "1184045765", "full_name": "wzyn20051216/solidworks-automation-skill" },
  "ruleset": {
    "id": 25631760, "name": "protect-main", "target": "branch", "enforcement": "active",
    "ref_name_include": ["refs/heads/main"], "ref_name_exclude": [],
    "updated_at": "...", "created_at": "...",
    "rules": [ { "type": "deletion" }, { "type": "pull_request", "parameters": { } } ],
    "bypass_actors": [ ]
  },
  "policy_digest_sha256":   "<64 hex>",
  "snapshot_digest_sha256": "<64 hex>",
  "compliant": false,
  "noncompliance_reasons": ["bypass_not_empty"],
  "governance": {
    "mode": "single_maintainer",
    "independent_audit": false,
    "bypass_requirement": "empty",
    "trust_boundary": "……"
  },
  "captured_at": "…", "expires_at": "…",
  "generator": { "tool": "capture_governance_attestation.cjs", "node": "v22.x" },
  "signer": { "key_id": "ed25519:<sha256>", "key_type": "ed25519", "signed_at": "…" },
  "signature": "<base64 Ed25519>"
}
```

签名覆盖**除 `signature` 外的全部字段**（规范化 JSON：键排序、数组保序）。
因此 `compliant` / `noncompliance_reasons` / 两个摘要 / `bypass_actors` 都在签名保护内，
不能靠事后翻字段把非合规改成合规。

### 4.1 `compliant` 的含义

`compliant` 表示**该证明记录的状态是否满足准入前提**，由 `evaluateCompliance()` 统一判定，判据为：

- `enforcement === 'active'`；
- `target === 'branch'`；
- 分支范围恰好只有 `refs/heads/main` 且 exclude 为空；
- 存在 `pull_request` 与 `required_status_checks` 规则；
- 必需状态检查含 `test-and-build`；
- `bypass_actors` 为数组且长度为 0（缺失记为 `bypass_unreadable`，不视为通过）。

校验侧不仅要求 `compliant === true` 且 `noncompliance_reasons` 为空，还会**按证明内容重算**，
两者不一致即判 `attestation_compliance_inconsistent`。

---

## 5. 生成流程（维护者本机）

```bash
# 一次性：生成签名密钥对。私钥留在本机，公钥由 governance_trust_root.cjs 固定
node -e "const c=require('node:crypto'),f=require('node:fs'),p=require('node:path');
const dir=p.join(require('node:os').homedir(),'.cad-governance');f.mkdirSync(dir,{recursive:true});
const {publicKey,privateKey}=c.generateKeyPairSync('ed25519');
f.writeFileSync(p.join(dir,'ed25519-private.pem'),privateKey.export({type:'pkcs8',format:'pem'}),{mode:0o600});
console.log(publicKey.export({type:'spki',format:'pem'}))"

# 每次规则集变更后：采集并签名（默认只在合规时签发）
GH_TOKEN="$(gh auth token)" node scripts/ci/capture_governance_attestation.cjs --verify

# 只打印不入库
GH_TOKEN="$(gh auth token)" node scripts/ci/capture_governance_attestation.cjs --print-only

# 非合规状态需要留证时（诊断用途，产物永远无法通过准入）
GH_TOKEN="$(gh auth token)" node scripts/ci/capture_governance_attestation.cjs --allow-noncompliant
```

参数与退出码：

| 参数 | 说明 |
| --- | --- |
| `--ttl-days N` | 有效期，`1..30`，默认 30 |
| `--out PATH` | 输出路径，默认 `docs/architecture/evidence/governance-ruleset-attestation.json` |
| `--key PATH` | 私钥路径，默认 `%USERPROFILE%\.cad-governance\ed25519-private.pem` |
| `--print-only` | 只打印到标准输出，不落盘 |
| `--allow-noncompliant` | 允许在非合规状态留证，产物标记 `compliant: false` |
| `--verify` | 采集后立即用只读快照与仓库信任根复核 |

| 退出码 | 含义 |
| --- | --- |
| `0` | 签发了 `compliant: true` 的证明 |
| `1` | 失败（含**默认拒签**：远端不合规、看不到 bypass、身份不符） |
| `2` | 签发了 `compliant: false` 的诊断证明 |
| `3` | `--verify` 未通过 |

**默认拒签是硬约束**：远端不合规时不会写出任何文件，避免"已生成"被误读成"已通过"。

---

## 6. CI 只读校验项（28 项）

`scripts/ci/cad_ci_guard.cjs` 的 `evaluatePolicy()` 逐项判定，任一为 false 即 `blocked`。

**上下文与来源**

1. `trusted_repository`
2. `manual_branch_only`
3. `protected_ref_is_main`
4. `protected_immutable_source`

**规则集结构**

5. `ruleset_active`
6. `pull_request_required`
7. `required_status_checks_present`（必须含 `test-and-build`）

**信任根**

8. `trust_root_integrity`
9. `trust_root_rotation_log`
10. `trust_root_anchor`

**证明绑定**

11. `attestation_present`
12. `attestation_schema`
13. `attestation_declaration`
14. `attestation_signature`
15. `attestation_repository`
16. `attestation_ruleset_identity`
17. `attestation_main_scope`
18. `attestation_fresh`
19. `attestation_policy_digest`
20. `attestation_snapshot_digest`
21. `attestation_updated_at`
22. `attestation_compliant`
23. `no_bypass_actors`
24. `live_bypass_consistent`

**凭据与运行环境**

25. `authorized_actor_and_rerunner`
26. `protected_environment_exists`
27. `protected_deployment_branches`
28. `idle_windows_cad_runner`

### 6.1 fail-closed 复述

以下情形**一律阻断**，且不得把"看不到"解释为"为空"：

- `bypass_actors` 字段缺失、类型不是数组、或数组非空；
- 证明缺失、不可解析、schema 不符、签名无效、key 未知或已吊销；
- 仓库 ID / 规则集 ID / 名称 / `enforcement` / 分支范围任一不匹配；
- 证明过期、TTL 超过 30 天、`captured_at` 超出时钟偏移的未来；
- 证明或线上的 `updated_at` 不一致；
- `policy_digest` 或 `snapshot_digest` 不自洽；
- 信任根缺失、结构错误、轮换日志断裂、退役密钥未撤销；
- 仓库变量 `GOVERNANCE_TRUST_ROOT_ANCHOR` 缺失、格式非法或不匹配。

### 6.2 路径注入隔离

`GOVERNANCE_ATTESTATION_PATH` **仅在 `NODE_ENV=test` 时生效**。生产运行忽略该变量并读取固定路径
`docs/architecture/evidence/governance-ruleset-attestation.json`；若检测到被忽略的注入尝试，
会写入治理风险 `attestation_path_overridden_by_test_env` 并记录 `attestation_source`。

---

## 7. 失效规则

| 远端变化 | 失效机制 | 结果 |
| --- | --- | --- |
| 修改规则内容 | `policy_digest` 变化 | blocked |
| 修改 bypass | `snapshot_digest` 变化 + `bypass_empty` | blocked |
| 任何配置修改 | `updated_at` 变化 | blocked |
| 规则集被删除重建 | `ruleset.id` 变化 | blocked |
| 证明过期 | `expires_at` | blocked |
| 公钥被替换 | `trust_root_anchor` | blocked |
| 退役密钥未撤销 | `trust_root_rotation_log` | blocked |

清空 bypass 后**必须立即重新采集**：旧证明因 `updated_at` 与两个摘要全部变化而失效，
这是刻意设计，不是缺陷。

---

## 8. 密钥固定与轮换

1. 本机生成新密钥对，私钥留在 `%USERPROFILE%\.cad-governance\`，**永不入库**（`.gitignore` 已排除）；
2. 在 `governance_trust_root.cjs` 的 `KEYS` 中**新增**条目，禁止删除旧条目；
3. 在 `ROTATION_LOG` 末尾追加新 `key_id`；
4. **给退役密钥设置 `revoked_at`**。缺失不被默认为已撤销——
   退役但未撤销会让 `trust_root_rotation_log` 失败并**全员阻断**；
5. 用 `trustRootAnchor()` 重新计算锚点，更新仓库变量 `GOVERNANCE_TRUST_ROOT_ANCHOR`；
6. 重新采集证明；
7. 运行 `node scripts/ci/check_governance_drift.cjs --update` 并随同一 PR 提交清单。

轮换日志强制检查项：日志条目对应的密钥必须存在、`KEYS` 中每个密钥必须在日志中登记、
日志不得重复、除最后一项外都必须有可解析的 `revoked_at`、最后一项不得被撤销。

> 已知局限：旧证明在其 `signed_at` 早于 `revoked_at` 时仍可验签通过，这是为保留历史可审计性。
> 因此撤销密钥**不会**让旧证明立即作废，需要靠重新采集与 `updated_at` 绑定。

---

## 9. 漂移检测（`check_governance_drift.cjs`）

`test-and-build` 会运行此脚本。它把 10 个治理敏感文件的 SHA-256 与
`.github/governance-sensitive-manifest.json` 比对，任一差异即 `BLOCKED`。

**定位说明**：这是**漂移检测**，不是防篡改。有合并权的人可以同时修改目标文件与清单——
但那样会留下一个包含清单变更的显式 diff，而不是静默生效。真正的抗篡改需要可信基线工作流
（`pull_request_target` 且不检出 PR 代码）或外部审批链，当前仓库不具备该条件。

清单文件本身不纳入摘要（自引用无法收敛），由 `.github/CODEOWNERS` 覆盖。

```bash
node scripts/ci/check_governance_drift.cjs          # 校验
node scripts/ci/check_governance_drift.cjs --list   # 打印当前摘要
node scripts/ci/check_governance_drift.cjs --update # 有意变更后重建清单
```

---

## 10. 自检

```bash
# 准入策略、证明链路、采集工具、漂移检测的完整回归（当前 93 项）
node --test tests/test_cad_ci_guard.cjs

# 漂移检测
node scripts/ci/check_governance_drift.cjs

# 仅核对离线证明与线上配置是否仍一致（不启动 CI、不启动 CAD）
GH_TOKEN="$(gh auth token)" node scripts/ci/capture_governance_attestation.cjs --allow-noncompliant --print-only --verify
```

上述测试全部使用**本地合成快照**与一次性密钥，不代表真实远端验证。

---

## 11. 未解决与未验证

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 当前远端 `bypass_actors` 非空 | **未解决** | `bypass_mode: always`，证明如实记录 `compliant: false` |
| 仓库变量 `GOVERNANCE_TRUST_ROOT_ANCHOR` | **未配置** | 需在 B5 远端治理阶段设置 |
| `cad-validation` Environment | **不存在** | B-01 阻断项 |
| 自托管 Windows CAD Runner | **0 台** | B-01 阻断项 |
| `CAD_CI_AUDIT_TOKEN` | **不存在** | 只读令牌，需维护者创建；缺失时 guard 直接阻断 |
| 远程 CAD CI 实际执行 | **NOT VERIFIED** | 从未成功运行过 |
| 跨机器部署 | **NOT VERIFIED** | 未在第二台 Windows + SolidWorks 环境实测 |
| `updated_at` 是否在任何变更时都更新 | **NOT VERIFIED** | 需真实改一次 ruleset 才能确认 |
| 认证只读令牌下 bypass 是否与匿名一致缺失 | **NOT VERIFIED** | 暂无该令牌且本轮不得创建 |
| 抗恶意 PR 同时改 guard + 工作流 + 测试 | **未闭环** | 仅有漂移检测，无可信基线工作流 |
