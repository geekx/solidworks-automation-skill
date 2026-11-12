"""@brief Capability Facade 单元测试（读取真实 capabilities.yaml，不依赖 SolidWorks）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from core.capability import (  # noqa: E402
    capability_gap,
    get_backend_candidates,
    get_capability,
    get_capability_status,
    get_operation_route,
    load_capabilities,
    requires_review,
    resolve_backend,
)


def test_load_capabilities_reads_existing_yaml():
    payload = load_capabilities()
    assert payload["schema_version"] == "1.0"
    assert isinstance(payload["capabilities"], list)
    assert len(payload["capabilities"]) > 0


def test_get_capability_reads_verified_capability():
    item = get_capability("part_and_features")
    assert item is not None
    assert item["level"] == "verified"
    assert item["backends"] == ["solidworks-com"]


def test_get_capability_missing_returns_none():
    assert get_capability("does_not_exist") is None


def test_get_capability_status_verified():
    status = get_capability_status("part_and_features")
    assert status["status"] == "verified"
    assert status["review_required"] is False
    assert status["unattended_allowed"] is True


def test_get_capability_status_pilot():
    status = get_capability_status("sheet_metal")
    assert status["status"] == "pilot"
    assert status["review_required"] is True
    assert status["unattended_allowed"] is False


def test_get_capability_status_unknown():
    status = get_capability_status("does_not_exist")
    assert status["status"] == "unknown"
    assert status["review_required"] is None
    assert status["capability"] is None


def test_requires_review_capability():
    assert requires_review(capability_id="part_and_features") is False
    assert requires_review(capability_id="sheet_metal") is True
    assert requires_review(capability_id="does_not_exist") is None


def test_requires_review_operation():
    assert requires_review(operation_id="solidworks_standard_automation") is True
    assert requires_review(operation_id="does_not_exist") is None


def test_resolve_backend_reuses_existing_router():
    result = resolve_backend(
        "solidworks_standard_automation",
        available_backends=["solidworks-com-pywin32"],
    )
    assert result["status"] == "ready"
    assert result["backend"] == "solidworks-com-pywin32"
    assert result["review_required"] is True


def test_resolve_backend_no_backend_available():
    result = resolve_backend("solidworks_standard_automation", available_backends=[])
    assert result["status"] == "unavailable"
    assert result["error_code"] == "NO_COMPATIBLE_BACKEND_AVAILABLE"


def test_resolve_backend_unknown_operation():
    result = resolve_backend("does_not_exist", available_backends=["solidworks-com-pywin32"])
    assert result["status"] == "blocked"
    assert result["error_code"] == "UNKNOWN_OPERATION_ROUTE"


def test_get_backend_candidates_sorted():
    candidates = get_backend_candidates("solidworks_standard_automation")
    assert candidates
    priorities = [candidate["priority"] for candidate in candidates]
    assert priorities == sorted(priorities)
    assert candidates[0]["backend"] == "solidworks-com-pywin32"


def test_get_operation_route():
    route = get_operation_route("solidworks_standard_automation")
    assert route is not None
    assert route["review_required"] is True
    assert get_operation_route("does_not_exist") is None


def test_capability_gap_shape():
    gap = capability_gap("fancy_gear", "no tool and no backend")
    assert gap["status"] == "capability_gap"
    assert gap["requested_capability"] == "fancy_gear"
    assert gap["reason"] == "no tool and no backend"
    assert gap["searched_existing_tools"] is True
    assert gap["searched_backends"] is True
    assert gap["recommended_action"] == "agent_resolution_required"
