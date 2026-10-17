"""@brief Recovery Decision Layer 单元测试（确定性、无 SolidWorks / pywin32 依赖）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from core.recovery import DEFAULT_MAX_RETRIES, ErrorKind, classify_error, decide  # noqa: E402
from core.state import RecoveryDecision  # noqa: E402
from core.trace import read_events  # noqa: E402


class FakeCapability:
    """可注入的 Capability Facade 替身。"""

    def __init__(self, candidates=None, capabilities=None):
        self.candidates = candidates or []
        self.capabilities = capabilities or {}

    def get_backend_candidates(self, operation_id):
        return self.candidates

    def get_capability(self, capability_id):
        return self.capabilities.get(capability_id)


def test_transient_retries_remaining_returns_retry():
    decision = decide(error_kind=ErrorKind.TRANSIENT, retry_count=0, max_retries=2)
    assert decision.decision == "retry"
    assert decision.error_kind == "transient"
    assert decision.retry_count == 0


def test_transient_retry_exhausted_returns_fail():
    decision = decide(error_kind=ErrorKind.TRANSIENT, retry_count=2, max_retries=2)
    assert decision.decision == "fail"


def test_invalid_argument_returns_replan():
    assert decide(error_kind=ErrorKind.INVALID_ARGUMENT).decision == "replan_required"
    assert decide(exception=ValueError("bad argument")).decision == "replan_required"
    assert decide(error_code="invalid_neutral_document").decision == "replan_required"


def test_backend_unavailable_with_fallback_returns_fallback():
    facade = FakeCapability(
        candidates=[
            {"backend": "solidworks-com-pywin32", "priority": 10},
            {"backend": "solidworks-com-comtypes", "priority": 20},
        ]
    )
    decision = decide(
        error_kind=ErrorKind.BACKEND_UNAVAILABLE,
        operation_id="solidworks_standard_automation",
        failed_backend="solidworks-com-pywin32",
        capability_facade=facade,
    )
    assert decision.decision == "fallback_backend"
    assert decision.fallback_backend == "solidworks-com-comtypes"


def test_backend_unavailable_no_fallback_not_fallback():
    facade = FakeCapability(candidates=[{"backend": "solidworks-com-pywin32", "priority": 10}])
    decision = decide(
        error_kind=ErrorKind.BACKEND_UNAVAILABLE,
        operation_id="solidworks_standard_automation",
        failed_backend="solidworks-com-pywin32",
        capability_facade=facade,
    )
    assert decision.decision != "fallback_backend"
    assert decision.decision == "block"


def test_environment_missing_with_fallback():
    decision = decide(error_kind=ErrorKind.ENVIRONMENT_MISSING, fallback_backend="headless-occt-python")
    assert decision.decision == "fallback_backend"
    assert decision.fallback_backend == "headless-occt-python"


def test_environment_missing_no_fallback_blocks():
    decision = decide(error_kind=ErrorKind.ENVIRONMENT_MISSING)
    assert decision.decision == "block"


def test_verification_failed_returns_replan():
    decision = decide(verification_result={"status": "fail", "error_code": "drawing_final_pdf_required"})
    assert decision.decision == "replan_required"
    assert decision.error_kind == "verification_failed"


def test_user_action_required():
    decision = decide(error_kind=ErrorKind.USER_ACTION_REQUIRED)
    assert decision.decision == "user_action_required"


def test_policy_blocked_cannot_be_bypassed():
    assert decide(error_kind=ErrorKind.POLICY_BLOCKED).decision == "block"
    assert decide(policy_result={"reasons": ["git push"]}).decision == "block"


def test_capability_gap_returns_replan():
    assert decide(error_kind=ErrorKind.CAPABILITY_GAP).decision == "replan_required"
    assert (
        decide(capability_result={"status": "capability_gap", "requested_capability": "fancy_gear"}).decision
        == "replan_required"
    )


def test_fatal_returns_fail():
    decision = decide(error_kind=ErrorKind.FATAL)
    assert decision.decision == "fail"


def test_unknown_error_does_not_default_to_retry():
    decision = decide(message="something completely unrecognized")
    assert decision.decision != "retry"
    assert decision.decision == "fail"
    assert classify_error(message="whatever") == ErrorKind.FATAL
    assert classify_error() == ErrorKind.FATAL


def test_verification_pass_does_not_trigger_recovery():
    assert decide(verification_result={"status": "pass"}) is None


def test_verification_warn_does_not_trigger_recovery():
    assert decide(verification_result={"status": "warn"}) is None


def test_required_fail_not_masked_by_blocked():
    verification = {
        "status": "blocked",
        "checks": [
            {"id": "critical_artifact", "status": "fail", "severity": "P0"},
            {"id": "blocked_env", "status": "blocked"},
        ],
    }
    decision = decide(verification_result=verification)
    assert decision.decision == "replan_required"
    assert decision.error_kind == "verification_failed"


def test_retry_count_never_exceeds_max_retries():
    assert decide(error_kind=ErrorKind.TRANSIENT, retry_count=0, max_retries=2).decision == "retry"
    assert decide(error_kind=ErrorKind.TRANSIENT, retry_count=1, max_retries=2).decision == "retry"
    assert decide(error_kind=ErrorKind.TRANSIENT, retry_count=2, max_retries=2).decision == "fail"
    assert decide(error_kind=ErrorKind.TRANSIENT, retry_count=9, max_retries=2).decision == "fail"
    # 默认预算
    assert DEFAULT_MAX_RETRIES == 2
    assert decide(error_kind=ErrorKind.TRANSIENT, retry_count=2).decision == "fail"


def test_decision_json_serializable():
    decision = decide(error_kind=ErrorKind.FATAL)
    restored = RecoveryDecision.from_json(decision.to_json())
    assert restored.decision == "fail"
    assert restored.error_kind == "fatal"


def test_trace_recovery_event_written(tmp_path):
    decide(error_kind=ErrorKind.TRANSIENT, retry_count=0, max_retries=2, trace_dir=tmp_path, run_id="r1", step_id="s1")
    events = read_events(tmp_path, "r1")
    assert [event["type"] for event in events] == ["recovery.retry"]
    assert events[0]["step_id"] == "s1"


def test_trace_failure_does_not_affect_decision(tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    decision = decide(error_kind=ErrorKind.FATAL, trace_dir=blocker, run_id="r1")
    assert decision.decision == "fail"


def test_fake_capability_facade_injectable():
    facade = FakeCapability(
        candidates=[{"backend": "a", "priority": 10}, {"backend": "b", "priority": 20}]
    )
    decision = decide(
        error_kind=ErrorKind.ENVIRONMENT_MISSING,
        operation_id="op",
        failed_backend="a",
        capability_facade=facade,
    )
    assert decision.decision == "fallback_backend"
    assert decision.fallback_backend == "b"


def test_deterministic_same_input_same_output():
    first = decide(error_kind=ErrorKind.BACKEND_UNAVAILABLE, fallback_backend="headless-occt-python")
    second = decide(error_kind=ErrorKind.BACKEND_UNAVAILABLE, fallback_backend="headless-occt-python")
    assert first.to_dict() == second.to_dict()


def test_classify_exception_types():
    assert classify_error(exception=TimeoutError("slow")) == ErrorKind.TRANSIENT
    assert classify_error(exception=ModuleNotFoundError("no ocp")) == ErrorKind.ENVIRONMENT_MISSING
    assert classify_error(exception=ValueError("bad spec")) == ErrorKind.INVALID_ARGUMENT
    # 未知异常 → FATAL（绝不默认 transient）
    assert classify_error(exception=RuntimeError("boom")) == ErrorKind.FATAL
