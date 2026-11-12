"""7.1 运行时并行流水线的骨架：revision 流 + 单写微操作队列 + 评估门禁。

这里的「并行」是**计算并行 + 写串行**：写请求进入单写队列逐个落地，每个微操作后
广播 revision；评估与规划在写通道之外进行，用 revision 对齐结论。它不是多线程
COM 并行——同一 SolidWorks 实例的写操作永远串行。

模块保持进程内、纯逻辑实现，便于在没有 SolidWorks 的机器上验证调度与门禁；
真正的评估沙箱（导出 STEP/网格后外部计算）通过 ``evaluators`` 注入。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

try:
    from . import sw_cad_bridge as bridge
except ImportError:  # pragma: no cover
    import sw_cad_bridge as bridge


# --------------------------------------------------------------------------- Revision 流


class RevisionStream:
    """单调递增的 revision 事件流；所有 Agent 结论必须绑定 revision。"""

    def __init__(self, start: int = 0) -> None:
        self._revision = int(start)
        self._events: list[dict[str, Any]] = []
        self._subscribers: list[Callable[[dict[str, Any]], None]] = []

    @property
    def revision(self) -> int:
        """当前 revision。"""
        return self._revision

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """订阅后续 revision 事件。"""
        self._subscribers.append(callback)

    def publish(self, event: Mapping[str, Any]) -> int:
        """发布一个事件并递增 revision；返回新 revision。"""
        self._revision += 1
        record = dict(event)
        record["revision"] = self._revision
        record.setdefault("at", _utc_now())
        self._events.append(record)
        for callback in list(self._subscribers):
            try:
                callback(dict(record))
            except Exception:  # noqa: BLE001 - 订阅者异常不得污染写通道
                continue
        return self._revision

    def since(self, revision: int) -> list[dict[str, Any]]:
        """返回某 revision 之后的事件。"""
        return [event for event in self._events if int(event.get("revision", -1)) > int(revision)]

    def latest(self) -> dict[str, Any] | None:
        """最近一个事件。"""
        return dict(self._events[-1]) if self._events else None


# --------------------------------------------------------------------------- 微操作


@dataclass(frozen=True)
class MicroOp:
    """一个可独立验证的最小写单元。"""

    op_id: str
    kind: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    gate: bool = False
    invariants: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """序列化为证据 JSON。"""
        return {
            "opId": self.op_id,
            "kind": self.kind,
            "payload": dict(self.payload),
            "gate": self.gate,
            "invariants": list(self.invariants),
        }


@dataclass
class MicroOpResult:
    """单个微操作的执行结果。"""

    op: MicroOp
    revision: int
    status: str
    result: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为证据 JSON。"""
        return {
            "op": self.op.to_dict(),
            "revision": self.revision,
            "status": self.status,
            "result": dict(self.result),
            "error": self.error,
        }


class MicroOpQueue:
    """单写执行器：串行落地微操作，每次写入后广播 revision。"""

    def __init__(self, stream: RevisionStream, executor: Callable[[MicroOp], Mapping[str, Any]]) -> None:
        self._stream = stream
        self._executor = executor
        self._history: list[MicroOpResult] = []

    @property
    def history(self) -> list[MicroOpResult]:
        """已执行的微操作（按时间顺序）。"""
        return list(self._history)

    def run(self, op: MicroOp) -> MicroOpResult:
        """执行一个微操作；失败不吞掉，转成 ``status="failed"`` 结果。"""
        try:
            payload = dict(self._executor(op) or {})
            status = str(payload.get("status") or "applied")
            revision = self._stream.publish({"kind": "micro_op", "op": op.to_dict(), "status": status, "result": payload})
            result = MicroOpResult(op=op, revision=revision, status=status, result=payload)
        except Exception as exc:  # noqa: BLE001
            revision = self._stream.publish({"kind": "micro_op_failed", "op": op.to_dict(), "error": str(exc)})
            result = MicroOpResult(op=op, revision=revision, status="failed", error=f"{type(exc).__name__}: {exc}")
        self._history.append(result)
        return result


# --------------------------------------------------------------------------- 评估


def evaluation_report(
    revision: int,
    checks: Iterable[Mapping[str, Any]],
    *,
    action: str | None = None,
) -> dict[str, Any]:
    """汇总评估检查项为 EvaluationReport。"""
    items = [dict(check) for check in checks]
    results = [str(item.get("result", "")).upper() for item in items]
    if any(result == "FAIL" for result in results):
        overall = "FAIL"
    elif any(result == "WARN" for result in results):
        overall = "WARN"
    else:
        overall = "PASS"
    return {
        "revision": int(revision),
        "checks": items,
        "overall": overall,
        "action": action or ("rollback_and_replan" if overall == "FAIL" else "continue"),
        "at": _utc_now(),
    }


def gate_decision(report: Mapping[str, Any], *, gate: bool) -> str:
    """根据评估结果与门禁标记决定下一步：``pass`` / ``rollback`` / ``warn``。"""
    overall = str(report.get("overall") or "").upper()
    if overall == "FAIL":
        return "rollback" if gate else "warn"
    if overall == "WARN":
        return "warn"
    return "pass"


# --------------------------------------------------------------------------- 任务卡与 Coordinator


REQUIRED_CARD_KEYS = ("task_id", "modeling", "evaluation")


