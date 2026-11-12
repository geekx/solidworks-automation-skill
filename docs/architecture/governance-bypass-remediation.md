# 绕过必需检查的风险与最小修复方案

> 状态：**方案已定，未实施**。远端 ruleset 未改动，等待维护者单独批准。
> 适用范围：`protect-main` 规则集（id `25631760`）。
> 相关实现：[`governance-attestation.md`](governance-attestation.md)。

---

## 1. 现状（2026-10-27 实测，只读）

```json
{
  "id": 25631760,
  "name": "protect-main",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["refs/heads/main"], "exclude": [] } },
  "rules": ["deletion", "non_fast_forward", "pull_request", "required_status_checks"],
  "required_status_checks": [{ "context": "test-and-build", "integration_id": 15368 }],
  "required_approving_review_count": 0,
  "require_code_owner_review": false,
  "bypass_actors": [{ "actor_id": 199356225, "actor_type": "User", "bypass_mode": "always" }],
  "updated_at": "2026-10-22T18:42:30.335+08:00"
}
```

## 2. 风险：`bypass_mode = always` 的作用

GitHub 的 `bypass_actors` 让列出的主体**不受该规则集全部规则约束**，包括：

- 不需要开 PR（`pull_request` 规则失效）；
- 不需要 `test-and-build` 通过（`required_status_checks` 失效）；
- 不受 `deletion` / `non_fast_forward` 约束（可强推、可删分支）。

因此，**当前"必需检查"只是对 owner 之外的主体有效**。对本项目这种单人维护仓库，
这等于"几乎对所有可能发生写入的操作都无效"。

### 2.1 为什么签名证明解决不了这个问题

离线证明证明的是"某时刻采集到的规则集配置里 `bypass_actors` 为空"。
它是**配置快照的真实性证明**，不是**绕过行为的阻止机制**：

| 机制 | 能证明 | 不能做 |
| --- | --- | --- |
| 签名证明 + `trust_root_anchor` | 采集时刻 bypass 为空、配置未被静默篡改 | 阻止 owner 在有 bypass 的情况下直接推 main |
| `check_governance_drift` | 治理文件被改动时产生显式差异 | 阻止有权限者一并改文件后照常合并 |

只要 `bypass_actors` 非空，上述两者都可以被"合法地"绕过——不是被破解，而是规则本身允许。

### 2.2 当前的实际暴露面

| 场景 | 现状 |
| --- | --- |
| 外部贡献者强推 `main` | ❌ 不可能（无写权限） |
| 外部贡献者跳过必需检查合并 | ❌ 不可能（无合并权限） |
| **owner 直接推 `main`** | ✅ **可以**（bypass always） |
| **owner 跳过 `test-and-build` 合并** | ✅ **可以** |
| owner 强制覆盖历史 | ✅ 可以（`non_fast_forward` 被绕过） |

---

## 3. 最小变更方案

### 3.1 改动内容

**单字段变更**：`bypass_actors: [...] → []`。其余字段（`rules`、`conditions`、`enforcement`、
`required_status_checks`、`pull_request` 参数）**一律不动**。

### 3.2 为什么这是最小方案

| 备选 | 评价 |
| --- | --- |
| 删除整个 ruleset 重建 | ❌ 会丢失 `updated_at` 连续性，且重建过程本身有一段时间无保护 |
| 改 `enforcement: evaluate` | ❌ 等于关闭强制力，方向相反 |
| 新增"指向特性分支的 ruleset" | 超出本目标（本目标只是消除 bypass），且会显著增加单人维护成本 |
| **清空 `bypass_actors`** | ✅ 一次 PUT、单字段、可逐字段回读验证、可原样回滚 |

### 3.3 执行前置条件（缺一不可）

1. 完整备份当前 ruleset JSON，记录 SHA-256；
2. 确认 `test-and-build` 在**托管 Runner** 上可运行（`windows-2022`，不需要自托管机）；
3. 确认维护者接受"此后每次改 `main` 都必须走 PR + 等 CI"；
4. 记录回滚触发条件（见 §5）。

### 3.4 执行步骤

