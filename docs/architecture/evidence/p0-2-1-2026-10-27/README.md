# P0-2.1 公开证据索引（2026-10-27）

本目录只包含**经过隐私审查、适合公开**的 P0-2.1 验收证据摘要。
原始证据包不在此处，仍保留在维护者本机。

| 项 | 值 |
| --- | --- |
| 阶段 | P0-2.1（夹爪机械保持 + 公差提案 + 五轮真实静态行程） |
| 分支 | `test/p0-real-gripper-retention-travel-2026` |
| 验收时冻结源码 | `df4d4c9a4143602d5af7077a7be7b19cee4850c2` |
| 最终提交（仅文档与证据索引） | `b414341b70dbc75b845f0f494817b4eab2674284` |
| 正式套件 | `output/p0-2-1-real-five-05`（五轮全部 acceptance passed） |

## 文件

| 文件 | 内容 | 是否脱敏 |
| --- | --- | --- |
| `p0-2-1-work-state.json` | 阶段工作状态：正式套件、历史失败上下文、pending 项、终态 | 否（原文，无敏感命中） |
| `p0-2-1-evidence-sha256.json` | 548 项逐文件 SHA-256 清单（相对路径） | **是**，更新脱敏证据的摘要 |
| `p0-2-1-independent-seal-verification.json` | 独立封存校验：整包哈希、549 成员、字节级比对、45 状态与 20 越限复核 | 否 |
| `suite-summary.redacted.json` | 五轮套件汇总（`acceptance_status` / `suite_checks` / `metrics` / 逐轮判定） | **是**，本机绝对路径前缀替换为 `<repo>` |
| `manifest.json` | 每个文件的原始哈希与发布哈希，以及隐私审查结论 | — |

## 如何复核

发布文件摘要按 Git 中的 UTF-8、LF 字节计算；`.gitattributes` 确保相关 JSON 检出为 LF。原始摘要仍记录原始文件字节，与发布摘要可能因脱敏或换行符规范化不同。

```bash
# 1. 逐文件核对发布包完整性（以本目录为基准）
python - <<'PY'
import json, hashlib, pathlib
root = pathlib.Path("docs/architecture/evidence/p0-2-1-2026-10-27")
m = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
for f in m["files"]:
    digest = hashlib.sha256((root / f["published"]).read_bytes()).hexdigest()
    print(f["published"], "OK" if digest == f["published_sha256"] else "MISMATCH")
PY

# 2. 核对声明值是否自洽（549 成员 / 190 原生哈希 / 45 静态状态 / 20 越限控制）
cat docs/architecture/evidence/p0-2-1-2026-10-27/p0-2-1-independent-seal-verification.json
```

## 隐私审查结论

| 项 | 结论 |
| --- | --- |
| 扫描范围 | 个人姓名、邮箱、用户名、主机名、用户目录、会话 ID、UUID、令牌、私钥、本机绝对路径 |
| `p0-2-1-work-state.json` | 无命中；`exec_session_id` 为 `null` |
| `p0-2-1-evidence-sha256.json` | 无命中（仅相对路径） |
| `p0-2-1-independent-seal-verification.json` | 无命中 |
| `p0-2-1-real-five-05/summary.json` | **命中 5 处本机绝对路径**，已脱敏后发布 |

**脱敏不等于原始文件。** 脱敏文件及其更新后的清单的 `published_sha256` 与 `original_sha256` 不同，
manifest 同时记录两者，不得把脱敏副本当作原始证据引用。

## 已知限制（不得忽略）

1. **原始证据包不在仓库内。** `output/p0-2-1-evidence-20261027.zip`（26,746,788 字节 / 549 成员 /
   SHA-256 `16a5e333…f3b4`）只在维护者本机保留。外部复核者**只能核对声明值与清单**，
   **不能独立重放**原始证据。
2. **`.gitignore` 不等于安全。** 本目录的文件之所以公开，是因为通过了上面的隐私审查，
   而不是因为它们被某些规则排除或放行。
3. **哈希一致不等于可公开。** SHA-256 只证明字节未变，不证明内容适合公开。
4. **`work-state.json` 是历史快照。** 其中 `offline_final: "1034 passed / 1 skipped"` 记录的是
   验收当时的离线回归结果；当前离线回归为 1035 passed / 1 skipped（新增了治理门禁测试）。
   该历史值**不修改**，以免篡改当时记录。
5. **本机通过 ≠ 发布通过。** 五轮真机通过只说明这台 SolidWorks 2026 环境通过；
   远程 CAD CI 从未成功运行，跨机器部署未验证。

## 其他证据文件的处理

`p0-2-1-real-gripper-travel-2026-10-27.json` 曾包含本机路径和用户目录，现已原位置脱敏；
其发布摘要已更新到本目录的清单中。更新后的清单是衍生文件，manifest 保留原始清单摘要。
原始封存包没有改写，发布副本不得被视为原始字节。仓库历史和已有副本仍可能包含脱敏前内容。
其他历史证据及其封存记录没有随本次处理更改。
