"""Apply / Rebuild / Verify 事务与 OperationJournal。

执行顺序固定（见 `docs/point-and-prompt/roadmap.md` 6.2）：

    Validate Document + Selection Token
        → Read Current Parameters
        → Create Operation Journal
        → Apply Whitelisted CAD Operation
        → Force Rebuild
        → Read Back Feature / Dimension
        → Run Checks
        → PASS: Commit Journal / FAIL: Rollback + 证据

本模块不直接依赖 COM：通过 ``DocumentAdapter`` 注入读/写/重建能力，便于在没有
SolidWorks 的机器上做事务逻辑测试；真实执行时使用 ``ComDocumentAdapter``。
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

try:
    from . import sw_cad_bridge as bridge
    from . import sw_edit_plan as edit_plan
except ImportError:  # pragma: no cover
    import sw_cad_bridge as bridge
    import sw_edit_plan as edit_plan

READBACK_TOLERANCE_MM = 0.01


class DocumentAdapter(Protocol):
    """写执行器需要的最小文档能力。"""

    def document_token(self) -> str: ...

    def read_dimension_mm(self, dimension: str) -> float | None: ...

    def write_dimension_mm(self, dimension: str, value_mm: float) -> None: ...

    def rebuild(self) -> dict[str, Any]: ...


class ComDocumentAdapter:
    """SolidWorks COM 适配器：只做参数读写、重建和回读，不做业务判断。"""

    def __init__(self, model: Any, *, document_token_value: str = "") -> None:
        self._model = model
        self._document_token = document_token_value

    def document_token(self) -> str:
        """返回文档 token（未显式给出时按当前路径现算）。"""
        if self._document_token:
            return self._document_token
        try:
            from scripts.sw_selection_context import describe_document

            return str(describe_document(self._model).get("documentToken") or "")
        except Exception:  # noqa: BLE001
            return ""

    # ------------------------------------------------------------------ 尺寸读写

    def _parameter(self, dimension: str) -> Any:
        """取尺寸对象；失败抛 ``_ParameterError``。"""
        try:
            parameter = self._model.Parameter(dimension)
        except Exception as exc:  # noqa: BLE001
            raise _ParameterError(f"无法读取尺寸 {dimension}：{type(exc).__name__}: {exc}") from exc
        if parameter is None:
            raise _ParameterError(f"尺寸 {dimension} 不存在或不可编辑")
        return parameter

    def read_dimension_mm(self, dimension: str) -> float | None:
        """读取尺寸的当前值（米 → 毫米）。"""
        parameter = self._parameter(dimension)
        try:
            value = getattr(parameter, "SystemValue")
        except Exception:  # noqa: BLE001
            value = getattr(parameter, "Value", None)
        try:
            return round(float(value) * 1000.0, 4)
        except (TypeError, ValueError):
            return None

    def write_dimension_mm(self, dimension: str, value_mm: float) -> None:
        """写入尺寸（毫米 → 米）。"""
        parameter = self._parameter(dimension)
        try:
            parameter.SystemValue = float(value_mm) / 1000.0
        except Exception as exc:  # noqa: BLE001
            raise _ParameterError(f"写入尺寸 {dimension} 失败：{type(exc).__name__}: {exc}") from exc

    def rebuild(self) -> dict[str, Any]:
        """强制重建并返回结果。"""
        for member in ("ForceRebuild3", "EditRebuild3"):
            try:
                result = getattr(self._model, member)
            except Exception:  # noqa: BLE001
                continue
            try:
                outcome = result(False) if member == "ForceRebuild3" else result()
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "method": member, "error": f"{type(exc).__name__}: {exc}"}
            return {"ok": bool(outcome), "method": member, "raw": bool(outcome)}
        return {"ok": False, "method": "none", "error": "文档不支持 ForceRebuild3/EditRebuild3"}


class _ParameterError(RuntimeError):
    """尺寸不存在或不可写。"""


# --------------------------------------------------------------------------- 事务


def _entry_id() -> str:
    """生成 Journal 条目 ID。"""
    return f"op-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class ApplyResult:
    """一次事务的结构化结果。"""

    status: str
    plan_id: str
    entry_id: str
    revision_before: int = 0
    revision_after: int = 0
    checks: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转为 MCP 返回 JSON。"""
        return {
            "status": self.status,
            "planId": self.plan_id,
            "entryId": self.entry_id,
            "revisionBefore": self.revision_before,
            "revisionAfter": self.revision_after,
            "checks": self.checks,
            "evidence": self.evidence,
            "error_code": self.error_code,
            "message": self.message,
            "retryable": self.status == "blocked",
        }


def _journal(event: str, payload: Mapping[str, Any]) -> None:
    """追加一条 Journal 记录；写失败只降级为 warning，不中断事务。"""
    record = {"entryId": payload.get("entryId"), "at": _utc_now(), "event": event}
    record.update({key: value for key, value in payload.items() if key != "entryId"})
    try:
        bridge.append_journal(record)
    except Exception:  # noqa: BLE001
        pass


