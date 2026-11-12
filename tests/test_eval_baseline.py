"""@brief Reliability Baseline 回归守卫（deterministic，方向性比较 + Release Guard）。

Release Guard（V2 语义修正后）：
- nominal_first_pass_success_rate >= target_first_pass_rate
- escaped_false_completion_rate == 0（假成功零泄漏）
- expected_outcome_accuracy 达到 deterministic scenario 预期
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from evals.runner import build_scenarios, load_golden_workflows, run_eval  # noqa: E402

BASELINE_PATH = ROOT / "tests" / "evals" / "baseline.json"


def _load_baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def test_baseline_file_present():
    baseline = _load_baseline()
    assert baseline["schema_version"] == "2.0"
    assert "nominal_metrics" in baseline
    assert "robustness_metrics" in baseline
    assert "safety_metrics" in baseline


def test_baseline_counts_derived_from_source():
    """计数必须与源数据一致：不允许在测试里写死数字。"""
    baseline = _load_baseline()
    workflows = load_golden_workflows()
    scenarios = build_scenarios(workflows)
    assert baseline["workflow_count"] == len(workflows["workflows"])
    assert baseline["scenario_count"] == len(scenarios)


def test_baseline_scenario_breakdown_matches_source():
    """nominal / robustness / overall 三段计数应与一次真实评估一致。"""
    baseline = _load_baseline()
    current = run_eval()
    assert baseline["nominal_metrics"]["scenario_count"] == current.metrics["nominal"]["scenario_count"]
    assert baseline["robustness_metrics"]["scenario_count"] == current.metrics["robustness"]["scenario_count"]
    assert baseline["overall_diagnostic_metrics"]["scenario_count"] == current.metrics["overall"]["scenario_count"]
    nominal = current.metrics["nominal"]["scenario_count"]
    robustness = current.metrics["robustness"]["scenario_count"]
    assert nominal + robustness == current.metrics["overall"]["scenario_count"]


def test_nominal_first_pass_meets_target():
    current = run_eval()
    nominal_first_pass = current.metrics["nominal"]["first_pass_success_rate"]
    target = current.target_first_pass_rate
    assert nominal_first_pass >= target


def test_escaped_false_completion_is_zero():
    current = run_eval()
    assert current.metrics["safety"]["escaped_false_completion_rate"] == 0.0


def test_robustness_expected_outcome_accuracy_not_regressed():
    baseline = _load_baseline()
    current = run_eval()
    current_acc = current.metrics["robustness"]["expected_outcome_accuracy"]
    baseline_acc = baseline["robustness_metrics"]["expected_outcome_accuracy"]
    assert current_acc >= baseline_acc


def test_safety_metrics_not_regressed():
    baseline = _load_baseline()
    current = run_eval()
    # 负面：escaped false completion 必须保持 0（不得上升）
    assert current.metrics["safety"]["escaped_false_completion_rate"] <= baseline["safety_metrics"]["escaped_false_completion_rate"]
    # 正面：false completion detection 必须保持 100%（不得下降）
    assert current.metrics["safety"]["false_completion_detection_rate"] >= baseline["safety_metrics"]["false_completion_detection_rate"]