def load_task_card(source: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    """加载任务卡：接受 dict、JSON 文件；有 PyYAML 时也接受 YAML。"""
    if isinstance(source, Mapping):
        return dict(source)
    path = Path(source)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".json"}:
        return json.loads(text)
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise ValueError("任务卡是 YAML 但环境没有 PyYAML；请改用 JSON 或安装 PyYAML") from exc
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ValueError("任务卡必须解析为对象")
    return loaded


def validate_task_card(card: Mapping[str, Any]) -> list[str]:
    """校验任务卡的最小字段与并行边界声明。"""
    errors: list[str] = []
    for key in REQUIRED_CARD_KEYS:
        if key not in card:
            errors.append(f"missing field: {key}")
    modeling = card.get("modeling") or {}
    if not isinstance(modeling, Mapping):
        errors.append("modeling: must be an object")
    elif not modeling.get("micro_ops"):
        errors.append("modeling.micro_ops: 至少需要一个微操作")
    evaluation = card.get("evaluation") or {}
    if not isinstance(evaluation, Mapping):
        errors.append("evaluation: must be an object")
    elif not evaluation.get("checks"):
        errors.append("evaluation.checks: 至少需要一个检查项")
    coordination = card.get("coordination") or {}
    if isinstance(coordination, Mapping) and int(coordination.get("max_parallel_writers", 1)) != 1:
        errors.append("coordination.max_parallel_writers: 写执行器只允许 1 个写者")
    return errors


def micro_ops_from_card(card: Mapping[str, Any]) -> list[MicroOp]:
    """把任务卡的 micro_ops 转成队列项；保持声明顺序。"""
    modeling = card.get("modeling") or {}
    gate_ops = set(modeling.get("gate_ops") or (card.get("coordination") or {}).get("gate_ops") or ())
    ops: list[MicroOp] = []
    for index, item in enumerate(modeling.get("micro_ops") or [], start=1):
        if isinstance(item, Mapping):
            payload = dict(item)
            kind = str(payload.pop("kind", next(iter(payload), "unknown")))
        elif isinstance(item, str):
            payload = {}
            kind = item
        else:
            continue
        ops.append(
            MicroOp(
                op_id=f"{card.get('task_id', 'task')}-op{index}",
                kind=kind,
                payload=payload,
                gate=kind in gate_ops,
                invariants=tuple(modeling.get("invariants") or ()),
            )
        )
    return ops


@dataclass
class CoordinatorResult:
    """一轮并行任务的结构化结果。"""

    task_id: str
    revision: int
    ops: list[dict[str, Any]] = field(default_factory=list)
    evaluations: list[dict[str, Any]] = field(default_factory=list)
    status: str = "completed"
    stopped_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为证据 JSON。"""
        return {
            "taskId": self.task_id,
            "revision": self.revision,
            "status": self.status,
            "stoppedAt": self.stopped_at,
            "ops": self.ops,
            "evaluations": self.evaluations,
        }


class Coordinator:
    """把任务卡跑成「写微操作 → 评估 → 门禁 → 继续/回退」的流水线。"""

    def __init__(
        self,
        stream: RevisionStream,
        queue: MicroOpQueue,
        evaluators: Iterable[Callable[[Mapping[str, Any]], Mapping[str, Any]]] = (),
        *,
        on_rollback: Callable[[MicroOp, Mapping[str, Any]], Mapping[str, Any]] | None = None,
    ) -> None:
        self.stream = stream
        self.queue = queue
        self.evaluators = list(evaluators)
        self.on_rollback = on_rollback

    def evaluate(self, revision: int) -> dict[str, Any]:
        """在当前 revision 上跑全部评估器并汇总。"""
        checks: list[dict[str, Any]] = []
        for evaluator in self.evaluators:
            try:
                outcome = evaluator({"revision": revision})
            except Exception as exc:  # noqa: BLE001
                outcome = {"id": getattr(evaluator, "__name__", "evaluator"), "result": "FAIL", "detail": str(exc)}
            if isinstance(outcome, Mapping):
                checks.append(dict(outcome))
        return evaluation_report(revision, checks)

    def run(self, card: Mapping[str, Any]) -> dict[str, Any]:
        """执行整张任务卡；gate 微操作评估 FAIL 时回退并停止。"""
        errors = validate_task_card(card)
        if errors:
            return {"status": "invalid", "errors": errors, "taskId": card.get("task_id")}
        result = CoordinatorResult(task_id=str(card.get("task_id")), revision=self.stream.revision)
        for op in micro_ops_from_card(card):
            op_result = self.queue.run(op)
            result.ops.append(op_result.to_dict())
            if op_result.status == "failed":
                result.status = "failed"
                result.stopped_at = op.op_id
                break
            report = self.evaluate(op_result.revision)
            result.evaluations.append(report)
            decision = gate_decision(report, gate=op.gate)
            if decision == "rollback":
                if self.on_rollback is not None:
                    rollback = dict(self.on_rollback(op, report) or {})
                    result.ops.append({"kind": "rollback", "op": op.to_dict(), "result": rollback})
                result.status = "rolled_back"
                result.stopped_at = op.op_id
                break
            if decision == "warn":
                result.status = "warn"
        result.revision = self.stream.revision
        try:
            bridge.append_journal(
                {
                    "entryId": f"coord-{int(time.time() * 1000)}",
                    "at": _utc_now(),
                    "event": "pipeline",
                    "taskId": result.task_id,
                    "status": result.status,
                    "revisions": [item.get("revision") for item in result.evaluations],
                }
            )
        except Exception:  # noqa: BLE001
            pass
        return result.to_dict()


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