def apply_plan(
    plan: Mapping[str, Any],
    context: Mapping[str, Any] | None,
    adapter: DocumentAdapter,
    *,
    confirmed: bool = False,
    post_check: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    tolerance_mm: float = READBACK_TOLERANCE_MM,
    write_journal: bool = True,
) -> dict[str, Any]:
    """执行白名单计划：校验 → 记录 → 写入 → 重建 → 回读 → 提交/回滚。"""
    entry_id = _entry_id()
    plan_id = str(plan.get("planId") or "unknown")
    operation = dict(plan.get("operation") or {})
    dimension = str(operation.get("dimension") or "")
    old_mm = _as_float(operation.get("oldValueMm"))
    new_mm = _as_float(operation.get("newValueMm"))
    token = str(adapter.document_token() or "")

    revision_before = bridge.document_revision(token) if token else 0
    result = ApplyResult(status="blocked", plan_id=plan_id, entry_id=entry_id, revision_before=revision_before)

    preview = edit_plan.preview_with_reader(
        plan,
        context,
        lambda name: adapter.read_dimension_mm(name),
        confirmed=confirmed,
    )
    result.checks.extend(preview["checks"])
    if preview["status"] != "ready":
        result.error_code = preview["error_code"] or "E_PLAN_INVALID"
        result.message = "计划未通过执行前校验，未写入模型。"
        if write_journal:
            _journal("blocked", {"entryId": entry_id, "planId": plan_id, "documentToken": token, "checks": preview["checks"]})
        return result.to_dict()

    if not dimension:
        result.error_code = "E_PLAN_INVALID"
        result.message = "计划没有给出尺寸全名。"
        return result.to_dict()

    before = adapter.read_dimension_mm(dimension)
    result.evidence["before"] = {"dimension": dimension, "valueMm": before}
    if write_journal:
        _journal(
            "start",
            {
                "entryId": entry_id,
                "planId": plan_id,
                "documentToken": token,
                "revisionBefore": revision_before,
                "dimension": dimension,
                "oldValueMm": before,
                "newValueMm": new_mm,
                "risk": plan.get("risk"),
                "invariants": list(plan.get("invariants") or []),
            },
        )

    try:
        adapter.write_dimension_mm(dimension, float(new_mm))
    except Exception as exc:  # noqa: BLE001
        result.status = "failed"
        result.error_code = "E_APPLY_FAILED"
        result.message = f"写入失败：{type(exc).__name__}: {exc}"
        result.evidence["write"] = {"ok": False, "error": str(exc)}
        if write_journal:
            _journal("failed", {"entryId": entry_id, "planId": plan_id, "documentToken": token, "error": result.message})
        return result.to_dict()

    rebuild = adapter.rebuild()
    result.evidence["rebuild"] = rebuild
    result.checks.append({"id": "rebuild", "result": "PASS" if rebuild.get("ok") else "FAIL", "detail": str(rebuild.get("method"))})
    if not rebuild.get("ok"):
        return _rollback(plan, adapter, result, entry_id, dimension, before, reason="重建失败", write_journal=write_journal)

    after = adapter.read_dimension_mm(dimension)
    result.evidence["after"] = {"dimension": dimension, "valueMm": after}
    matched = after is not None and abs(float(after) - float(new_mm)) <= tolerance_mm
    result.checks.append(
        {
            "id": "readback",
            "result": "PASS" if matched else "FAIL",
            "detail": f"回读 {after} mm，期望 {new_mm} mm（容差 {tolerance_mm} mm）",
        }
    )
    if not matched:
        return _rollback(plan, adapter, result, entry_id, dimension, before, reason="回读值与计划不一致", write_journal=write_journal)

    if post_check is not None:
        try:
            extra = dict(post_check(plan))
        except Exception as exc:  # noqa: BLE001
            extra = {"status": "error", "detail": f"{type(exc).__name__}: {exc}"}
        result.checks.append(
            {
                "id": "geometry_checks",
                "result": "PASS" if extra.get("status") in {None, "PASS", "pass"} else "FAIL",
                "detail": json.dumps(extra, ensure_ascii=False)[:2000],
            }
        )
        if extra.get("status") not in {None, "PASS", "pass"}:
            return _rollback(plan, adapter, result, entry_id, dimension, before, reason="几何检查未通过", write_journal=write_journal)

    revision_after = bridge.bump_document_revision(token) if token else revision_before
    result.status = "applied"
    result.revision_after = revision_after
    result.message = "修改已应用并通过回读校验。"
    if write_journal:
        _journal(
            "commit",
            {
                "entryId": entry_id,
                "planId": plan_id,
                "documentToken": token,
                "revisionBefore": revision_before,
                "revisionAfter": revision_after,
                "dimension": dimension,
                "oldValueMm": before,
                "newValueMm": after,
                "checks": result.checks,
            },
        )
    return result.to_dict()


