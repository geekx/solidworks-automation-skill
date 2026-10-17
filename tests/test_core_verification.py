"""@brief Verification Adapter 单元测试（Fake Reviewer，不依赖 SolidWorks / pywin32）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from core.state import VerificationResult, VerificationStatus  # noqa: E402
from core.trace import read_events  # noqa: E402
from core.verification import (  # noqa: E402
    aggregate_checks,
    aggregate_statuses,
    check_status,
    normalize_status,
    verify,
)


def test_reviewer_pass():
    result = verify(reviewer=lambda: {"status": "pass"})
    assert result.status == VerificationStatus.PASS


def test_reviewer_warn():
    result = verify(reviewer=lambda: {"status": "warning"})
    assert result.status == VerificationStatus.WARN
    assert result.manual_review_required is True


def test_reviewer_fail():
    result = verify(reviewer=lambda: {"status": "fail", "error_code": "E1"})
    assert result.status == VerificationStatus.FAIL
    assert result.error_code == "E1"


def test_reviewer_blocked():
    result = verify(reviewer=lambda: {"status": "blocked", "reason": "no env"})
    assert result.status == VerificationStatus.BLOCKED
    assert result.reason == "no env"


def test_unknown_reviewer_status_is_not_pass():
    result = verify(reviewer=lambda: {"status": "totally_weird"})
    assert result.status == VerificationStatus.BLOCKED
    assert result.status != VerificationStatus.PASS


def test_reviewer_exception_becomes_blocked():
    def boom():
        raise RuntimeError("reviewer crashed")

    result = verify(reviewer=boom)
    assert result.status == VerificationStatus.BLOCKED
    assert result.error_code == "reviewer_error"
    assert "RuntimeError" in result.reason


def test_no_reviewer_and_no_result_is_blocked():
    result = verify()
    assert result.status == VerificationStatus.BLOCKED
    assert result.error_code == "verification_unavailable"


def test_aggregate_all_pass():
    assert aggregate_checks([{"status": "pass"}, {"status": "pass"}]) == VerificationStatus.PASS


def test_aggregate_pass_warn():
    assert aggregate_statuses([VerificationStatus.PASS, VerificationStatus.WARN]) == VerificationStatus.WARN


def test_aggregate_pass_fail():
    assert aggregate_statuses([VerificationStatus.PASS, VerificationStatus.FAIL]) == VerificationStatus.FAIL


def test_aggregate_blocked_wins():
    assert (
        aggregate_statuses(
            [VerificationStatus.PASS, VerificationStatus.WARN, VerificationStatus.BLOCKED]
        )
        == VerificationStatus.BLOCKED
    )


def test_informational_fail_does_not_upgrade_to_fail():
    # severity=low/info 且 status=fail，只降级为 WARN，不升级为 FAIL
    assert check_status({"status": "fail", "severity": "low"}) == VerificationStatus.WARN
    assert check_status({"status": "fail", "severity": "info"}) == VerificationStatus.WARN
    assert check_status({"status": "fail", "optional": True}) == VerificationStatus.WARN
    assert check_status({"status": "fail", "required": False}) == VerificationStatus.WARN
    assert aggregate_checks([{"status": "pass"}, {"status": "fail", "severity": "info"}]) == VerificationStatus.WARN


def test_critical_fail_still_fails():
    assert check_status({"status": "fail", "severity": "critical"}) == VerificationStatus.FAIL
    assert check_status({"status": "fail"}) == VerificationStatus.FAIL


def test_verify_preserves_artifacts_and_evidence():
    result = verify(
        reviewer=lambda: {
            "status": "pass",
            "checks": [{"id": "c1", "status": "pass"}],
            "artifacts": [{"kind": "step", "path": "a.step"}],
        },
        artifacts=[{"kind": "step", "path": "a.step"}],
        evidence=[{"kind": "geometry_evidence"}],
    )
    assert result.status == VerificationStatus.PASS
    assert result.checks == [{"id": "c1", "status": "pass"}]
    # 去重：同一 path 只保留一次
    assert result.artifacts == [{"kind": "step", "path": "a.step"}]
    assert result.evidence == [{"kind": "geometry_evidence"}]


def test_verification_result_json_roundtrip():
    result = verify(reviewer=lambda: {"status": "fail", "reason": "missing artifact", "error_code": "E2"})
    restored = VerificationResult.from_json(result.to_json())
    assert restored.status == VerificationStatus.FAIL
    assert restored.reason == "missing artifact"
    assert restored.error_code == "E2"


def test_fake_reviewer_injectable():
    def fake_reviewer():
        return {"status": "review_required", "checks": [{"id": "dfm", "status": "pass"}]}

    result = verify(reviewer=fake_reviewer, capability_id="dfm_checks")
    assert result.status == VerificationStatus.WARN
    assert result.manual_review_required is True


def test_reviewer_result_list_aggregates():
    results = [{"status": "pass", "checks": [{"id": "a", "status": "pass"}]}, {"status": "fail", "error_code": "E3"}]
    result = verify(reviewer_result=results)
    assert result.status == VerificationStatus.FAIL
    assert result.error_code == "E3"
    assert result.checks == [{"id": "a", "status": "pass"}]


def test_trace_verification_events_written(tmp_path):
    result = verify(
        reviewer=lambda: {"status": "pass"},
        trace_dir=tmp_path,
        run_id="r1",
        step_id="s1",
        capability_id="part_and_features",
    )
    assert result.status == VerificationStatus.PASS
    events = read_events(tmp_path, "r1")
    assert [event["type"] for event in events] == ["verification.started", "verification.passed"]
    assert events[1]["verification_status"] == "PASS"
    assert events[1]["step_id"] == "s1"
    assert events[1]["capability_id"] == "part_and_features"


def test_trace_write_failure_does_not_affect_verification(tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    result = verify(reviewer=lambda: {"status": "pass"}, trace_dir=blocker, run_id="r1")
    assert result.status == VerificationStatus.PASS


def test_normalize_status_aliases():
    assert normalize_status("pass") == VerificationStatus.PASS
    assert normalize_status("warning") == VerificationStatus.WARN
    assert normalize_status("review_required") == VerificationStatus.WARN
    assert normalize_status("failed") == VerificationStatus.FAIL
    assert normalize_status("blocked") == VerificationStatus.BLOCKED
    assert normalize_status("ok") == VerificationStatus.PASS
    assert normalize_status("unsupported") == VerificationStatus.BLOCKED
    assert normalize_status(None) == VerificationStatus.BLOCKED
    assert normalize_status("") == VerificationStatus.BLOCKED
