"""检索质量与初始工具列表体积的回归门槛；不调用真实 SolidWorks。

指标口径：
- top-5：期望工具出现在前 5 条候选中的比例（发布门槛 ≥95%）；
- top-1：期望工具排在首位的比例（只报告，不单独设门槛）；
- no-match：与 SolidWorks 无关的查询不得返回任何候选；
- 体积：tools/list 的协议 JSON（UTF-8 字节）在 discovery 模式下的降幅，
  这是确定性代理指标，不代表某个具体模型的 tokenizer 计数。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp-server"))

import policy  # noqa: E402
from approval import ApprovalLedger  # noqa: E402
from gate import ToolGate  # noqa: E402

DOCUMENT = json.loads((ROOT / "tests" / "evals" / "mcp_discovery_queries.json")
                      .read_text(encoding="utf-8"))
EXPECTED = [case for case in DOCUMENT["cases"] if "expect" in case]
NO_MATCH = list(DOCUMENT["no_match"])

TOP5_THRESHOLD = 0.95
SIZE_REDUCTION_THRESHOLD = 0.90


def _gate(tmp_path: Path, mode: str) -> ToolGate:
    import server

    return ToolGate(server._gate._manager, mode=mode, approval_mode="auto",
                    ledger=ApprovalLedger(tmp_path / mode))


def _payload_bytes(gate: ToolGate) -> int:
    """按协议字段序列化 tools/list，作为体积代理（不含内部分派元数据）。"""
    payload = []
    for tool in gate.list_tools():
        data = tool.model_dump(exclude_none=True)
        item = {key: data[key] for key in ("name", "title", "description", "annotations")
                if key in data}
        if "parameters" in data:
            item["inputSchema"] = data["parameters"]
        payload.append(item)
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def test_eval_set_is_meaningful():
    assert len(EXPECTED) >= 60
    assert len(NO_MATCH) >= 5
    import server

    catalog = server._gate.catalog()
    for case in EXPECTED:
        assert catalog.describe(case["expect"]) is not None, case["expect"]


def test_top5_recall_and_no_match_precision(tmp_path):
    import server

    catalog = server._gate.catalog()
    misses: list[str] = []
    top1 = 0
    for case in EXPECTED:
        names = [item["name"] for item in catalog.search(case["query"], 5)]
        if case["expect"] not in names:
            misses.append(f"{case['id']}: {case['query']!r} -> {names}")
        elif names[0] == case["expect"]:
            top1 += 1

    leaks = [f"{query!r} -> {[item['name'] for item in catalog.search(query, 5)]}"
             for query in NO_MATCH if catalog.search(query, 5)]

    recall = (len(EXPECTED) - len(misses)) / len(EXPECTED)
    assert recall >= TOP5_THRESHOLD, (
        f"top-5 {recall:.1%}、top-1 {top1 / len(EXPECTED):.1%}；未命中:\n" + "\n".join(misses))
    assert not leaks, "无关查询返回了候选:\n" + "\n".join(leaks)


def test_search_results_carry_effect_from_policy(tmp_path):
    gate = _gate(tmp_path, "search")
    results = gate.search("检查装配体有没有干涉", 5)
    assert results[0]["name"] == "solidworks_interference_check"
    assert results[0]["effect"] == policy.Effect.READ.value
    write = next(item for item in gate.search("保存文档", 5) if item["name"] == "solidworks_save_document")
    assert write["effect"] == policy.Effect.WRITE.value


@pytest.mark.parametrize("query", ["SolidWorks 版本号是多少", "SolidWorks version"])
def test_version_query_finds_connection_tool_first(tmp_path, query):
    catalog = _gate(tmp_path, "search").catalog()
    results = catalog.search(query, 5)
    assert results and results[0]["name"] == "solidworks_connect"


def test_discovery_mode_cuts_initial_tool_list_size(tmp_path):
    full = _gate(tmp_path, "full")
    search = _gate(tmp_path, "search")
    full_bytes = _payload_bytes(full)
    search_bytes = _payload_bytes(search)
    reduction = 1 - search_bytes / full_bytes
    assert reduction >= SIZE_REDUCTION_THRESHOLD, (
        f"full={full_bytes}B ({len(full.list_tools())} 个工具)，"
        f"search={search_bytes}B ({len(search.list_tools())} 个入口)，降幅 {reduction:.1%}")