def _rollback(
    plan: Mapping[str, Any],
    adapter: DocumentAdapter,
    result: ApplyResult,
    entry_id: str,
    dimension: str,
    before: float | None,
    *,
    reason: str,
    write_journal: bool,
) -> dict[str, Any]:
    """恢复旧值并重建；回滚失败时报告 ``E_ROLLBACK_FAILED``。"""
    result.status = "rolled_back"
    result.error_code = "E_READBACK_MISMATCH"
    result.message = f"{reason}，已尝试恢复原值。"
    if before is None:
        result.status = "failed"
        result.error_code = "E_ROLLBACK_FAILED"
        result.message = f"{reason}；没有可恢复的旧值记录。"
        return result.to_dict()

    try:
        adapter.write_dimension_mm(dimension, float(before))
        rebuild = adapter.rebuild()
        restored = adapter.read_dimension_mm(dimension)
    except Exception as exc:  # noqa: BLE001
        result.status = "failed"
        result.error_code = "E_ROLLBACK_FAILED"
        result.message = f"{reason}；回滚异常：{type(exc).__name__}: {exc}"
        result.evidence["rollback"] = {"ok": False, "error": str(exc)}
        return result.to_dict()

    ok = bool(rebuild.get("ok")) and restored is not None and abs(float(restored) - float(before)) <= READBACK_TOLERANCE_MM
    result.evidence["rollback"] = {"ok": ok, "restoredMm": restored, "rebuild": rebuild}
    result.checks.append({"id": "rollback", "result": "PASS" if ok else "FAIL", "detail": f"恢复为 {restored} mm"})
    if not ok:
        result.status = "failed"
        result.error_code = "E_ROLLBACK_FAILED"
        result.message = f"{reason}；回滚后回读仍不一致，需要人工处理。"
    if write_journal:
        _journal(
            "rollback" if ok else "rollback_failed",
            {
                "entryId": entry_id,
                "planId": plan.get("planId"),
                "documentToken": adapter.document_token(),
                "dimension": dimension,
                "restoredMm": restored,
                "reason": result.message,
            },
        )
    return result.to_dict()


def undo_last_operation(adapter: DocumentAdapter, *, write_journal: bool = True) -> dict[str, Any]:
    """撤销最近一次成功提交（同一文档、且 revision 未被推进过）。"""
    token = str(adapter.document_token() or "")
    entries = [entry for entry in bridge.read_journal(limit=200) if entry.get("documentToken") == token]
    commits = [entry for entry in entries if entry.get("event") == "commit"]
    if not commits:
        return {
            "status": "blocked",
            "error_code": "E_NO_PINNED_CONTEXT",
            "message": "没有可撤销的 AI 操作记录。",
            "retryable": False,
        }
    last = commits[-1]
    current_revision = bridge.document_revision(token) if token else 0
    if int(last.get("revisionAfter", -1)) != current_revision:
        return {
            "status": "blocked",
            "error_code": "E_STALE_CONTEXT",
            "message": "文档 revision 已推进，最近一次操作不是最新状态，拒绝盲目撤销。",
            "retryable": False,
        }
    dimension = str(last.get("dimension") or "")
    old_mm = _as_float(last.get("oldValueMm"))
    if not dimension or old_mm is None:
        return {"status": "blocked", "error_code": "E_PLAN_INVALID", "message": "Journal 缺少可恢复的旧值。", "retryable": False}

    entry_id = _entry_id()
    try:
        adapter.write_dimension_mm(dimension, old_mm)
        rebuild = adapter.rebuild()
        readback = adapter.read_dimension_mm(dimension)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error_code": "E_ROLLBACK_FAILED", "message": f"撤销失败：{exc}", "retryable": False}
    ok = bool(rebuild.get("ok")) and readback is not None and abs(float(readback) - old_mm) <= READBACK_TOLERANCE_MM
    revision = bridge.bump_document_revision(token) if token and ok else current_revision
    if write_journal:
        _journal(
            "undo" if ok else "undo_failed",
            {
                "entryId": entry_id,
                "planId": last.get("planId"),
                "documentToken": token,
                "dimension": dimension,
                "restoredMm": readback,
                "revisionAfter": revision,
            },
        )
    return {
        "status": "undone" if ok else "failed",
        "entryId": entry_id,
        "undoneEntryId": last.get("entryId"),
        "dimension": dimension,
        "restoredMm": readback,
        "revisionAfter": revision,
        "rebuild": rebuild,
        "error_code": None if ok else "E_ROLLBACK_FAILED",
        "message": "已恢复上一次操作前的参数。" if ok else "撤销后回读不一致，需要人工处理。",
        "retryable": False,
    }


def refresh_context_after_apply(context: dict[str, Any], revision_after: int) -> dict[str, Any]:
    """把上下文推进到新 revision，重算 selectionToken 并标记 fresh。"""
    try:
        from .sw_selection_context import bump_revision
    except ImportError:  # pragma: no cover
        from sw_selection_context import bump_revision

    return bump_revision(context, revision_after)


def _as_float(value: Any) -> float | None:
    """安全转 float。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
