"""@brief deterministic Fake handler / reviewer / capability 工厂 + 异常类型。

异常类型名与 ``core.recovery._EXCEPTION_KIND`` 对齐，使 ``classify_error`` 能正确
识别 BACKEND_UNAVAILABLE / CAPABILITY_GAP / USER_ACTION_REQUIRED 等类别。
"""
from __future__ import annotations

from typing import Any, Callable


class SolidWorksConnectionError(RuntimeError):
    """@brief 模拟后端不可用（classify → BACKEND_UNAVAILABLE）。"""


class UnsupportedFeatureError(ValueError):
    """@brief 模拟能力缺口（classify → CAPABILITY_GAP）。"""


class DependencyInstallDeclined(RuntimeError):
    """@brief 模拟用户拒绝安装/授权（classify → USER_ACTION_REQUIRED）。"""


class FakeCapabilityFacade:
    """@brief 可注入的 Capability Facade 替身。"""

    def __init__(self, candidates=None, capabilities=None):
        self.candidates = candidates or []
        self.capabilities = capabilities or {}

    def get_backend_candidates(self, operation_id):
        return list(self.candidates)

    def get_capability(self, capability_id):
        return self.capabilities.get(capability_id)


def success_handler(result: dict[str, Any]) -> Callable[[Any], dict[str, Any]]:
    """@brief 返回一个总是成功并返回给定 result 的 handler。"""
    def run(job=None):
        return dict(result)
    return run


def fail_handler(exc: BaseException) -> Callable[[Any], Any]:
    """@brief 返回一个总是抛出给定异常的 handler。"""
    def run(job=None):
        raise exc
    return run


def pass_reviewer() -> Callable[[], dict[str, Any]]:
    return lambda: {"status": "pass", "checks": [{"id": "ok", "status": "pass"}]}


def warn_reviewer() -> Callable[[], dict[str, Any]]:
    return lambda: {"status": "warning", "checks": [{"id": "warn", "status": "warning"}]}


def fail_reviewer(error_code: str = "E_FAIL") -> Callable[[], dict[str, Any]]:
    return lambda: {
        "status": "fail",
        "error_code": error_code,
        "checks": [{"id": "missing", "status": "fail", "severity": "P0"}],
    }


def blocked_reviewer(reason: str = "环境缺失") -> Callable[[], dict[str, Any]]:
    return lambda: {"status": "blocked", "reason": reason}


def crash_reviewer() -> Callable[[], Any]:
    def boom():
        raise RuntimeError("reviewer crashed")
    return boom


def ok_result(artifacts: list[str]) -> dict[str, Any]:
    """@brief 交付物齐全的成功 handler 结果。"""
    return {"message": "ok", "outputs": [{"kind": a, "path": f"out/{a}"} for a in artifacts]}


def incomplete_result() -> dict[str, Any]:
    """@brief handler 报成功但交付物缺失（用于 False Completion 场景）。"""
    return {"message": "handler 报成功但缺少交付物", "outputs": []}


def artifacts_from_result(result: dict[str, Any]) -> list[str]:
    """@brief 从 handler 结果提取 artifact kind 列表。"""
    outputs = result.get("outputs") or result.get("artifacts") or []
    if not isinstance(outputs, list):
        return []
    names = []
    for item in outputs:
        if isinstance(item, dict):
            names.append(str(item.get("kind") or item.get("path") or item))
        else:
            names.append(str(item))
    return names
