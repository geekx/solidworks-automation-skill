"""EditPlan：白名单参数化修改计划的校验、能力门禁与预览。

Agent 只能生成这里允许的操作类型；执行前必须通过：

1. Schema 校验（`docs/point-and-prompt/edit-plan.schema.json`）；
2. 目标 token / 文档 revision 与当前上下文一致；
3. 旧值与当前回读值一致（调用方提供 reader 时）；
4. `capabilities.yaml` 中对应原子能力允许执行；
5. 用户确认（medium 及以上风险）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Mapping

PLAN_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "docs" / "point-and-prompt" / "edit-plan.schema.json"
CAPABILITIES_PATH = Path(__file__).resolve().parents[1] / "capabilities.yaml"

#: 允许的计划操作类型 → 执行器实现键。
WHITELIST: dict[str, str] = {
    "modify_sketch_dimension": "dimension",
    "modify_feature_depth": "dimension",
    "modify_fillet_radius": "dimension",
    "modify_chamfer_distance": "dimension",
}

#: 操作 → 依赖的原子能力。
CAPABILITY_BY_KIND: dict[str, str] = {
    "modify_sketch_dimension": "solidworks_point_prompt_parametric_edit_apply",
    "modify_feature_depth": "solidworks_point_prompt_parametric_edit_apply",
    "modify_fillet_radius": "solidworks_point_prompt_parametric_edit_apply",
    "modify_chamfer_distance": "solidworks_point_prompt_parametric_edit_apply",
}

#: 允许执行的能力等级；其余等级一律阻断。
EXECUTABLE_LEVELS = {"verified", "pilot"}

#: 旧值比对容差（毫米）。
OLD_VALUE_TOLERANCE_MM = 0.01

#: 单次修改的绝对上限（毫米），防止把米/毫米混用成灾难值。
MAX_DELTA_MM = 2000.0


def load_schema() -> dict[str, Any]:
    """加载 EditPlan Schema。"""
    return json.loads(PLAN_SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_plan(plan: Mapping[str, Any]) -> list[str]:
    """按 Schema 校验计划；返回错误列表（空表示通过）。"""
    try:
        import jsonschema  # type: ignore

        validator = jsonschema.Draft202012Validator(load_schema())
        errors = [
            f"{'/'.join(str(part) for part in error.absolute_path)}: {error.message}"
            for error in validator.iter_errors(dict(plan))
        ]
    except ImportError:
        errors = _structural_errors(plan)
    operation = plan.get("operation") or {}
    kind = str(operation.get("kind") or "")
    if kind and kind not in WHITELIST:
        errors.append(f"operation.kind: {kind} 不在白名单 {sorted(WHITELIST)}")
    return errors


def _structural_errors(plan: Mapping[str, Any]) -> list[str]:
    """无 jsonschema 时的最小结构校验。"""
    errors: list[str] = []
    for key in ("schemaVersion", "planId", "createdAt", "target", "operation", "risk", "requiresConfirmation"):
        if key not in plan:
            errors.append(f"missing field: {key}")
    target = plan.get("target") or {}
    for key in ("documentToken", "documentRevision", "selectionToken", "entityToken"):
        if target.get(key) in (None, ""):
            errors.append(f"target.{key}: required")
    operation = plan.get("operation") or {}
    if not operation.get("kind"):
        errors.append("operation.kind: required")
    for key in ("oldValueMm", "newValueMm"):
        value = operation.get(key)
        if not isinstance(value, (int, float)) or value <= 0:
            errors.append(f"operation.{key}: must be a positive number")
    return errors


def make_plan(
    operation: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    risk: str = "low",
    invariants: list[str] | None = None,
    summary: str = "",
    gate: bool = False,
    requires_confirmation: bool | None = None,
    plan_id: str | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """构造一个结构合法的 EditPlan（仍应再跑 ``validate_plan``）。"""
    plan = {
        "schemaVersion": "1.0",
        "planId": plan_id or f"plan-{int(time.time() * 1000)}",
        "createdAt": _utc_now(),
        "summary": summary,
        "target": dict(target),
        "operation": dict(operation),
        "invariants": list(invariants or []),
        "risk": risk,
        "requiresConfirmation": bool(requires_confirmation if requires_confirmation is not None else risk in {"medium", "high", "critical"}),
        "gate": bool(gate),
    }
    if notes:
        plan["notes"] = notes
    return plan


def operation_kind(plan: Mapping[str, Any]) -> str:
    """返回计划的操作类型。"""
    return str((plan.get("operation") or {}).get("kind") or "")


def plan_dimension(plan: Mapping[str, Any]) -> str:
    """返回计划作用的尺寸全名（若显式给出）。"""
    operation = plan.get("operation") or {}
    return str(operation.get("dimension") or "")


def capability_level(capability_id: str, capabilities: Mapping[str, Any] | None = None) -> str:
    """从 `capabilities.yaml` 读取能力等级；未声明视为 ``not_implemented``。"""
    data = capabilities if capabilities is not None else load_capabilities()
    for capability in data.get("capabilities", []):
        if capability.get("id") == capability_id:
            return str(capability.get("level") or "not_implemented")
    return "not_implemented"


def load_capabilities() -> dict[str, Any]:
    """读取能力清单（文件是 JSON 结构的 YAML）。"""
    try:
        return json.loads(CAPABILITIES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"capabilities": []}


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _check(check_id: str, result: str, detail: str = "") -> dict[str, str]:
    item = {"id": check_id, "result": result}
    if detail:
        item["detail"] = detail
    return item


def preview_plan(
    plan: Mapping[str, Any],
    context: Mapping[str, Any] | None,
    *,
    current_value_mm: float | None = None,
    confirmed: bool = False,
    capabilities: Mapping[str, Any] | None = None,
    old_value_tolerance_mm: float = OLD_VALUE_TOLERANCE_MM,
) -> dict[str, Any]:
    """校验并预览计划；不写模型。

    返回 ``{"status": "ready"|"needs_confirmation"|"blocked", "checks": [...],
    "diff": {...}, "error_code": str|None}``。
    """
    checks: list[dict[str, str]] = []
    errors = validate_plan(plan)
    checks.append(_check("schema", "PASS" if not errors else "FAIL", "；".join(errors)))
    operation = dict(plan.get("operation") or {})
    target = dict(plan.get("target") or {})
    old_mm = _as_float(operation.get("oldValueMm"))
    new_mm = _as_float(operation.get("newValueMm"))

    if context:
        document = context.get("document") or {}
        state = context.get("modelState") or {}
        selection = context.get("selection") or {}
        token_checks = {
            "documentToken": document.get("documentToken") == target.get("documentToken"),
            "documentRevision": int(state.get("revision", -1)) == int(target.get("documentRevision", -2)),
            "selectionToken": selection.get("selectionToken") == target.get("selectionToken"),
            "entityToken": any(
                entity.get("entityToken") == target.get("entityToken")
                for entity in selection.get("objects") or []
            ),
        }
        failed = [name for name, passed in token_checks.items() if not passed]
        checks.append(
            _check(
                "target_tokens",
                "PASS" if not failed else "FAIL",
                "全部匹配" if not failed else f"不匹配：{', '.join(failed)}",
            )
        )
        staleness = (context.get("staleness") or {}).get("status", "fresh")
        checks.append(
            _check(
                "context_staleness",
                "PASS" if staleness == "fresh" else "FAIL",
                "上下文新鲜" if staleness == "fresh" else f"上下文状态为 {staleness}",
            )
        )
        entity = next(
            (
                item
                for item in selection.get("objects") or []
                if item.get("entityToken") == target.get("entityToken")
            ),
            None,
        )
        resolution = (entity or {}).get("resolution") or {}
        checks.append(
            _check(
                "entity_resolution",
                "PASS" if resolution.get("status") == "resolved" else "WARN",
                f"解析状态：{resolution.get('status', 'unknown')}",
            )
        )
    else:
        checks.append(_check("context", "WARN", "没有提供上下文，仅做静态校验"))

    if current_value_mm is not None and old_mm is not None:
        matched = abs(float(current_value_mm) - old_mm) <= old_value_tolerance_mm
        checks.append(
            _check(
                "old_value",
                "PASS" if matched else "FAIL",
                f"当前 {current_value_mm:.4f} mm，计划 {old_mm:.4f} mm",
            )
        )
    else:
        checks.append(_check("old_value", "WARN", "未提供当前值回读，执行前必须再次校验"))

    capability_id = CAPABILITY_BY_KIND.get(operation_kind(plan), "")
    level = capability_level(capability_id, capabilities) if capability_id else "not_implemented"
    checks.append(
        _check(
            "capability",
            "PASS" if level in EXECUTABLE_LEVELS else "FAIL",
            f"{capability_id or '未声明'} = {level}",
        )
    )

    delta = None
    if old_mm is not None and new_mm is not None:
        delta = round(new_mm - old_mm, 4)
        sensible = 0 < abs(delta) <= MAX_DELTA_MM and 0.2 <= (new_mm / old_mm) <= 5.0
        checks.append(
            _check(
                "value_range",
                "PASS" if sensible else "WARN",
                f"Δ={delta} mm，比例={new_mm / old_mm:.3f}" if sensible else f"变化幅度异常：{old_mm} → {new_mm} mm",
            )
        )

    needs_confirmation = bool(plan.get("requiresConfirmation")) and not confirmed
    checks.append(
        _check(
            "confirmation",
            "PASS" if not needs_confirmation else "PENDING",
            "已确认" if not needs_confirmation else f"风险等级 {plan.get('risk')} 需要用户确认",
        )
    )

    failed_checks = [item for item in checks if item["result"] == "FAIL"]
    status = "blocked" if failed_checks else ("needs_confirmation" if needs_confirmation else "ready")
    if failed_checks:
        error_code = "E_TOKEN_MISMATCH" if any(item["id"] == "target_tokens" for item in failed_checks) else "E_PLAN_INVALID"
        if any(item["id"] == "capability" for item in failed_checks):
            error_code = "E_CAPABILITY_BLOCKED"
    elif needs_confirmation:
        error_code = "E_CONFIRMATION_REQUIRED"
    else:
        error_code = None

    return {
        "status": status,
        "planId": plan.get("planId"),
        "risk": plan.get("risk"),
        "gate": bool(plan.get("gate")),
        "checks": checks,
        "diff": {
            "dimension": plan_dimension(plan) or None,
            "feature": operation.get("feature"),
            "oldValueMm": old_mm,
            "newValueMm": new_mm,
            "deltaMm": delta,
        },
        "invariants": list(plan.get("invariants") or []),
        "error_code": error_code,
        "requiresConfirmation": bool(plan.get("requiresConfirmation")),
    }


def _as_float(value: Any) -> float | None:
    """安全转 float。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def preview_with_reader(
    plan: Mapping[str, Any],
    context: Mapping[str, Any] | None,
    reader: Callable[[str], float | None] | None,
    *,
    confirmed: bool = False,
) -> dict[str, Any]:
    """带「当前值回读」的预览；reader 接收尺寸全名并返回毫米值。"""
    current = None
    dimension = plan_dimension(plan)
    if reader is not None and dimension:
        try:
            current = reader(dimension)
        except Exception:  # noqa: BLE001 - 回读失败只降级为 WARN
            current = None
    return preview_plan(plan, context, current_value_mm=current, confirmed=confirmed)