```bash
# 1) 备份
gh api repos/wzyn20051216/solidworks-automation-skill/rulesets/25631760 > ruleset-backup-$(date +%Y%m%d).json
sha256sum ruleset-backup-*.json

# 2) 仅改 bypass_actors（其余字段取自上一步的备份，逐个复制）
gh api -X PUT repos/wzyn20051216/solidworks-automation-skill/rulesets/25631760 \
  --input ruleset-without-bypass.json

# 3) 立即回读完整结构
gh api repos/wzyn20051216/solidworks-automation-skill/rulesets/25631760 \
  --jq '{bypass:.bypass_actors, rules:[.rules[].type], upd:.updated_at, cond:.conditions}'

# 4) 重签证明（旧证明必因 updated_at + snapshot_digest 变化而失效）
GH_TOKEN="$(gh auth token)" node scripts/ci/capture_governance_attestation.cjs --verify
node scripts/ci/check_governance_drift.cjs --update
```

### 3.5 验收判据

| 判据 | 期望 |
| --- | --- |
| `bypass_actors` | `[]`（空数组，不是字段缺失） |
| `rules` | 仍为 `deletion` / `non_fast_forward` / `pull_request` / `required_status_checks` 四类 |
| `conditions.ref_name.include` | 仍为 `["refs/heads/main"]` |
| `required_status_checks` | 仍为 `test-and-build` |
| **`policy_digest`** | **与变更前完全相同**（证明规则内容未被误改） |
| **`snapshot_digest`** | **必然变化**（覆盖 bypass 与 `updated_at`） |
| `updated_at` | 变化（这是唯一可只读观测的变更锚点） |
| 新证明 `--verify` | 通过，且 `compliant: true`、`noncompliance_reasons` 为空 |

> 两个摘要的分工在此处是唯一正确的用法：`policy_digest` 不变说明"只动了 bypass"，
> `snapshot_digest` 变化说明"完整快照确实变了"。不能拿前者声称整体未变。

### 3.6 变更后的必然结果

清空后，owner 的操作方式随之改变：

- 改 `main` **必须**开 PR 并通过 `test-and-build`；
- 紧急情况（CI 故障、配额耗尽）**没有**"直接推"这条捷径。此时的正确做法是：
  记录故障证据 → 用 `workflow_dispatch` 手动重试 → 仍失败则把变更推迟，或单独批准一次临时放开并**事后补记**；
- 任何"临时把 bypass 加回去"的动作本身是一次**新的规则集变更**，会让证明失效，必须重新采集。

---

## 4. 与 CI 门禁的关系

清空 bypass 只解决"owner 能绕过"。它**不解决**以下三项，仍需分别对待：

| 缺口 | 现状 | 说明 |
| --- | --- | --- |
| 管理员可修改规则集本身 | 存在 | 有 admin 权限者可以再次把 bypass 加回去并更新证明；只能靠"变更留痕 + 人工复核"，不能技术上阻止 |
| 无强制 code owner review | 存在 | `require_code_owner_review = false`；单人仓库开启会因"作者不能批准自己 PR"死锁 |
| PR 可同时改 guard/工作流/测试 | 存在 | 仅靠漂移检测产生显式差异，不能阻止 |

这三项必须在对外表述中继续披露，**不得**因为清空了 bypass 就宣称"治理闭环"。

---

## 5. 回滚方案

**触发条件**（任一成立立即回滚）：回读发现规则类型减少、`conditions` 变化、
`required_status_checks` 丢失、或 `test-and-build` 无法在托管 Runner 上运行。

```bash
# 原样 PUT 回备份（不要手写简化 body）
gh api -X PUT repos/wzyn20051216/solidworks-automation-skill/rulesets/25631760 \
  --input ruleset-backup-YYYYMMDD.json
# 回滚后必须重新 GET 复核，并重新采集证明；不能沿用旧证明
```

代码侧不使用 `git reset --hard`；需要撤销提交时用 `git revert`。

---

## 6. 停止条件

- 备份不完整或无法恢复；
- 无法确认修改只影响 `bypass_actors`（`policy_digest` 变化即视为失败）；
- 回读时 bypass 仍非空或字段不可读；
- 任何动作会触及代码推送、PR 合并或 Release 发布（本方案只改远端规则，不涉及代码）；
- 维护者未完成 §3.3 的前置确认。
