# 上游同步策略（脱离 fork 后）

## 现状

| remote | 地址 | 用途 |
|---|---|---|
| `origin` | `https://github.com/geekx/solidworks-automation-skill.git` | 本项目主仓库（非 fork） |
| `upstream` | `https://github.com/wzyn20051216/solidworks-automation-skill.git` | 只读同步来源 |

旧的 fork 已改名为 `geekx/solidworks-automation-skill-legacy`，仅作备份；确认新仓库
稳定后可删除。GitHub 的 fork 网络关系无法「转正」，因此新仓库不再出现在上游的
fork 列表里，也不会自动向上游提 PR。

## 同步上游

```powershell
gh repo sync geekx/solidworks-automation-skill --source wzyn20051216/solidworks-automation-skill
```

或手动（推荐，便于先看差异）：

```powershell
git fetch upstream
git switch main
git log --oneline main..upstream/main    # 先看上游改了什么
git merge upstream/main                  # 或 git rebase upstream/main
git push origin main
```

**冲突原则：**

1. `capabilities.yaml`、`mcp-server/server.py`、`dotnet/.../SwAddin.cs` 是高频冲突点；
   冲突时保留本仓库的 Point & Prompt 修改，再补上游新增能力；
2. 上游新增能力默认不动，除非与本路线图冲突；
3. 合并后必须重跑 `pytest tests/test_point_prompt_*.py` 与 `python scripts/validate_mcp.py`。

## 不向上游合并

- 不再从本仓库向 `wzyn20051216/solidworks-automation-skill` 发起 PR；
- 如需把通用修复回馈上游，单独 fork 一个临时分支、人工挑选提交，而不是把本仓库整体同步回去；
- `upstream` remote 只用于 `fetch`，不要 `push`（可设置
  `git remote set-url --push upstream DISABLED` 防止手误）。

## 分支约定

| 分支 | 用途 |
|---|---|
| `main` | 集成分支，保持可运行 |
| `feature/point-prompt-*` | 交互层特性 |
| `agent/*` | 并行子 Agent 的隔离分支（见路线图 7.1 开发约定） |

合并前要求：L1 测试通过、`capabilities.yaml` 等级与证据一致、文档同步更新。
